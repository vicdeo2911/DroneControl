'use strict';
(() => {
  const el = id => document.getElementById(id);
  let busy = false, signature = '';
  async function request(path, data) {
    const response = await fetch('/api/recordings' + path, {
      method: data === undefined ? 'GET' : 'POST', headers: data === undefined ? {} : {'Content-Type': 'application/json'},
      body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(20000)
    });
    const result = await response.json(); if (!response.ok) throw Error(result.error || 'Camera request failed.'); return result;
  }
  function selectedClip() {
    const id = el('recordingSelect').value;
    el('downloadRecording').hidden = !id;
    const base = el('recordingPanel').dataset.downloadBase || '';
    el('downloadRecording').href = base + '/api/recordings/' + id + '.avi';
    if (el('extractFrames')) el('extractFrames').disabled = !id || el('extractFrames').dataset.busy === 'true';
  }
  el('recordingSelect').onchange = selectedClip;
  async function update() {
    const data = await request(''), current = data.current;
    el('startRecording').disabled = busy || current.active;
    el('stopRecording').disabled = busy || !current.active;
    el('recordSeconds').disabled = current.active;
    for (const id of ['recordResolution', 'recordFps']) if (el(id)) el(id).disabled = busy || current.active;
    el('recordingStatus').textContent = current.active ? `Recording on Pi · ${current.elapsed_seconds.toFixed(1)} s · ${current.frames} frames · ${current.actual_fps} fps · ${current.dropped} dropped` :
      current.state === 'idle' ? 'Ready to record clean video on the Pi.' : `${current.state} · ${current.frames} frames · ${current.actual_fps || 0} fps${current.error ? ' · ' + current.error : ''}${current.stop_reason ? ' · ' + current.stop_reason : ''}`;
    const clips = data.recordings.filter(c => c.state === 'completed' && c.frames > 0);
    const nextSignature = JSON.stringify(clips.map(c => [c.id, c.frames, c.bytes]));
    if (signature !== nextSignature) {
      signature = nextSignature; const previous = el('recordingSelect').value; el('recordingSelect').replaceChildren();
      for (const clip of clips) { const option = document.createElement('option'); option.value = clip.id;
        option.textContent = `${clip.started_utc.slice(0,19).replace('T',' ')} UTC · ${clip.width}×${clip.height} · ${clip.actual_fps || clip.fps} fps · ${clip.elapsed_seconds}s · ${clip.frames} frames · ${(clip.bytes/1024/1024).toFixed(1)} MB`; el('recordingSelect').append(option); }
      if (!clips.length) { const option = document.createElement('option'); option.value = ''; option.textContent = 'No completed clips yet'; el('recordingSelect').append(option); }
      else if (clips.some(c => c.id === previous)) el('recordingSelect').value = previous;
      selectedClip();
    }
  }
  async function action(name, data) {
    busy = true; el('startRecording').disabled = true; el('stopRecording').disabled = true;
    try { await request('/' + name, data); el('recordingMessage').textContent = name === 'start' ? 'Recording continues on the Pi if this tab closes. It stops at the selected duration.' : 'Clip saved. You can now extract frames.'; }
    catch (error) { el('recordingMessage').textContent = error.message; }
    finally { busy = false; try { await update(); } catch (error) { el('recordingStatus').textContent = error.message; } }
  }
  el('startRecording').onclick = () => action('start', {seconds: Number(el('recordSeconds').value),
    ...(el('recordResolution') ? {resolution: el('recordResolution').value, fps: Number(el('recordFps').value)} : {})});
  el('stopRecording').onclick = () => action('stop', {});
  async function poll() { try { await update(); } catch (error) { el('recordingStatus').textContent = error.message; } setTimeout(poll, 1500); }
  poll();
})();
