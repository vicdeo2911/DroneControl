'use strict';
const $ = id => document.getElementById(id);
let owner = null, pending = false, populated = false;
const labels = {
  feedback_hz: ['Feedback rate (Hz)', '2–60 updates/s; fresh observations are limited by detection FPS.'],
  launch_delay: ['Launch countdown (s)', 'Idle until the launch cue.'],
  launch_seconds: ['Launch phase (s)', 'Timed launch throttle before acquisition.'],
  acquire_timeout: ['Acquisition timeout (s)', 'Wait limit before launch in view / after launch.'],
  max_flight_seconds: ['Session limit (s)', 'Includes arming and countdown.'],
  launch_throttle: ['Launch throttle (%)', 'Absolute calibrated percentage.'],
  cruise_throttle: ['Cruise throttle (%)', 'Constant while tracking.'],
  lost_throttle: ['Lost throttle (%)', 'Immediate fallback during brief detection loss.'],
  max_roll: ['Maximum roll (%)', 'Absolute limit in either direction.'],
  kp: ['Proportional gain Kp', 'Roll percentage per image-width error.'],
  kd: ['Derivative gain Kd', 'Damping per image-width/second velocity.'],
  roll_direction: ['Roll direction (+1 or −1)', 'Depends on camera and flight direction; verify experimentally.'],
  setpoint_x: ['Horizontal setpoint (0–1)', 'Fixed image position to steer toward.'],
  deadband: ['Horizontal deadband', 'Fraction of image width.'],
  filter_alpha: ['Position filter alpha', 'Larger responds faster; smaller smooths more.'],
  confidence: ['Minimum plane confidence', 'Only label “plane” is used.'],
  association_gate: ['Association gate', 'Maximum center jump as normalized image distance.'],
  confirm_frames: ['Consecutive confirmation frames', 'Required for acquisition and reacquisition.'],
  loss_timeout: ['Detection loss timeout (s)', 'Time since last accepted plane before stop.'],
  frame_timeout: ['Camera freshness limit (s)', 'Stale frame or frozen counter stops flight.'],
  throttle_slew: ['Throttle ramp (pp/s)', 'Normal changes; loss fallback is immediate.'],
  roll_slew: ['Roll ramp (pp/s)', 'Normal changes; loss centers immediately.']
};
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({version: 2, ...data})});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Request failed'); return result;
}
function render(s) {
  $('message').textContent = `${s.source === 'gpio' ? 'GPIO · ' : ''}${s.dry_run ? 'DRY RUN · ' : ''}${s.state}: ${s.reason}`;
  $('telemetry').textContent = `Requested throttle: ${s.command.throttle.toFixed(1)}%\nRequested roll: ${s.command.roll.toFixed(1)}%`;
  if (s.feedback) $('telemetry').textContent += `\nController: ${s.feedback.loop_hz} Hz / target ${s.config.feedback_hz} Hz\nFresh observations used: ${s.feedback.observation_hz} /s`;
  $('cameraFps').disabled = $('applyCameraFps').disabled = s.active || pending;
  $('start').disabled = s.active || pending; $('save').disabled = s.active || pending;
  for (const id of ['mode', 'dry']) $(id).disabled = s.active || pending;
  if (!populated) {
    for (const [key, value] of Object.entries(s.config)) {
      const label = document.createElement('label'), input = document.createElement('input'), help = document.createElement('small');
      label.textContent = labels[key][0]; input.id = key; input.type = 'number'; input.value = value;
      input.min = s.parameters[key].min; input.max = s.parameters[key].max;
      input.step = ['confirm_frames', 'roll_direction'].includes(key) ? '1' : 'any';
      help.textContent = labels[key][1]; label.append(input, help); $('parameters').append(label);
    }
    populated = true;
  }
  for (const input of $('parameters').querySelectorAll('input')) input.disabled = s.active || pending;
  if (!s.active) owner = null;
}
async function action(fn) { try { await fn(); } catch (e) { $('message').textContent = e.message; } }
$('save').onclick = () => action(async () => {
  const config = {}; for (const input of $('parameters').querySelectorAll('input')) {
    if (!input.reportValidity()) return; config[input.id] = Number(input.value);
  }
  render(await api('/api/autonomous/settings', {config})); $('message').textContent = 'Parameters saved.';
});
$('start').onclick = () => action(async () => {
  let restore_outputs = false;
  if (!$('dry').checked) {
    const remote = await api('/api/status');
    if (!confirm('Start live autonomous outputs? Hold the aircraft for the full-throttle arming sweep; launch at the countdown cue.')) return;
    if (remote.outputs_zero) {
      if (!confirm('Outputs are in zero mode. Restore calibrated voltages and arm the aircraft?')) return;
      restore_outputs = true;
    }
  }
  pending = true; $('start').disabled = true;
  try { const s = await api('/api/autonomous/start', {mode: $('mode').value, dry_run: $('dry').checked, restore_outputs}); owner = s.owner; render(s); }
  finally { pending = false; }
});
async function stop() { owner = null; await api('/api/stop', {}); render(await api('/api/autonomous')); }
$('stop').onclick = () => action(stop);
document.addEventListener('keydown', e => { if (['Space', 'Escape'].includes(e.code)) { e.preventDefault(); action(stop); } });
function leave() { if (owner) { owner = null; navigator.sendBeacon('/api/stop', new Blob(['{}'], {type: 'application/json'})); } }
window.addEventListener('pagehide', leave); window.addEventListener('blur', leave);
document.addEventListener('visibilitychange', () => { if (document.hidden) leave(); });
async function poll() {
  try { render(owner ? await api('/api/autonomous/heartbeat', {owner}) : await api('/api/autonomous')); }
  catch (e) { owner = null; $('message').textContent = e.message; }
  setTimeout(poll, 200);
}
poll();

const preview = new LatestCameraPreview($('cameraPreview'), '/camera/preview.jpg', stats => {
  $('previewMetrics').textContent = stats.ready
    ? `Browser preview: ${stats.fps.toFixed(1)} fps · frame age ≈ ${stats.ageMs == null ? 'unknown' : Math.round(stats.ageMs) + ' ms'}`
    : 'Preview unavailable. Retrying…';
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
