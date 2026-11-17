'use strict';
(() => {
  const el = id => document.getElementById(id);
  let busy = false;
  function duration(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
  }
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch('/api/flights', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw Error('Flight logs could not be loaded.');
      const {flights, recorder} = await response.json();
      el('logStatus').textContent = recorder.error ? 'Recording error' : recorder.recording ? 'Recording' : 'Recorder ready';
      el('logStatus').dataset.state = recorder.error ? 'error' : recorder.recording ? 'armed' : 'idle';
      el('logMessage').textContent = recorder.error ? 'Recorder error: ' + recorder.error
        : !recorder.available ? 'Flight recorder is unavailable.'
        : recorder.recording ? 'Recording the current flight. Downloads contain data saved so far.'
        : 'Logs stay on the Pi after restarts. Download CSV for spreadsheets or Setup JSON for flight settings.';
      el('logMessage').classList.toggle('error', !!recorder.error);
      el('emptyFlights').hidden = flights.length > 0;
      const fragment = document.createDocumentFragment();
      for (const flight of flights) {
        const row = document.createElement('tr');
        const values = [new Date(flight.started_at).toLocaleString(), flight.source === 'joystick' ? 'Joystick' : 'Keyboard / sliders',
          duration(flight.duration), flight.samples, flight.ended_at ? flight.end_reason : 'Recording'];
        for (const value of values) {
          const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
        }
        const downloads = document.createElement('td');
        const actions = document.createElement('div'); actions.className = 'flight-downloads';
        for (const [format, label] of [['csv', 'CSV'], ['json', 'Setup JSON']]) {
          const link = document.createElement('a');
          link.href = '/api/flights/' + encodeURIComponent(flight.id) + '.' + format;
          link.download = ''; link.className = 'secondary'; link.textContent = label;
          actions.append(link);
        }
        downloads.append(actions); row.append(downloads); fragment.append(row);
      }
      // Keep download links focused/clickable while a refresh is in progress.
      if (!el('flightRows').contains(document.activeElement)) el('flightRows').replaceChildren(fragment);
    } catch (error) {
      el('logStatus').textContent = 'Disconnected'; el('logStatus').dataset.state = 'error';
      el('logMessage').textContent = error.message; el('logMessage').classList.add('error');
    } finally { busy = false; }
  }
  el('refreshFlights').onclick = refresh;
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  refresh(); setInterval(refresh, 5000);
})();
