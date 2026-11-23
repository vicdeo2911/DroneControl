const section = document.getElementById('launcher');
const form = document.getElementById('launcher-position-form');
const pulse = document.getElementById('launcher-pulse');
const status = document.getElementById('launcher-status');
const buttons = [...section.querySelectorAll('button')];
let saved = null;
let sending = false;

function render() {
  buttons.forEach(button => { button.disabled = sending || !saved; });
  section.querySelectorAll('[data-servo-save]').forEach(button => {
    button.disabled = sending || !saved || saved.last_pulse_us === null;
  });
  section.querySelectorAll('[data-servo-position]').forEach(button => {
    button.disabled = sending || !saved || saved[button.dataset.servoPosition + '_us'] === null;
  });
  pulse.disabled = sending;
  document.getElementById('launcher-current').textContent = saved?.last_pulse_us == null
    ? 'Apply a position before saving it.'
    : `Last applied position is ${saved.last_pulse_us} microseconds.`;
  document.getElementById('launcher-saved').textContent =
    `Lock ${saved?.lock_us ?? 'unsaved'} / Release ${saved?.release_us ?? 'unsaved'} microseconds`;
}

async function load() {
  const response = await fetch('/api/launcher');
  const result = await response.json();
  if (!response.ok) throw Error(result.error);
  saved = result;
  render();
}

async function command(data, save = false, stop = false) {
  if (sending || !saved) return;
  if (!save && !stop && !confirm('Move the servo on GPIO12? Confirm the launcher is unloaded and propulsion is disconnected.')) return;
  sending = true;
  render();
  status.textContent = save ? 'Saving position...' : 'Moving servo...';
  try {
    const response = await fetch(stop ? '/api/launcher/stop' : save ? '/api/launcher/save' : '/api/launcher/jog', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({version: 2, ...data})
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    await load();
    status.textContent = stop ? 'Servo signal stopped.' : save
      ? `Saved ${data.position} position on the Pi.`
      : data.direction
        ? 'Direction test completed, including return to 1500 microseconds.'
        : `Holding position at ${result.pulse_us} microseconds. Hardware PWM remains active.`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    sending = false;
    render();
  }
}

form.addEventListener('submit', event => {
  event.preventDefault();
  if (form.reportValidity()) command({pulse_us: pulse.valueAsNumber, unloaded: true});
});
document.getElementById('launcher-stop').addEventListener('click', () => command({}, false, true));
section.querySelectorAll('[data-servo-save]').forEach(button => {
  button.addEventListener('click', () => command({position: button.dataset.servoSave, pulse_us: saved.last_pulse_us}, true));
});
section.querySelectorAll('[data-servo-position]').forEach(button => {
  button.addEventListener('click', () => command({position: button.dataset.servoPosition, unloaded: true}));
});
section.querySelectorAll('[data-servo-direction]').forEach(button => {
  button.addEventListener('click', () => command({direction: button.dataset.servoDirection, unloaded: true}));
});
load().catch(error => { status.textContent = error.message; });
