'use strict';
// The setup monitor never sends aircraft commands. remote.js owns the flight session.
window.planeJoystick = (() => {
  const el = id => document.getElementById(id);
  const storageKey = 'plane-joystick-v1';
  let device = null, draft = null, saved = null, dirty = false, locked = false;
  let identifying = null, limits = {throttle: 50, roll: 50}, rows = [];
  let storageError = '', apiError = '';
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const empty = id => ({device: id, throttleAxis: -1, rollAxis: -1,
    idle: null, full: null, left: null, center: null, right: null, deadzone: 5});
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  function pads() {
    if (!window.isSecureContext || typeof navigator.getGamepads !== 'function') return [];
    try { apiError = ''; return Array.from(navigator.getGamepads()).filter(pad => pad?.connected); }
    catch (error) { apiError = error.message; return []; }
  }
  function current() {
    return pads().find(pad => pad.index === device?.index && pad.id === device?.id) || null;
  }
  function valid(mapping, pad) {
    if (!mapping || !pad || mapping.device !== pad.id) return false;
    for (const name of ['throttleAxis', 'rollAxis']) {
      if (!Number.isInteger(mapping[name]) || mapping[name] < 0 || mapping[name] >= pad.axes.length) return false;
    }
    if (mapping.throttleAxis === mapping.rollAxis) return false;
    if (!['idle', 'full', 'left', 'center', 'right'].every(name => finite(mapping[name]) && Math.abs(mapping[name]) <= 1)) return false;
    if (Math.abs(mapping.full - mapping.idle) < .25) return false;
    if (!finite(mapping.deadzone) || mapping.deadzone < 0 || mapping.deadzone > 20) return false;
    return Math.min(mapping.left, mapping.right) + .1 < mapping.center &&
      mapping.center < Math.max(mapping.left, mapping.right) - .1;
  }
  function normalize(mapping, pad) {
    if (!valid(mapping, pad)) return null;
    const t = pad.axes[mapping.throttleAxis], r = pad.axes[mapping.rollAxis];
    if (!finite(t) || !finite(r) || Math.abs(t) > 1 || Math.abs(r) > 1) return null;
    const throttle = clamp((t - mapping.idle) / (mapping.full - mapping.idle), 0, 1);
    const towardRight = (r - mapping.center) * Math.sign(mapping.right - mapping.center) >= 0;
    const end = towardRight ? mapping.right : mapping.left;
    const distance = clamp(Math.abs((r - mapping.center) / (end - mapping.center)), 0, 1);
    const zone = mapping.deadzone / 100;
    const roll = Math.max(0, (distance - zone) / (1 - zone)) * (towardRight ? 1 : -1);
    return {throttle, roll};
  }
  function sample() { return !dirty ? normalize(saved, current()) : null; }
  function armError() {
    const value = sample();
    if (!value) return 'Connect your joystick, calibrate throttle and roll, then save the mapping.';
    if (value.throttle > .03 || Math.abs(value.roll) > .05) return 'Move throttle to idle and center roll before arming.';
    return '';
  }
  function status(text, error = false) {
    el('joystickMessage').textContent = text;
    el('joystickMessage').classList.toggle('error', error);
  }
  function choose(pad) {
    if (locked) return;
    device = pad ? {id: pad.id, index: pad.index} : null;
    saved = null; dirty = false; identifying = null;
    draft = empty(pad?.id || '');
    if (pad) {
      try {
        const value = JSON.parse(localStorage.getItem(storageKey) || 'null');
        if (valid(value, pad)) { saved = {...value}; draft = {...value}; }
      } catch { storageError = 'Browser storage is unavailable. Mapping will last for this page only.'; }
    }
    for (const name of ['throttleAxis', 'rollAxis']) {
      const select = el(name);
      select.replaceChildren(new Option('Choose an axis', '-1'));
      for (let i = 0; i < (pad?.axes.length || 0); i++) select.add(new Option('Axis ' + (i + 1), String(i)));
      select.value = draft[name];
    }
    el('joystickDeadzone').value = draft.deadzone;
    el('axisList').replaceChildren(); rows = [];
    for (let i = 0; i < (pad?.axes.length || 0); i++) {
      const row = document.createElement('div'); row.className = 'axis-row';
      const label = document.createElement('span'); label.textContent = 'Axis ' + (i + 1);
      const meter = document.createElement('meter'); meter.min = -1; meter.max = 1;
      meter.setAttribute('aria-label', 'Axis ' + (i + 1) + ' position');
      const output = document.createElement('output');
      row.append(label, meter, output); el('axisList').append(row); rows.push({meter, output});
    }
  }
  function editAxis(name, value) {
    draft[name] = value;
    for (const point of (name === 'throttleAxis' ? ['idle', 'full'] : ['left', 'center', 'right'])) draft[point] = null;
    dirty = true;
  }
  for (const name of ['throttleAxis', 'rollAxis']) el(name).onchange = () => {
    if (!locked && draft) { identifying = null; editAxis(name, Number(el(name).value)); }
  };
  for (const button of document.querySelectorAll('[data-identify]')) button.onclick = () => {
    const pad = current();
    if (!pad || locked) return;
    identifying = {name: button.dataset.identify, baseline: Array.from(pad.axes), until: performance.now() + 10000};
    status('Move only the ' + (identifying.name === 'throttleAxis' ? 'throttle' : 'roll') + ' stick through its range.');
  };
  for (const button of document.querySelectorAll('[data-capture]')) button.onclick = () => {
    const pad = current(), point = button.dataset.capture;
    const axis = draft?.[['idle', 'full'].includes(point) ? 'throttleAxis' : 'rollAxis'];
    if (locked || !pad || !Number.isInteger(axis) || !finite(pad.axes[axis])) return;
    draft[point] = pad.axes[axis]; dirty = true;
    status(point[0].toUpperCase() + point.slice(1) + ' position captured. Complete all positions, then save mapping.');
  };
  el('joystickDeadzone').oninput = () => {
    if (!locked && draft) { draft.deadzone = Number(el('joystickDeadzone').value); dirty = true; }
  };
  el('saveJoystick').onclick = () => {
    if (locked || !valid(draft, current())) return;
    saved = {...draft}; dirty = false;
    try { localStorage.setItem(storageKey, JSON.stringify(saved)); storageError = ''; }
    catch { storageError = 'Browser storage is unavailable. Mapping will last for this page only.'; }
    status('Mapping saved on this laptop. Put throttle at idle and roll at center to arm.');
  };
  el('joystickDevice').onchange = () => choose(pads().find(pad => String(pad.index) === el('joystickDevice').value));
  window.addEventListener('gamepaddisconnected', event => {
    if (event.gamepad.index === device?.index && event.gamepad.id === device?.id) {
      window.dispatchEvent(new Event('plane-joystick-lost'));
    }
  });
  function frame(now) {
    const available = pads();
    const options = available.map(pad => pad.index + ':' + pad.id).join('|');
    if (el('joystickDevice').dataset.devices !== options) {
      el('joystickDevice').dataset.devices = options;
      el('joystickDevice').replaceChildren(new Option('Select controller', ''));
      for (const pad of available) el('joystickDevice').add(new Option(pad.id, String(pad.index)));
      if (device) el('joystickDevice').value = device.index;
    }
    if (!device && available.length && !locked) choose(available[0]);
    const pad = current();
    if (!locked && device && !pad) choose(null);
    if (pad) el('joystickDevice').value = pad.index;
    el('joystickConnection').textContent = !window.isSecureContext ? 'Open the laptop link below'
      : apiError ? 'Joystick access blocked' : pad ? 'Joystick connected' : 'Waiting for joystick';
    el('joystickName').textContent = pad ? pad.id + ' · ' + pad.axes.length + ' axes' :
      'Connect your BETAFPV joystick, then move a stick or press a controller button.';
    el('localhostHelp').hidden = window.isSecureContext;
    if (pad) {
      rows.forEach((row, index) => {
        const value = pad.axes[index];
        row.meter.value = finite(value) ? value : 0;
        row.output.textContent = finite(value) ? value.toFixed(3) : 'Unavailable';
      });
      if (identifying && !locked) {
        const changes = pad.axes.map((value, index) => Math.abs(value - identifying.baseline[index]));
        const biggest = Math.max(...changes), index = changes.indexOf(biggest);
        if (biggest > .35) {
          const name = identifying.name;
          editAxis(name, index); el(name).value = index; identifying = null;
          status('Axis ' + (index + 1) + ' selected. Capture its positions below.');
        } else if (now > identifying.until) { identifying = null; status('No clear movement detected. Try again or choose an axis manually.'); }
      }
    }
    for (const button of document.querySelectorAll('[data-capture]')) {
      const point = button.dataset.capture;
      const axis = draft?.[['idle', 'full'].includes(point) ? 'throttleAxis' : 'rollAxis'];
      button.disabled = locked || !pad || !Number.isInteger(axis) || axis < 0;
      el('joystick_' + point).textContent = finite(draft?.[point]) ? draft[point].toFixed(3) : 'Not captured';
    }
    for (const input of document.querySelectorAll('[data-joystick-edit]')) input.disabled = locked || !pad;
    el('joystickDevice').disabled = locked || !available.length;
    el('saveJoystick').disabled = locked || !dirty || !valid(draft, pad);
    el('joystickDeadzoneRead').textContent = (draft?.deadzone ?? 5) + '%';
    const preview = normalize(draft, pad);
    el('joystickThrottlePreview').textContent = preview ? (preview.throttle * limits.throttle).toFixed(1) + '%' : '—';
    el('joystickRollPreview').textContent = preview ? (preview.roll * limits.roll).toFixed(1) + '%' : '—';
    el('joystickReady').textContent = dirty ? 'Unsaved mapping. Use different axes and capture all five positions.' : armError() || 'Ready to arm with joystick.';
    if (apiError) status(apiError, true);
    el('joystickStorage').textContent = storageError;
    requestAnimationFrame(frame);
  }
  choose(null); requestAnimationFrame(frame);
  return {sample, armError, setLocked(value) { locked = value; if (value) identifying = null; },
    setLimits(value) { limits = value; }};
})();
