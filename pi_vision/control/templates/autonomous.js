'use strict';
const $ = id => document.getElementById(id);
let owner = null, pending = false, populated = false;
let autonomousActive = false;
let sessionRevision = 0;
let targetDrag = null, liveTargetBox = null, savedSetpoint = null;
let noiseBoxes = [];
let lastStationaryTimeout = 0;
const stationaryTimeoutKey = 'pi-vision-stationary-timeout';
const targetKeys = ['setpoint_x', 'setpoint_y'];
const throttleTrackingKeys = new Set([
  'setpoint_y', 'vertical_kp', 'vertical_kd', 'vertical_deadband', 'vertical_filter_alpha',
  'vertical_direction', 'max_vertical_throttle', 'turn_throttle_boost'
]);

function renderLiveTarget() {
  const image = $('cameraPreview'), overlay = $('liveTargetBox');
  image.classList.toggle('pick-live-target', populated && !autonomousActive && !pending);
  const bounds = previewImageBounds();
  $('saveLiveTarget').disabled = !liveTargetBox || !!targetDrag
    || autonomousActive || pending;
  overlay.hidden = !liveTargetBox || !bounds;
  if (overlay.hidden) return;
  const {left, top, width, height} = liveTargetBox;
  overlay.style.left = (bounds.left + left * bounds.width) + 'px';
  overlay.style.top = (bounds.top + top * bounds.height) + 'px';
  overlay.style.width = width * bounds.width + 'px';
  overlay.style.height = height * bounds.height + 'px';
}

function imagePoint(event) {
  const bounds = previewImageBounds();
  if (!bounds?.width || !bounds.height) return null;
  return {
    x: (event.clientX - bounds.box.left - bounds.left) / bounds.width,
    y: (event.clientY - bounds.box.top - bounds.top) / bounds.height
  };
}

const cameraPreview = $('cameraPreview');
cameraPreview.addEventListener('pointerdown', event => {
  if (!cameraPreview.classList.contains('pick-live-target') || event.button !== 0 || targetDrag) return;
  const point = imagePoint(event);
  if (!point || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1) return;
  event.preventDefault();
  targetDrag = {id: event.pointerId, point, previous: liveTargetBox};
  cameraPreview.setPointerCapture(event.pointerId);
});
cameraPreview.addEventListener('pointermove', event => {
  if (targetDrag?.id !== event.pointerId) return;
  const point = imagePoint(event);
  if (!point) return;
  const x = Math.max(0, Math.min(1, point.x));
  const y = Math.max(0, Math.min(1, point.y));
  const start = targetDrag.point;
  liveTargetBox = {left: Math.min(x, start.x), top: Math.min(y, start.y),
    width: Math.abs(x - start.x), height: Math.abs(y - start.y)};
  renderLiveTarget();
});
function finishTargetDrag(event) {
  if (targetDrag?.id !== event.pointerId) return;
  if (event.type !== 'pointerup' || !cameraPreview.classList.contains('pick-live-target')
      || !liveTargetBox || liveTargetBox === targetDrag.previous
      || liveTargetBox.width < .01 || liveTargetBox.height < .01) {
    liveTargetBox = targetDrag.previous;
  } else {
    const centers = [liveTargetBox.left + liveTargetBox.width / 2, liveTargetBox.top + liveTargetBox.height / 2];
    targetKeys.forEach((key, index) => {
      const input = $(key);
      input.value = Math.max(Number(input.min), Math.min(Number(input.max), centers[index])).toFixed(3);
    });
    $('liveTargetStatus').textContent = `X ${$('setpoint_x').value}, Y ${$('setpoint_y').value}. Save target before launch.`;
  }
  targetDrag = null;
  renderLiveTarget();
}
for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) {
  cameraPreview.addEventListener(event, finishTargetDrag);
}

function previewImageBounds() {
  const image = $('cameraPreview');
  const box = image.getBoundingClientRect();
  if (!image.naturalWidth || !image.naturalHeight) return null;
  const scale = Math.min(box.width / image.naturalWidth, box.height / image.naturalHeight);
  const width = image.naturalWidth * scale, height = image.naturalHeight * scale;
  return {box, width, height, left: (box.width - width) / 2, top: (box.height - height) / 2};
}

function renderNoise() {
  LatestCameraPreview.showNoise($('noiseOverlay'), cameraPreview, autonomousActive ? noiseBoxes : []);
}

cameraPreview.addEventListener('load', renderLiveTarget);
window.addEventListener('resize', renderLiveTarget);
window.addEventListener('resize', renderNoise);
const labels = {
  feedback_hz: ['Feedback rate (Hz)', '2–60 updates/s; fresh observations are limited by detection FPS.'],
  launch_delay: ['Launch countdown (s)', 'Idle until the launch cue.'],
  launcher_release_delay: ['Launcher release delay (s)', 'Time to hold launch throttle before releasing the launcher.'],
  launch_seconds: ['Launch phase (s)', 'Timed launch throttle before acquisition.'],
  post_release_throttle_hold: ['Post-release throttle hold (s)', 'Keep launch throttle for this long after release, even if tracking starts or drops out. Set to 0 to disable.'],
  acquire_timeout: ['Acquisition timeout (s)', 'Wait limit before launch in view / after launch.'],
  max_flight_seconds: ['Session limit (s)', 'Includes arming and countdown.'],
  launch_throttle: ['Launch throttle (%)', 'Absolute calibrated percentage.'],
  cruise_throttle: ['Cruise throttle (%)', 'Base throttle while tracking.'],
  lost_throttle: ['Lost throttle (%)', 'Fallback during brief detection loss once the post-release throttle hold ends.'],
  max_roll: ['Maximum roll (%)', 'Absolute limit in either direction.'],
  kp: ['Proportional gain Kp', 'Roll percentage per image-width error.'],
  kd: ['Derivative gain Kd', 'Damping per image-width/second velocity.'],
  roll_direction: ['Roll direction (+1 or −1)', 'Depends on camera and flight direction; verify experimentally.'],
  setpoint_x: ['Horizontal setpoint (0–1)', 'Fixed image position to steer toward.'],
  deadband: ['Horizontal deadband', 'Fraction of image width.'],
  setpoint_y: ['Vertical setpoint (0–1)', '0 is the top of the image and 1 is the bottom.'],
  vertical_kp: ['Vertical gain Kp', 'Throttle percentage points per image-height error.'],
  vertical_kd: ['Vertical gain Kd', 'Throttle percentage points per image-height of motion per second.'],
  vertical_deadband: ['Vertical deadband', 'Fraction of image height.'],
  vertical_filter_alpha: ['Vertical filter alpha', 'Smaller values smooth the vertical position more.'],
  vertical_direction: ['Vertical throttle direction (+1 or −1)', '+1 adds power when the plane is below the target.'],
  max_vertical_throttle: ['Maximum vertical correction (pp)', 'Limits the amount added to or subtracted from cruise throttle.'],
  turn_throttle_boost: ['Turn throttle boost (pp)', 'Extra throttle at maximum configured roll. Smaller roll commands add less.'],
  filter_alpha: ['Position filter alpha', 'Larger responds faster; smaller smooths more.'],
  confidence: ['Minimum plane confidence', 'Only label “plane” is used.'],
  association_gate: ['Association gate', 'Maximum center jump as normalized image distance.'],
  confirm_frames: ['Consecutive confirmation frames', 'Required for acquisition and reacquisition.'],
  loss_timeout: ['Detection loss timeout (s)', 'Time since last accepted plane before stop.'],
  frame_timeout: ['Camera freshness limit (s)', 'Stale frame or frozen counter stops flight.'],
  stationary_timeout: ['Stationary detection timeout (s)', 'Ignore plane boxes without recent center movement. Set to 0 to disable.'],
  throttle_slew: ['Throttle ramp (pp/s)', 'Normal changes; loss fallback is immediate.'],
  roll_slew: ['Roll ramp (pp/s)', 'Normal changes; loss centers immediately.']
};
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({version: 2, ...data})});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Request failed'); return result;
}
function render(s) {
  const roll = Math.max(-100, Math.min(100, s.command.roll));
  const direction = Math.abs(roll) < 0.05 ? 'CENTER' : roll < 0 ? 'LEFT' : 'RIGHT';
  $('rollCommand').textContent = `${s.dry_run && s.active ? 'Dry run roll' : 'Roll command'}: ${direction} ${Math.abs(roll).toFixed(1)}%`;
  $('rollMarker').style.left = `${50 + roll / 2}%`;
  $('rollMarker').hidden = false;
  const dryRun = s.active && s.dry_run;
  const output = s.throttle_output;
  const percent = dryRun ? s.command.throttle : output?.percent;
  if (percent != null) {
    $('throttleOutput').textContent = `${dryRun ? 'Dry run throttle' : 'Throttle sent'}: ${percent.toFixed(1)}%`;
    $('throttleMeter').value = percent;
  } else if (output?.volts != null) {
    $('throttleOutput').textContent = `${output.zero_mode ? 'Throttle zero mode' : 'Throttle sent'}: ${output.volts.toFixed(2)} V`;
  } else {
    $('throttleOutput').textContent = 'Throttle output unavailable';
  }
  $('throttleMeter').hidden = percent == null;
  const timeout = s.config.stationary_timeout;
  const timeoutInput = $('stationary_timeout');
  if (timeoutInput?.value && Number(timeoutInput.value) === savedSetpoint?.stationary_timeout) {
    timeoutInput.value = timeout;
  }
  savedSetpoint = s.config;
  autonomousActive = s.active;
  const filterToggle = $('toggleStationaryFilter');
  filterToggle.textContent = timeout ? 'Disable noise filter' : 'Enable noise filter';
  filterToggle.disabled = s.settings_locked || s.active || pending;
  $('stationaryFilter').textContent = timeout
    ? `Stationary filter on (${timeout} s). ${s.active ? 'Noise marks plane boxes rejected for insufficient motion.' : 'Noise labels appear on evaluated frames during autonomous sessions.'}`
    : 'Stationary filter off.';
  if (!s.active) {
    noiseBoxes = [];
    renderNoise();
  }
  if (timeout) lastStationaryTimeout = timeout;
  $('message').textContent = `${s.source === 'gpio' ? 'GPIO · ' : ''}${s.active && s.dry_run ? 'DRY RUN · ' : ''}${s.state}: ${s.reason}`;
  $('telemetry').textContent = `Requested throttle: ${s.command.throttle.toFixed(1)}%\nRequested roll: ${s.command.roll.toFixed(1)}%`;
  if (s.throttle_feedback) {
    const {image_y, vertical_correction, turn_boost} = s.throttle_feedback;
    $('telemetry').textContent += `\nPlane Y: ${image_y.toFixed(3)} / target ${s.config.setpoint_y.toFixed(3)}`
      + `\nThrottle correction targets: vertical ${vertical_correction.toFixed(1)} pp, turn ${turn_boost.toFixed(1)} pp`;
  }
  if (s.plane_detections != null) $('telemetry').textContent += `\nPlane detections after release: ${s.plane_detections}`;
  if (s.feedback) $('telemetry').textContent += `\nController: ${s.feedback.loop_hz} Hz / target ${s.config.feedback_hz} Hz\nFresh observations used: ${s.feedback.observation_hz} /s`;
  $('cameraFps').disabled = $('applyCameraFps').disabled = s.active || pending;
  $('start').disabled = s.active || pending; $('save').disabled = s.active || pending;
  $('mode').disabled = s.active || pending;
  $('reloadLauncher').disabled = s.active || pending;
  if (s.stationary_hint) $('telemetry').textContent += s.config.stationary_timeout
    ? '\nStationary or unconfirmed plane boxes ignored'
    : '\nObservation: plane box has little recent motion';
  if (s.predicted_x != null) {
    $('telemetry').textContent += `\nEstimated plane position (observation): ${(s.predicted_x * 100).toFixed(1)}% from left`;
  }
  if (!populated) {
    for (const [key, value] of Object.entries(s.config)) {
      const label = document.createElement('label'), input = document.createElement('input'), help = document.createElement('small');
      label.textContent = labels[key][0]; input.id = key; input.type = 'number'; input.value = value;
      input.min = s.parameters[key].min; input.max = s.parameters[key].max;
      if (key === 'stationary_timeout') input.dataset.default = s.parameters[key].default;
      if (targetKeys.includes(key)) input.addEventListener('input', () => {
        liveTargetBox = null;
        $('liveTargetStatus').textContent = '';
        renderLiveTarget();
      });
      input.step = ['confirm_frames', 'roll_direction', 'vertical_direction'].includes(key) ? '1' : 'any';
      help.textContent = labels[key][1];
      label.append(input, help);
      $(throttleTrackingKeys.has(key) ? 'throttleTrackingSettings' : 'flightSettings').append(label);
    }
    populated = true;
  }
  for (const input of $('parameters').querySelectorAll('input')) input.disabled = s.active || pending;
  renderLiveTarget();
  if (!s.active) owner = null;
}
async function action(fn) {
  $('actionError').textContent = '';
  try { await fn(); }
  catch (e) { $('actionError').textContent = e.message; }
}
$('toggleStationaryFilter').onclick = async () => {
  const button = $('toggleStationaryFilter');
  const message = $('stationaryFilterMessage');
  const timeout = savedSetpoint.stationary_timeout;
  const input = $('stationary_timeout');
  let remembered = lastStationaryTimeout;
  try {
    remembered ||= Number(localStorage.getItem(stationaryTimeoutKey));
  } catch { /* The in-memory value works when storage is unavailable. */ }
  const fallback = Number(input.dataset.default);
  const enabledTimeout = remembered > 0 && remembered <= Number(input.max) ? remembered : fallback;
  const target = timeout ? 0 : enabledTimeout;
  sessionRevision++;
  pending = true;
  button.disabled = $('start').disabled = $('save').disabled = true;
  message.textContent = '';
  let status;
  try {
    status = await api('/api/autonomous/settings', {config: {stationary_timeout: target}});
    input.value = status.config.stationary_timeout;
    try {
      localStorage.setItem(stationaryTimeoutKey, String(timeout || enabledTimeout));
    } catch { /* Settings were saved on the controller. */ }
  } catch (error) {
    message.textContent = error.message;
  } finally {
    pending = false;
  }
  if (status) render(status);
};
$('saveLiveTarget').onclick = () => action(async () => {
  const config = Object.fromEntries(targetKeys.map(key => [key, Number($(key).value)]));
  render(await api('/api/autonomous/settings', {config}));
  $('liveTargetStatus').textContent = `Target X ${config.setpoint_x}, Y ${config.setpoint_y} saved.`;
});
$('save').onclick = () => action(async () => {
  const config = {}; for (const input of $('parameters').querySelectorAll('input')) {
    if (!input.reportValidity()) return; config[input.id] = Number(input.value);
  }
  render(await api('/api/autonomous/settings', {config})); $('message').textContent = 'Parameters saved.';
  if (liveTargetBox) $('liveTargetStatus').textContent = `Target X ${config.setpoint_x}, Y ${config.setpoint_y} saved.`;
});
$('reloadLauncher').onclick = () => action(async () => {
  if (pending || autonomousActive) return;
  pending = true;
  $('reloadLauncher').disabled = true;
  $('start').disabled = true;
  try {
    const launcher = await api('/api/launcher/reload', {});
    $('launcherStatus').textContent = `Lock commanded at ${launcher.lock_us} microseconds. Ready for autonomous start; release setting is ${launcher.release_us} microseconds.`;
  } finally {
    pending = false;
    render(await api('/api/autonomous'));
  }
});
$('start').onclick = () => action(async () => {
  let restore_outputs = false;
  if (liveTargetBox && targetKeys.some(key => Number($(key).value) !== savedSetpoint[key])) {
    throw Error('Save the drawn target before launch.');
  }
  const remote = await api('/api/status');
  if (!confirm('Start live autonomous launch? The arming sweep runs first, then countdown and launch throttle ramp. The launcher releases automatically after holding launch throttle for the configured delay.')) return;
  if (remote.outputs_zero) {
    if (!confirm('Outputs are in zero mode. Restore calibrated voltages and arm the aircraft?')) return;
    restore_outputs = true;
  }
  sessionRevision++;
  pending = true; $('start').disabled = true;
  renderLiveTarget();
  try {
    const s = await api('/api/autonomous/start', {mode: $('mode').value, dry_run: false, restore_outputs, predict_gaps: true});
    owner = s.owner; render(s);
  } finally { pending = false; renderLiveTarget(); }
});
async function stop() { sessionRevision++; owner = null; await api('/api/stop', {}); render(await api('/api/autonomous')); }
$('stop').onclick = () => action(stop);
document.addEventListener('keydown', e => { if (['Space', 'Escape'].includes(e.code)) { e.preventDefault(); action(stop); } });
function leave() { if (owner) { owner = null; navigator.sendBeacon('/api/stop', new Blob(['{}'], {type: 'application/json'})); } }
window.addEventListener('pagehide', leave); window.addEventListener('blur', leave);
document.addEventListener('visibilitychange', () => { if (document.hidden) leave(); });
async function poll() {
  const revision = sessionRevision;
  try {
    if (!pending) {
      const status = owner ? await api('/api/autonomous/heartbeat', {owner}) : await api('/api/autonomous');
      if (revision === sessionRevision) render(status);
    }
  } catch (e) {
    if (revision === sessionRevision) {
      owner = null;
      $('message').textContent = e.message;
      $('rollCommand').textContent = 'Roll command unavailable';
      $('rollMarker').hidden = true;
      $('throttleOutput').textContent = 'Throttle output unavailable';
      $('throttleMeter').hidden = true;
      $('toggleStationaryFilter').disabled = true;
      noiseBoxes = [];
      renderNoise();
    }
  } finally { setTimeout(poll, 200); }
}
poll();

const preview = new LatestCameraPreview($('cameraPreview'), '/camera/preview.jpg', stats => {
  $('previewMetrics').textContent = stats.ready
    ? `Browser preview: ${stats.fps.toFixed(1)} fps · frame age ≈ ${stats.ageMs == null ? 'unknown' : Math.round(stats.ageMs) + ' ms'}`
    : 'Preview unavailable. Retrying…';
}, headers => {
  noiseBoxes = JSON.parse(headers?.get('X-Noise-Boxes') || '[]');
  renderNoise();
});
let cameraDirty = false;
$('cameraFps').oninput = () => { cameraDirty = true; };
$('applyCameraFps').onclick = () => action(async () => {
  if (!$('cameraFps').reportValidity()) return;
  try {
    const result = await api('/api/autonomous/camera', {fps: Number($('cameraFps').value)});
    cameraDirty = false; preview.setRate(result);
    $('cameraMessage').textContent = 'Camera FPS requested. Actual detection FPS is shown above.';
  } catch (error) { $('cameraMessage').textContent = error.message; }
});
async function cameraPoll() {
  try {
    const s = await api('/api/autonomous/camera');
    preview.setRate(s);
    if (!cameraDirty) $('cameraFps').value = s.settings.fps;
    $('cameraFps').max = s.limits.fps_max;
    $('cameraMetrics').textContent = s.ready
      ? `Capture: ${s.frame_size?.join(' × ') ?? 'N/A'} · Detector: ${s.model_input_shape?.slice(0, 2).reverse().join(' × ') ?? 'N/A'} · Mode: ${s.detection_mode ?? 'full'} / Confidence: ${s.confidence_threshold ?? 'N/A'} / Region: ${s.inference_region?.join(', ') ?? 'full view'} / Sensor: ${s.camera_fps} fps · Detections: ${s.fps} fps · Inference: ${s.inference_ms} ms · NPU: ${s.usage?.npu_percent ?? 'N/A'}%`
      : s.error || 'Camera starting…';
  } catch (error) { $('cameraMetrics').textContent = error.message; }
  setTimeout(cameraPoll, 1000);
}
cameraPoll();
window.addEventListener('pagehide', () => preview.stop());
