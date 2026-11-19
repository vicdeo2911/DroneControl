'use strict';
const $ = id => document.getElementById(id);
const calibrationPage = document.body.dataset.page === 'calibration';
const joystickPage = document.body.dataset.page === 'joystick';
let joystickEngaged = false;
let token = null, seq = 0, generation = 0, connected = false, state = null;
let starting = false, saving = false, busy = false, inflight = null, limitsDirty = false;
let throttle = 0, roll = 0, pending = {}, chain = Promise.resolve();
let offPending = false;
let sensitivityDirty = false;
const keys = new Set();
let guided = false, steeringConfig = null, steeringObservation = null;
let steeringVelocity = 0, steeringConfirmed = 0, steeringFrame = null;
let steeringCenter = null, steeringGeometry = null;
let lastGuidedTime = null;

function manualSteering() {
  if (!guided) return;
  guided = false;
  lastGuidedTime = null;
  roll = 0;
}

function trackingReady(now = performance.now()) {
  return steeringConfig && steeringObservation
    && steeringConfirmed >= steeringConfig.confirm_frames
    && now - steeringObservation.time <= steeringConfig.frame_timeout * 1000;
}

function loseSteeringTrack() {
  steeringObservation = null;
  steeringConfirmed = 0;
  if (guided) {
    manualSteering();
    message('Tracking lost. Manual control resumed.', true);
  }
}

function guidedCommand() {
  const now = performance.now();
  if (!trackingReady(now)) {
    loseSteeringTrack();
    return;
  }
  const c = steeringConfig;
  const error = Number($('steeringX').value) / 100 - steeringObservation.x;
  const adjusted = Math.sign(error) * Math.max(0, Math.abs(error) - c.deadband);
  const limit = Math.min(c.max_roll, state.limits.roll);
  const desired = Math.max(-limit, Math.min(limit,
    c.roll_direction * (c.kp * adjusted - c.kd * steeringVelocity)));
  const dt = Math.min(.2, (now - (lastGuidedTime ?? now)) / 1000);
  lastGuidedTime = now;
  roll += Math.max(-c.roll_slew * dt, Math.min(c.roll_slew * dt, desired - roll));
  const cruise = Number($('steeringCruise').value);
  throttle += Math.max(-c.throttle_slew * dt, Math.min(c.throttle_slew * dt, cruise - throttle));
}

function updateSteeringObservation(camera, began) {
  const [width, height] = camera.frame_size ?? [];
  const now = performance.now();
  const age = camera.frame_age_seconds;
  const geometry = JSON.stringify([width, height, camera.sensor_mode?.size, camera.settings?.zoom]);
  if (!camera.ready || camera.error || !(width > 0 && height > 0) || !Number.isInteger(camera.frames)
      || !Number.isFinite(age) || age < 0 || age + (now - began) / 1000 > steeringConfig.frame_timeout) {
    throw Error('Camera unavailable or stale');
  }
  if (steeringGeometry !== geometry || (steeringFrame !== null && camera.frames < steeringFrame)) {
    steeringFrame = null;
    steeringConfirmed = 0;
    steeringObservation = null;
    if (guided) {
      manualSteering();
      message('Camera geometry or stream changed. Manual control resumed.', true);
    }
  }
  steeringGeometry = geometry;
  if (camera.frames === steeringFrame) return;
  steeringFrame = camera.frames;
  const points = (camera.detections ?? []).filter(d => d.label === 'plane'
    && d.confidence >= steeringConfig.confidence && Array.isArray(d.box) && d.box.length === 4
    && d.box.every(Number.isFinite)).map(d => d.box).filter(([x1, y1, x2, y2]) =>
    0 <= x1 && x1 < x2 && x2 <= width && 0 <= y1 && y1 < y2 && y2 <= height);
  if (points.length !== 1) {
    loseSteeringTrack();
    return;
  }
  const [x1, y1, x2, y2] = points[0];
  const center = [(x1 + x2) / (2 * width), (y1 + y2) / (2 * height)];
  const time = began - age * 1000;
  const previous = steeringObservation;
  if (previous && Math.hypot(center[0] - steeringCenter[0], center[1] - steeringCenter[1]) > steeringConfig.association_gate) {
    loseSteeringTrack();
    return;
  }
  steeringCenter = center;
  const dt = previous ? (time - previous.time) / 1000 : 0;
  const continuous = dt > 0 && dt <= steeringConfig.frame_timeout;
  const x = continuous ? previous.x + steeringConfig.filter_alpha * (center[0] - previous.x) : center[0];
  steeringVelocity = continuous ? (x - previous.x) / dt : 0;
  steeringObservation = {x, time};
  steeringConfirmed = continuous ? steeringConfirmed + 1 : 1;
}

const offDialog = document.createElement('dialog');
offDialog.id = 'offDialog';
offDialog.setAttribute('aria-labelledby', 'offTitle');
offDialog.innerHTML = '<form id="offForm"><p class="eyebrow">POWER-OFF SEQUENCE</p><h2 id="offTitle">Hold the drone.</h2><p class="off-warning">Setting 0 V can command full throttle while the remote is powered.</p><ol><li>Hold the drone securely.</li><li>Set both outputs to 0 V.</li><li>Switch off the physical remote.</li></ol><label class="off-check"><input id="offHeld" type="checkbox" required> I am holding the drone securely.</label><div class="actions"><button id="offCancel" type="button" class="secondary">Cancel</button><button id="offConfirm" type="submit" class="stop">Set both outputs to 0 V</button></div></form>';
document.body.append(offDialog);

function message(text, error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}
async function request(path, data) {
  const response = await fetch('/api/' + path, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({version: 2, ...data}), signal: AbortSignal.timeout(900)
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Request failed');
  return result;
}
function api(path, data = {}) {
  // Keep heartbeats, calibration saves, and their sequence numbers in order.
  const operation = chain.then(() => request(path, data));
  chain = operation.catch(() => {});
  return operation;
}
function active() {
  return !!token && !starting && !offPending && !offDialog.open && !state?.initializing && connected && !calibrationPage;
}
function renderPositions() {
  if (calibrationPage) return;
  $('throttle').value = throttle;
  $('roll').value = roll;
  $('throttleRead').textContent = Math.round(throttle);
  $('rollRead').textContent = roll === 0 ? 'CENTER' : Math.round(Math.abs(roll)) + '% ' + (roll < 0 ? 'LEFT' : 'RIGHT');
  $('throttleBar').style.width = throttle + '%';
  $('aircraft').style.transform = 'rotate(' + (roll * .45) + 'deg)';
  const p = state?.calibration || {};
  const t = state?.outputs_zero ? state.throttle_volts : p.throttle_idle + throttle / 100 * (p.throttle_full - p.throttle_idle);
  const end = roll >= 0 ? p.roll_right : p.roll_left;
  const r = state?.outputs_zero ? state.roll_volts : p.roll_center + Math.abs(roll) / 100 * (end - p.roll_center);
  $('throttleVolt').textContent = Number.isFinite(t) ? t.toFixed(3) + ' V' : '—';
  $('rollVolt').textContent = Number.isFinite(r) ? r.toFixed(3) + ' V' : '—';
  for (const button of document.querySelectorAll('[data-key]')) button.classList.toggle('active', keys.has(button.dataset.key));
}
function render() {
  if ($('steeringStart')) {
    const tracking = trackingReady();
    $('steeringStart').disabled = !active() || !tracking || guided;
    $('steeringManual').disabled = !guided;
    $('steeringCruise').disabled = guided;
    if (state?.limits) $('steeringCruise').max = state.limits.throttle;
    $('steeringStatus').textContent = (guided ? 'Camera-guided steering' : 'Manual')
      + (tracking ? ' · YOLO tracking' : ' · Waiting for a clear YOLO detection');
  }
  const occupied = !!state?.enabled && !token;
  const ready = connected && !!state && !state.error && !offPending;
  if ($('recordingStatus')) {
    const log = state?.flight_log;
    $('recordingStatus').textContent = !connected ? 'Connecting'
      : log?.error ? 'Recording error: ' + log.error
      : log?.recording ? 'Recording · ' + Math.floor(log.elapsed_seconds) + ' s · ' + log.samples + ' samples'
      : log?.available ? 'Ready · records when armed' : 'Recording unavailable';
    $('recordingStatus').classList.toggle('error', !!log?.error);
    $('latestFlight').hidden = !log?.latest_id;
    if (log?.latest_id) $('latestFlight').href = '/api/flights/' + log.latest_id + '.csv';
  }
  $('status').textContent = offPending ? 'Setting 0 V…' : !connected ? 'Disconnected' : state?.error ? 'DAC error' : state.outputs_zero ? '0 V outputs' : starting || state?.initializing ? 'Arming…' : token ? (calibrationPage ? 'Calibrating' : 'Armed') : occupied ? 'In use' : 'Standby';
  $('status').dataset.state = !ready ? 'error' : token ? 'armed' : 'idle';
  $('offRemote').disabled = !connected || !state || offPending || starting || saving || (state.outputs_zero && !state.error);
  if (calibrationPage) {
    const p = state?.calibration || {};
    const editing = ready && !!token && !saving && !starting;
    $('setup').disabled = !ready || !!token || occupied || starting;
    $('finishSetup').disabled = !editing;
    for (const id of ['rawThrottle', 'rawThrottleNumber']) $(id).disabled = !editing;
    for (const id of ['rawRoll', 'rawRollNumber']) $(id).disabled = !editing || p.throttle_idle === undefined;
    for (const button of document.querySelectorAll('[data-point]')) {
      button.disabled = !editing || (button.dataset.point !== 'throttle_idle' && p.throttle_idle === undefined);
      const value = p[button.dataset.point];
      $('saved_' + button.dataset.point).textContent = value === undefined ? 'Not saved' : value.toFixed(3) + ' V';
    }
    $('progress').textContent = Object.keys(p).length + ' / 5 positions saved';
    $('calibrationReady').textContent = state?.calibration_complete ? 'Calibration complete ✓' : 'Calibration required';
  } else {
    $('arm').disabled = !ready || !state.calibration_complete || !!token || occupied || starting;
    if (joystickPage) {
      window.planeJoystick.setLocked(!!token || starting || occupied);
      window.planeJoystick.setLimits(state?.limits || {throttle: 50, roll: 50});
      $('arm').disabled ||= !!window.planeJoystick.armError();
    }
    $('arm').textContent = starting || state?.initializing ? 'Arming…' : token ? 'Aircraft armed ✓' : 'Arm aircraft ↗';
    for (const id of ['throttle', 'roll', 'centerRoll']) $(id).disabled = joystickPage || !active();
    for (const button of document.querySelectorAll('[data-key]')) button.disabled = !active();
    const canSetLimits = ready && state.calibration_complete && !token && !occupied && !starting && !saving;
    for (const id of ['maxThrottle', 'maxRoll', 'saveLimits']) $(id).disabled = !canSetLimits;
    const limits = state?.limits || {throttle: 50, roll: 50};
    $('throttle').max = limits.throttle;
    $('roll').min = -limits.roll;
    $('roll').max = limits.roll;
    $('throttleCap').textContent = 'MAX ' + limits.throttle + '%';
    $('leftCap').textContent = 'LEFT ' + limits.roll + '%';
    $('rightCap').textContent = 'RIGHT ' + limits.roll + '%';
    if (!limitsDirty) {
      $('maxThrottle').value = limits.throttle;
      $('maxRoll').value = limits.roll;
    }
    $('maxThrottleRead').textContent = $('maxThrottle').value + '%';
    $('maxRollRead').textContent = $('maxRoll').value + '%';
    const sensitivity = {rise: 30, fall: 30, decay: 10, ...state?.sensitivity};
    for (const id of ['throttleRise', 'throttleFall', 'throttleDecay', 'saveSensitivity']) {
      $(id).disabled = !ready || !!token || occupied || starting || saving;
    }
    for (const [id, direction] of [['throttleRise', 'rise'], ['throttleFall', 'fall'], ['throttleDecay', 'decay']]) {
      if (!sensitivityDirty) $(id).value = sensitivity[direction];
      $(id + 'Read').textContent = id === 'throttleDecay' && Number($(id).value) === 0 ? 'Off' : $(id).value + ' pp/s';
    }
    renderPositions();
  }
}
function accept(result) {
  state = result;
  connected = true;
  if (token && !result.enabled) resetLocal();
  render();
}
function syncRaw(result) {
  if (!calibrationPage) return;
  for (const [id, volts] of [['rawThrottle', result.throttle_volts], ['rawRoll', result.roll_volts]]) {
    $(id).value = volts ?? 0;
    $(id + 'Number').value = (volts ?? 0).toFixed(3);
  }
  pending = {};
}
function resetLocal() {
  manualSteering();
  joystickEngaged = false;
  token = null; starting = false; pending = {}; throttle = 0; roll = 0;
  keys.clear(); generation++;
}
async function stop(text = 'Stopped. Idle and center restored.', stopReason = 'manual') {
  resetLocal(); render();
  const g = generation;
  try {
    // Stop must not wait behind a slow heartbeat.
    const result = await request('stop', {reason: stopReason});
    if (g !== generation) return;
    accept(result); syncRaw(result); message(result.outputs_zero ? result.reason : text);
  } catch (error) {
    if (g !== generation) return;
    connected = false; render(); message('Stop unconfirmed. Use the physical power switch.', true);
  }
}
async function begin() {
  if (joystickPage && window.planeJoystick.armError()) { message(window.planeJoystick.armError(), true); return; }
  if (state?.outputs_zero && !window.confirm('Hold the drone securely and switch the physical remote ON, then continue. This restores saved idle and center voltages.')) return;
  const g = ++generation;
  starting = true; throttle = roll = 0; keys.clear(); render();
  try {
    let result = await api('enable', {mode: calibrationPage ? 'calibration' : 'normal',
      source: joystickPage ? 'joystick' : 'keyboard', restore_outputs: !!state?.outputs_zero});
    if (g !== generation) return;
    token = result.token; seq = 0; accept(result); syncRaw(result);
    if (!calibrationPage) {
      message('Arming · idle → full → idle');
      result = await api('initialize', {token});
      if (g !== generation) return;
      accept(result);
    } else message('Adjust a position, then save it.');
  } catch (error) {
    if (g === generation) await stop(error.message, 'client_error');
  } finally {
    if (g === generation) { starting = false; render(); }
  }
}
async function tick() {
  if (busy || saving || offPending || document.hidden || starting) return;
  busy = true;
  const g = generation;
  try {
    if (token) {
      if (joystickPage && active() && !updateJoystick()) return;
      if (guided) guidedCommand();
      const sent = {...pending};
      const result = await api('control', {token, sequence: seq++,
        throttle: calibrationPage ? (sent.throttle ?? null) : throttle,
        roll: calibrationPage ? (sent.roll ?? null) : roll});
      if (g !== generation) return;
      const wasInitializing = state?.initializing;
      accept(result);
      for (const [key, value] of Object.entries(sent)) if (pending[key] === value) delete pending[key];
      if (wasInitializing && !result.initializing) message(joystickPage ? 'Armed. Joystick controls throttle and roll.' : 'Armed. Use WASD or the sliders.');
      if (!calibrationPage && result.initializing) {
        $('throttleVolt').textContent = result.throttle_volts.toFixed(3) + ' V';
      }
    } else {
      const response = await fetch('/api/status', {cache: 'no-store', signal: AbortSignal.timeout(900)});
      if (!response.ok) throw Error('Connection unavailable');
      const result = await response.json();
      if (g !== generation) return;
      const wasConnected = connected;
      accept(result);
      if (!wasConnected || result.outputs_zero) message(result.error || (result.outputs_zero ? result.reason : result.enabled ? 'Controls are open in another tab.' : result.calibration_complete ? 'Ready when you are.' : 'Open Calibration to set up your aircraft.'), !!result.error);
    }
  } catch (error) {
    if (g === generation) {
      const hadToken = !!token;
      resetLocal(); connected = false; render(); message(error.message, true);
      if (hadToken) request('stop', {reason: 'client_error'}).catch(() => {});
    }
  } finally { busy = false; }
}
$('stop').onclick = () => stop();
$('offRemote').onclick = () => {
  keys.clear();
  $('offHeld').checked = false;
  offDialog.showModal(); render();
};
$('offCancel').onclick = () => offDialog.close();
offDialog.addEventListener('close', render);
$('offForm').onsubmit = async event => {
  event.preventDefault();
  if (!$('offHeld').checked || offPending) return;
  offDialog.close();
  offPending = true; resetLocal(); render();
  const g = generation;
  try {
    const result = await request('off', {confirmed: true});
    if (g !== generation) return;
    accept(result); syncRaw(result); message(result.reason);
  } catch (error) {
    if (g === generation) { connected = false; message('0 V could not be confirmed. Keep holding the drone. ' + error.message, true); }
  } finally { offPending = false; render(); }
};
if (calibrationPage) {
  $('setup').onclick = begin;
  $('finishSetup').onclick = async () => {
    await stop(state?.calibration_complete ? 'Calibration saved. Return to the flight deck.' : 'Saved positions kept. Complete the remaining positions before flight.');
  };
  for (const [id, channel] of [['rawThrottle', 'throttle'], ['rawRoll', 'roll']]) {
    for (const input of [$(id), $(id + 'Number')]) input.addEventListener('input', event => {
      if (!token || saving) return;
      if (event.target.value === '') return;
      const value = Number(event.target.value);
      if (!Number.isFinite(value) || value < 0 || value > 3.3) return;
      $(id).value = value; $(id + 'Number').value = value;
      pending[channel] = value;
    });
  }
  for (const button of document.querySelectorAll('[data-point]')) button.onclick = async () => {
    if (!token || saving) return;
    const g = generation;
    const point = button.dataset.point;
    const volts = Number($(point.startsWith('throttle_') ? 'rawThrottle' : 'rawRoll').value);
    saving = true; render();
    try {
      if (inflight) await inflight;
      if (g !== generation) return;
      const result = await api('save-point', {token, sequence: seq++, point, volts});
      if (g !== generation) return;
      accept(result); syncRaw(result); message('Position saved. Idle and center restored.');
    } catch (error) { if (g === generation) message(error.message, true); }
    finally { saving = false; render(); }
  };
} else {
  $('arm').onclick = begin;
  for (const id of ['throttle', 'roll']) $(id).addEventListener('input', event => {
    if (!active()) return;
    manualSteering();
    if (id === 'throttle') throttle = Number(event.target.value);
    else roll = Number(event.target.value);
    renderPositions();
  });
  $('centerRoll').onclick = () => { manualSteering(); roll = 0; keys.delete('KeyA'); keys.delete('KeyD'); renderPositions(); };
  for (const id of ['maxThrottle', 'maxRoll']) $(id).oninput = () => { limitsDirty = true; render(); };
  $('saveLimits').onclick = async () => {
    const g = generation;
    saving = true; render();
    try {
      const result = await api('limits', {throttle: Number($('maxThrottle').value), roll: Number($('maxRoll').value)});
      if (g !== generation) return;
      limitsDirty = false; accept(result); message('Limits saved.');
    } catch (error) { if (g === generation) message(error.message, true); }
    finally { saving = false; render(); }
  };
  function steer() {
    roll = ((keys.has('KeyD') ? 1 : 0) - (keys.has('KeyA') ? 1 : 0)) * (state?.limits.roll || 50);
    renderPositions();
  }
  for (const id of ['throttleRise', 'throttleFall', 'throttleDecay']) $(id).oninput = () => { sensitivityDirty = true; render(); };
  $('saveSensitivity').onclick = async () => {
    const g = generation;
    saving = true; render();
    try {
      const result = await api('sensitivity', {rise: Number($('throttleRise').value), fall: Number($('throttleFall').value), decay: Number($('throttleDecay').value)});
      if (g !== generation) return;
      sensitivityDirty = false; accept(result); message('Throttle speeds saved.');
    } catch (error) { if (g === generation) message(error.message, true); }
    finally { saving = false; render(); }
  };
  function keyDown(code) { if (active()) { manualSteering(); keys.add(code); steer(); } }
  function keyUp(code) { const held = keys.delete(code); if (held) steer(); }
  document.addEventListener('keydown', event => {
    if (joystickPage) return;
    if (!['KeyW', 'KeyA', 'KeyS', 'KeyD'].includes(event.code) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.matches('input:not([type=range]), textarea, select, [contenteditable=true]')) return;
    if (!active()) return;
    event.preventDefault(); keyDown(event.code);
  });
  document.addEventListener('keyup', event => keyUp(event.code));
  for (const button of document.querySelectorAll('[data-key]')) {
    button.style.touchAction = 'none';
    button.onpointerdown = event => {
      if (!active()) return;
      event.preventDefault(); button.setPointerCapture(event.pointerId); keyDown(button.dataset.key);
    };
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) button.addEventListener(event, () => keyUp(button.dataset.key));
  }
  let previousFrame = performance.now();
  function frame(now) {
    const seconds = Math.min((now - previousFrame) / 1000, .1);
    previousFrame = now;
    if (active() && !guided) {
      if (joystickPage) {
        updateJoystick();
        requestAnimationFrame(frame);
        return;
      }
      // S lowers throttle; releasing both throttle buttons gradually returns to idle.
      const speed = keys.has('KeyS') ? -(state.sensitivity?.fall ?? 30)
        : keys.has('KeyW') ? (state.sensitivity?.rise ?? 30)
        : -(state.sensitivity?.decay ?? 10);
      if (speed) {
        throttle = Math.max(0, Math.min(state.limits.throttle, throttle + speed * seconds));
        renderPositions();
      }
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}
function updateJoystick() {
  const sample = window.planeJoystick.sample();
  const error = !sample ? 'Joystick disconnected or mapping unavailable. Controls stopped.'
    : !joystickEngaged ? window.planeJoystick.armError() : '';
  if (error) { stop(error, !sample ? 'joystick_disconnected' : 'joystick_not_ready'); return false; }
  joystickEngaged = true;
  throttle = sample.throttle * state.limits.throttle;
  roll = sample.roll * state.limits.roll;
  renderPositions();
  return true;
}
document.addEventListener('keydown', event => {
  if (offDialog.open) return;
  if (event.code === 'Space' || event.code === 'Escape') { event.preventDefault(); stop(); }
});
function leave() {
  // An inactive tab must not stop another tab's control session.
  if (!token && !starting) return;
  resetLocal(); render(); message('Stopped. Return to the controls to arm again.');
  navigator.sendBeacon('/api/stop', new Blob([JSON.stringify({reason: 'page_left'})], {type: 'application/json'}));
}
document.addEventListener('visibilitychange', () => { if (document.hidden) leave(); });
window.addEventListener('pagehide', leave);
window.addEventListener('blur', leave);
window.addEventListener('plane-joystick-lost', () => {
  if (joystickPage && (token || starting)) stop('Joystick disconnected. Controls stopped.', 'joystick_disconnected');
});
if ($('steeringPreview')) {
  const preview = new LatestCameraPreview($('steeringPreview'), '/camera/preview.jpg');
  function setReference(value) {
    $('steeringX').value = Math.max(5, Math.min(95, value));
    $('steeringCrosshair').style.left = $('steeringX').value + '%';
    $('steeringCrosshair').hidden = false;
  }
  setReference(50);
  $('steeringPreview').onclick = event => {
    const rect = $('steeringPreview').getBoundingClientRect();
    if (rect.width) setReference(100 * (event.clientX - rect.left) / rect.width);
  };
  $('steeringX').oninput = () => {
    if ($('steeringX').value !== '' && $('steeringX').checkValidity()) {
      setReference(Number($('steeringX').value));
    } else if (guided) {
      manualSteering();
      render();
    }
  };
  $('steeringStart').onclick = () => {
    if (!active() || !trackingReady() || !$('steeringCruise').reportValidity()
        || !$('steeringX').reportValidity() || $('steeringX').value === '') return;
    keys.clear();
    guided = true;
    lastGuidedTime = performance.now();
    render();
  };
  $('steeringManual').onclick = () => { manualSteering(); render(); };
  async function pollSteering() {
    try {
      if (!steeringConfig) {
        const response = await fetch('/api/autonomous', {cache: 'no-store', signal: AbortSignal.timeout(500)});
        if (!response.ok) throw Error('Steering settings unavailable');
        steeringConfig = (await response.json()).config;
      }
      const began = performance.now();
      const response = await fetch('/api/autonomous/camera', {cache: 'no-store', signal: AbortSignal.timeout(350)});
      if (!response.ok) throw Error('Camera unavailable');
      const camera = await response.json();
      preview.setRate(camera);
      updateSteeringObservation(camera, began);
    } catch {
      loseSteeringTrack();
    }
    setTimeout(pollSteering, 100);
  }
  pollSteering();
  window.addEventListener('pagehide', () => preview.stop());
}
render(); inflight = tick();
setInterval(() => { if (!busy && !saving) inflight = tick(); }, 200);
