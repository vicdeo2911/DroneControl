'use strict';
const $ = id => document.getElementById(id);
const colors = ['#95ebbf', '#ffc475'], names = ['plane', 'target'];
let images = [], selected = null, boxes = [], activeClass = 0, activeBox = -1;
let image = null, dirty = false, drag = null, captureTimer = null, captureActive = false, captured = 0;
let cameraActive = false, cameraTimer = null, predictionActive = false, predictionTimer = null;
let trainingActive = false, imageGeneration = 0, savingLabels = false;
let inspectionStatus = {weights: [], compiled: []}, inspectionBusy = false;
let predictionGeneration = 0, piLiveState = null;
let markedImages = new Set(), selectionAnchor = null, deletingImages = false;
const canvas = $('annotation'), context = canvas.getContext('2d');
function message(value, error = false) { $('message').textContent = value; $('message').classList.toggle('error', error); }
async function api(path, data, timeout = 15000) {
  const response = await fetch(path, {method: data === undefined ? 'GET' : 'POST',
    headers: data === undefined ? {} : {'Content-Type': 'application/json'},
    body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(timeout)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || `Request failed (${response.status})`);
  return result;
}
function run(action) { return Promise.resolve().then(action).catch(error => message(error.message, true)); }
function setDirty(value) { dirty = value; $('saveState').textContent = value ? 'Unsaved labels · save before leaving this image.' : 'Labels saved · 1/2 select class · drag to draw · Ctrl+S saves.'; }
function filtered() { const f = $('filter').value; return images.filter(i => f === 'all' || (f === 'unreviewed' ? !i.reviewed : i.split === f)); }
function library() {
  const reviewed = images.filter(i => i.reviewed).length;
  $('counts').textContent = `${reviewed} / ${images.length} reviewed`;
  $('library').replaceChildren();
  $('selectionCount').textContent = `${markedImages.size} selected`;
  $('deleteImages').disabled = !markedImages.size || deletingImages || savingLabels;
  for (const item of filtered()) {
    const button = document.createElement('button'); button.className = 'thumb' + (item.id === selected ? ' selected' : '');
    const img = document.createElement('img'); img.src = `/images/${item.id}.jpg`; img.loading = 'lazy'; img.alt = '';
    const text = document.createElement('span'); text.textContent = `${item.reviewed ? '✓' : '○'} ${item.split} · ${item.session}`;
    button.setAttribute('aria-pressed', markedImages.has(item.id));
    button.style.outline = markedImages.has(item.id) ? '2px solid var(--amber)' : '';
    button.append(img, text); button.onclick = event => run(() => selectLibraryImage(item.id, event)); $('library').append(button);
  }
}
async function selectLibraryImage(id, event) {
  if (savingLabels || deletingImages) return;
  if (event.shiftKey) {
    const ids = filtered().map(item => item.id), start = ids.indexOf(selectionAnchor), end = ids.indexOf(id);
    if (!event.ctrlKey && !event.metaKey) markedImages.clear();
    if (start >= 0) ids.slice(Math.min(start, end), Math.max(start, end) + 1).forEach(value => markedImages.add(value));
    else { markedImages.add(id); selectionAnchor = id; }
  } else if (event.ctrlKey || event.metaKey) {
    if (markedImages.has(id)) markedImages.delete(id); else markedImages.add(id);
    selectionAnchor = id;
  } else {
    await selectImage(id);
    if (selected !== id) return;
    markedImages = new Set([id]); selectionAnchor = id;
  }
  library(); $('library').focus({preventScroll: true});
}
async function refresh(selectNew = false) {
  const data = await api('/api/images'); images = data.images;
  const existing = new Set(images.map(item => item.id));
  markedImages = new Set([...markedImages].filter(id => existing.has(id))); library();
  if (!selected && images.length) await selectImage(images[0].id);
  else if (selectNew && !dirty && images.length) await selectImage(images[images.length - 1].id);
}
async function selectImage(id, force = false) {
  if (savingLabels && !force) return;
  if (id === selected && !force) return;
  if (dirty && !force && !confirm('Discard unsaved labels on this image?')) return;
  const row = images.find(i => i.id === id); if (!row) return;
  const generation = ++imageGeneration;
  const nextImage = new Image(); nextImage.src = `/images/${id}.jpg`;
  await nextImage.decode();
  if (generation !== imageGeneration) return;
  selected = id; image = nextImage; boxes = structuredClone(row.boxes); activeBox = -1; drag = null; dirty = false;
  canvas.width = row.width; canvas.height = row.height; canvas.hidden = false; $('empty').hidden = true;
  $('imageInfo').textContent = `${row.session} / ${row.split} · ${row.width} × ${row.height} · ${row.reviewed ? 'reviewed' : 'needs labels'}`;
  $('saveState').textContent = row.reviewed ? 'Saved labels loaded.' : 'Draw boxes for every plane and target. Background only? Confirm no objects.';
  draw(); library();
}
function strokeBox(ctx, box, width, height, selectedBox = false, caption = '') {
  const x = (box.x - box.w / 2) * width, y = (box.y - box.h / 2) * height;
  ctx.strokeStyle = selectedBox ? '#ffffff' : colors[box.class_id]; ctx.lineWidth = Math.max(2, width / 400);
  ctx.strokeRect(x, y, box.w * width, box.h * height);
  ctx.font = `${Math.max(14, width / 60)}px system-ui`; const label = caption || names[box.class_id];
  const textWidth = ctx.measureText(label).width;
  ctx.fillStyle = colors[box.class_id]; ctx.fillRect(x, Math.max(0, y - 25), textWidth + 10, 25);
  ctx.fillStyle = '#101719'; ctx.fillText(label, x + 5, Math.max(18, y - 7));
}
function draw() {
  if (!image) return; context.drawImage(image, 0, 0);
  boxes.forEach((b, i) => strokeBox(context, b, canvas.width, canvas.height, i === activeBox));
  if (drag) { const b = dragBox(); if (b) strokeBox(context, b, canvas.width, canvas.height); }
  $('boxes').replaceChildren();
  boxes.forEach((b, i) => { const button = document.createElement('button'); button.textContent = `${i + 1} · ${names[b.class_id]}`;
    button.className = 'class-' + names[b.class_id] + (i === activeBox ? ' selected' : '');
    button.onclick = () => { activeBox = i; draw(); }; $('boxes').append(button); });
  $('save').disabled = !selected || !boxes.length; $('negative').disabled = !selected || boxes.length > 0;
  $('deleteBox').disabled = activeBox < 0; $('undo').disabled = !boxes.length;
  $('discardImage').disabled = !selected;
}
function point(event) { const r = canvas.getBoundingClientRect(); return {x: Math.max(0, Math.min(1, (event.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (event.clientY - r.top) / r.height))}; }
function dragBox() {
  if (!drag) return null;
  return {class_id: activeClass, x: (drag.start.x + drag.end.x) / 2, y: (drag.start.y + drag.end.y) / 2,
    w: Math.abs(drag.end.x - drag.start.x), h: Math.abs(drag.end.y - drag.start.y)};
}
canvas.onpointerdown = event => { if (!image || savingLabels || event.button !== 0) return; canvas.setPointerCapture(event.pointerId); drag = {start: point(event), end: point(event)}; };
canvas.onpointermove = event => { if (drag) { drag.end = point(event); draw(); } };
canvas.onpointerup = event => {
  if (!drag) return; drag.end = point(event); const box = dragBox();
  if (box.w * canvas.width >= 3 && box.h * canvas.height >= 3) { boxes.push(box); activeBox = boxes.length - 1; setDirty(true); }
  else { const p = point(event); activeBox = boxes.findLastIndex(b => Math.abs(p.x - b.x) <= b.w / 2 && Math.abs(p.y - b.y) <= b.h / 2); }
  drag = null; draw();
};
canvas.onpointercancel = () => { drag = null; draw(); };
function chooseClass(value) { activeClass = value; $('planeClass').setAttribute('aria-pressed', value === 0); $('targetClass').setAttribute('aria-pressed', value === 1); }
$('planeClass').onclick = () => chooseClass(0); $('targetClass').onclick = () => chooseClass(1);
$('undo').onclick = () => { if (!savingLabels && boxes.length) { boxes.pop(); activeBox = -1; setDirty(true); draw(); } };
$('deleteBox').onclick = () => { if (!savingLabels && activeBox >= 0) { boxes.splice(activeBox, 1); activeBox = -1; setDirty(true); draw(); } };
async function navigate(direction) {
  const list = filtered(), index = list.findIndex(i => i.id === selected);
  const next = list[index + direction]; if (next) await selectImage(next.id);
}
async function save(negative = false) {
  if (savingLabels || !selected || (!negative && !boxes.length)) return;
  if (negative && boxes.length) throw Error('Remove boxes before confirming a background image.');
  const id = selected, savedBoxes = structuredClone(boxes), list = filtered(), index = list.findIndex(i => i.id === id);
  savingLabels = true;
  try {
    await api(`/api/images/${id}`, {boxes: savedBoxes});
    const row = images.find(i => i.id === id); row.boxes = savedBoxes; row.reviewed = true;
    if (selected === id) { setDirty(false); const next = list[index + 1]; if (next) await selectImage(next.id, true); }
    library(); message(negative ? 'Background image confirmed.' : 'Labels saved.');
  } finally { savingLabels = false; }
}
$('save').onclick = () => run(() => save()); $('negative').onclick = () => run(() => save(true));
$('previous').onclick = () => run(() => navigate(-1)); $('next').onclick = () => run(() => navigate(1));
$('discardImage').onclick = () => run(async () => {
  if (!selected || savingLabels || !confirm('Delete this image and its labels from the library? Existing training snapshots are preserved.')) return;
  await api(`/api/images/${selected}/discard`, {});
  selected = null; image = null; boxes = []; activeBox = -1; setDirty(false);
  canvas.hidden = true; $('empty').hidden = false; $('boxes').replaceChildren();
  $('imageInfo').textContent = 'No image selected'; draw(); await refresh(); message('Image discarded.');
});
$('deleteImages').onclick = () => run(async () => {
  if (savingLabels || deletingImages || !markedImages.size) return;
  const ids = [...markedImages];
  if (!confirm(`Delete ${ids.length} selected images and their labels?${dirty && ids.includes(selected) ? ' Unsaved labels on the open image will be discarded.' : ''} Existing training snapshots are preserved.`)) return;
  deletingImages = true; library();
  try {
    const result = await api('/api/images/discard', {ids}, 60000);
    if (ids.includes(selected)) {
      ++imageGeneration; selected = null; image = null; boxes = []; activeBox = -1; drag = null; setDirty(false);
      canvas.hidden = true; $('empty').hidden = false; $('boxes').replaceChildren();
      $('imageInfo').textContent = 'No image selected';
    }
    markedImages.clear(); selectionAnchor = null; await refresh(); draw();
    message(`${result.deleted} images deleted.`);
  } finally { deletingImages = false; library(); }
});
$('filter').onchange = () => { markedImages.clear(); selectionAnchor = null; library(); };
$('refresh').onclick = () => run(() => refresh());
function collection() { const session = $('session').value.trim(); if (!/^[A-Za-z0-9_-]{1,64}$/.test(session)) throw Error('Use letters, digits, underscores or hyphens in the session name.'); return {session, split: $('split').value}; }
$('import').onclick = () => run(async () => {
  const values = collection(), files = [...$('photos').files]; if (!files.length) throw Error('Choose images first.');
  $('import').disabled = true; let added = 0, duplicates = 0;
  try { for (const file of files) {
    const body = new FormData(); body.append('images', file); body.append('session', values.session); body.append('split', values.split);
    const response = await fetch('/api/import', {method: 'POST', body}); const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Image import failed.'); added += result.ids.length - result.duplicates; duplicates += result.duplicates;
  } $('photos').value = ''; message(`Imported ${added} images; skipped ${duplicates} duplicates.`);
  } finally { $('import').disabled = false; await refresh(); }
});
$('capture').onclick = () => run(async () => { const r = await api('/api/capture', collection()); await refresh(); if (!dirty) await selectImage(r.id); message(r.created ? 'Clean frame captured.' : 'Duplicate frame skipped.'); });
function stopCapture() { captureActive = false; clearTimeout(captureTimer); $('autoCapture').textContent = 'Start interval capture'; $('session').disabled = false; $('split').disabled = false; }
$('autoCapture').onclick = () => run(async () => {
  if (captureActive) { stopCapture(); return; }
  const settings = collection(), interval = Number($('interval').value), limit = Number($('captureLimit').value);
  if (!Number.isFinite(interval) || interval < 0.5 || interval > 60 || !Number.isInteger(limit) || limit < 1 || limit > 1000) throw Error('Use interval 0.5–60 seconds and limit 1–1000.');
  captureActive = true; captured = 0; $('autoCapture').textContent = 'Stop capture'; $('session').disabled = true; $('split').disabled = true;
  let attempts = 0;
  async function captureTick() {
    if (!captureActive) return;
    try { const result = await api('/api/capture', settings); attempts++; if (result.created) captured++;
      $('captureState').textContent = `${attempts} / ${limit} capture attempts · ${captured} new frames${result.created ? '' : ' · duplicate skipped'}`; await refresh();
      if (attempts >= limit) stopCapture(); else if (captureActive) captureTimer = setTimeout(captureTick, interval * 1000);
    } catch (error) { stopCapture(); message(error.message, true); }
  } await captureTick();
});
function stopCamera() { cameraActive = false; clearTimeout(cameraTimer); $('liveCamera').removeAttribute('src'); $('cameraPanel').hidden = true; }
$('hideCamera').onclick = stopCamera;
$('showCamera').onclick = () => run(async () => {
  if (cameraActive) return; cameraActive = true; $('cameraPanel').hidden = false;
  $('liveCamera').onerror = () => { if (cameraActive) { stopCamera(); message('Camera stream unavailable. Check the Pi camera service.', true); } };
  $('liveCamera').src = '/camera.mjpg';
});
$('export').onclick = () => run(async () => { const result = await api('/api/export', {preparation: $('preparation').value});
  const a = document.createElement('a'); a.href = result.download; a.textContent = 'Download dataset ZIP';
  $('exportInfo').replaceChildren(a, document.createTextNode('\nLocal data: ' + result.path)); message('Dataset snapshot exported.');
});
$('train').onclick = () => run(async () => {
  stopPrediction(); const options = {model: $('model').value, device: $('device').value, target: $('target').value, preparation: $('preparation').value};
  for (const key of ['epochs', 'batch', 'imgsz', 'workers']) options[key] = Number($(key).value);
  $('train').disabled = true;
  try { await api('/api/train', options, 60000); message('Local training started. You can keep labeling; this run uses its saved dataset snapshot.'); }
  finally { await trainingStatus(); }
});
$('stopTraining').onclick = () => run(async () => { await api('/api/train/stop', {}); await trainingStatus(); });
function preparationControls() {
  const adaptive = $('preparation').value === 'adaptive';
  if (adaptive) $('imgsz').value = '640';
  $('imgsz').disabled = adaptive;
}
$('preparation').onchange = preparationControls;
preparationControls();
function previewPreparationControls() {
  const hef = $('modelFormat').value === 'hailo';
  const live = hef && predictionActive;
  const compiled = inspectionStatus.compiled.find(r => r.path === $('weights').value);
  if (hef) {
    const size = compiled?.tested_input_size || compiled?.input_size;
    if (size && !Array.from($('previewSize').options).some(o => Number(o.value) === size)) {
      const option = document.createElement('option'); option.value = size; option.textContent = `${size} x ${size}`; $('previewSize').append(option);
    }
    if (size) $('previewSize').value = String(size);
    if (size !== 640) $('previewPreparation').value = 'full';
  }
  const adaptive = $('previewPreparation').value === 'adaptive';
  if (adaptive) $('previewSize').value = '640';
  $('previewSize').disabled = adaptive || hef || inspectionBusy;
  for (const id of ['modelFormat', 'weights', 'previewPreparation', 'confidence']) $(id).disabled = inspectionBusy || live;
  for (const id of ['previewPreparation','previewSize','confidence']) {
    $(id).hidden = live;
    document.querySelector(`label[for="${id}"]`).hidden = live;
  }
  for (const id of ['predictImage','predictPiFrame']) $(id).disabled = inspectionBusy || live || !$('weights').value || (trainingActive && !hef);
  $('predictLive').disabled = inspectionBusy || !$('weights').value || (trainingActive && !hef) || (hef && inspectionStatus.liveAvailable === false);
  $('predictPiFrame').hidden = !hef;
  $('activateHef').hidden = !hef;
  $('activateHef').disabled = inspectionBusy || live || !compiled?.tested;
  $('activateHef').title = compiled?.tested ? 'Activate this exact tested HEF on the Pi' : 'Run a Full frame test of this HEF on the Pi first';
  $('predictLive').textContent = hef ? live ? 'Stop live Pi preview' : 'Start live Pi preview' : predictionActive ? 'Stop live preview' : 'Start live preview';
  $('modelBackendInfo').textContent = hef
    ? inspectionStatus.hefAvailable === false
      ? 'The running studio backend needs a restart to support HEF inspection. Wait for the current training/compilation to finish, then restart the studio and refresh this page.'
      : live ? 'Live detections are produced by the Pi NPU using its active model and settings. Viewing does not stop the camera or send aircraft commands.'
      : `Start live Pi preview views this HEF continuously when it is active on the Pi. For a different HEF, test it in Full frame, review detections, then Activate on Pi. Single-frame tests briefly pause camera inference; live viewing does not. Live uses the Pi confidence threshold.${inspectionStatus.liveAvailable === false ? ' Restart the studio backend after the current job to enable live Pi preview.' : ''}`
    : 'Standard YOLO runs on the laptop. Selecting a model does not activate it on the Pi.';
}
$('previewPreparation').onchange = previewPreparationControls;
$('weights').onchange = () => { stopPrediction(); previewPreparationControls(); };
$('modelFormat').onchange = () => { stopPrediction(); populateInspectionModels(); };
function populateInspectionModels() {
  if (inspectionBusy) return;
  if (predictionActive && $('modelFormat').value === 'hailo') { previewPreparationControls(); return; }
  const hef = $('modelFormat').value === 'hailo';
  const models = hef ? inspectionStatus.compiled.map(r => ({path:r.path, label:r.path}))
    : inspectionStatus.weights.map(path => ({path, label:path}));
  const previous = $('weights').value;
  if (!models.length || Array.from($('weights').options).map(o => o.value).join('|') !== models.map(r => r.path).join('|')) {
    $('weights').replaceChildren();
    for (const item of models) { const option = document.createElement('option'); option.value = item.path; option.textContent = item.label; $('weights').append(option); }
    if (!models.length) { const option = document.createElement('option'); option.value = ''; option.textContent = hef ? 'No completed compiled HEF yet' : 'No trained model yet'; $('weights').append(option); }
    else $('weights').value = models.some(r => r.path === previous) ? previous : models[models.length-1].path;
  }
  previewPreparationControls();
}
previewPreparationControls();
async function trainingStatus() {
  const result = await api('/api/training'); trainingActive = result.active;
  $('train').disabled = result.active; $('stopTraining').disabled = !result.active;
  $('trainingState').textContent = `${result.state} / ${result.target === 'hailo' ? 'YOLO + Hailo' : 'Standard YOLO'}${result.name ? ' · ' + result.name : ''}`;
  const log = $('trainingLog'), follow = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  log.textContent = result.log || 'Training output will appear here.'; if (follow) log.scrollTop = log.scrollHeight;
  inspectionStatus = {weights: result.weights, compiled: result.compiled || [], hefAvailable: Array.isArray(result.compiled), activationAvailable: result.hef_activation === true, liveAvailable: result.hef_live === true};
  populateInspectionModels();
}
function stopPrediction() {
  const wasPiLive = piLiveState !== null;
  predictionActive = false; predictionGeneration++; clearTimeout(predictionTimer); piLiveState = null;
  $('piLivePreview').removeAttribute('src'); $('piLivePreview').hidden = true;
  if (wasPiLive) $('predictionInfo').textContent = 'Live Pi preview stopped.';
  previewPreparationControls();
}
async function predict(live = false, generation = predictionGeneration) {
  if (!live && !selected) throw Error('Select a library image first.');
  const hef = $('modelFormat').value === 'hailo';
  const options = {weights: $('weights').value, confidence: Number($('confidence').value), imgsz: Number($('previewSize').value), preparation: $('previewPreparation').value};
  if (!live) options.image_id = selected;
  let result;
  inspectionBusy = true; previewPreparationControls();
  if (hef) { stopCamera(); $('predictionInfo').textContent = 'Testing compiled model on the Pi. Camera inference will resume after this test.'; }
  try { result = await api('/api/predict', options, hef ? 180000 : 60000); }
  finally { inspectionBusy = false; previewPreparationControls(); }
  const img = new Image(); img.src = result.image; await img.decode();
  if (generation !== predictionGeneration || (live && !hef && !predictionActive)) return;
  $('piLivePreview').hidden = true;
  const c = $('prediction'); c.width = result.width; c.height = result.height; c.hidden = false;
  const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
  result.detections.forEach(d => { const [x1, y1, x2, y2] = d.box, class_id = names.indexOf(d.label);
    strokeBox(ctx, {class_id, x: (x1+x2)/2/c.width, y: (y1+y2)/2/c.height, w: (x2-x1)/c.width, h: (y2-y1)/c.height}, c.width, c.height, false, `${d.label} ${Math.round(d.confidence*100)}%`);
  });
  const g = result.geometry;
  if (result.backend === 'hailo' && result.activation_ready) {
    const record = inspectionStatus.compiled.find(r => r.path === options.weights && r.sha256 === result.artifact_sha256);
    if (record) { record.tested = true; record.tested_input_size = result.detector_input_size; }
    previewPreparationControls();
  }
  $('predictionInfo').textContent = `${result.detections.length} detections / ${result.preparation ?? 'full'} / ${result.tiles ?? 1} tiles · input ${result.detector_input_size} × ${result.detector_input_size} · ${result.elapsed_ms} ms${g.ready ? `\nTarget relative to plane: Δx ${g.dx}, Δy ${g.dy}.` : ''}\n${g.reason}\nObservation only · physical passage unconfirmed.`;
  if (result.backend === 'hailo') $('predictionInfo').textContent += `\nActual Hailo NPU: ${result.inference_ms} ms; test round trip: ${result.round_trip_ms} ms. Camera service restored; active model unchanged.`;
}
$('predictImage').onclick = () => run(async () => { stopPrediction(); await predict(false); });
$('predictPiFrame').onclick = () => run(async () => { stopPrediction(); await predict(true); });

function renderPiLive(status) {
  const g = status.geometry;
  $('predictionInfo').textContent = `Live Pi Hailo / ${status.model}\nInput ${status.model_input_shape.slice(0,2).join(' x ')} / Capture ${status.frame_size.join(' x ')}\nDetections: ${status.fps} /s / Capture: ${status.capture_fps ?? status.camera_fps} fps / Preview limit: ${status.preview_fps_limit} fps\nNPU inference: ${status.inference_ms} ms / Detection age: ${Math.round(status.frame_age_seconds*1000)} ms / Preview age: ${Math.round(status.preview_age_seconds*1000)} ms\nPi mode: ${status.configured_detection_mode ?? status.detection_mode} / Confidence: ${status.confidence_threshold} / ${status.detections.length} detections\n${g.ready ? `Target relative to plane: dx ${g.dx}, dy ${g.dy}. ` : ''}${g.reason}\nObservation only; no aircraft commands are sent.`;
}
async function startPiLive() {
  const record = inspectionStatus.compiled.find(r => r.path === $('weights').value);
  if (!record) throw Error('Select a completed compiled HEF first.');
  const generation = ++predictionGeneration;
  predictionActive = true; inspectionBusy = true; previewPreparationControls();
  $('prediction').hidden = true;
  $('predictionInfo').textContent = 'Connecting to the live Pi detector...';
  const query = new URLSearchParams({weights:record.path,sha256:record.sha256}).toString();
  try {
    const status = await api('/api/hailo/live?' + query);
    if (generation !== predictionGeneration) return;
    piLiveState = {query, instance:status.instance_id};
    $('prediction').hidden = true; $('piLivePreview').hidden = false;
    $('piLivePreview').src = '/camera/hailo.mjpg?' + query + '&t=' + Date.now();
    renderPiLive(status);
    const tick = async () => {
      if (!predictionActive || generation !== predictionGeneration) return;
      try {
        const next = await api('/api/hailo/live?' + query);
        if (!predictionActive || generation !== predictionGeneration) return;
        if (next.instance_id !== piLiveState.instance) {
          piLiveState.instance = next.instance_id;
          $('piLivePreview').src = '/camera/hailo.mjpg?' + query + '&t=' + Date.now();
        }
        renderPiLive(next);
        predictionTimer = setTimeout(tick,500);
      } catch (error) { if (generation === predictionGeneration) { stopPrediction(); message(error.message,true); } }
    };
    predictionTimer = setTimeout(tick,500);
  } catch (error) { if (generation === predictionGeneration) stopPrediction(); throw error; }
  finally { inspectionBusy = false; previewPreparationControls(); }
}
$('piLivePreview').onerror = () => {
  if (piLiveState && predictionActive) { stopPrediction(); message('Live Pi stream disconnected. Check the active model and start the preview again.',true); }
};
$('activateHef').onclick = () => run(async () => {
  stopPrediction();
  const record = inspectionStatus.compiled.find(r => r.path === $('weights').value);
  if ($('modelFormat').value !== 'hailo' || !record?.tested) throw Error('Test this exact HEF on the Pi first.');
  const size = record.tested_input_size || record.input_size;
  if (!confirm(`Activate ${record.path} on the Pi?\n\nInput: ${size} x ${size}. Full-frame detection.\nCamera settings and the Pi confidence threshold are retained. Camera inference will restart. Controls and recording must be stopped. This does not arm the aircraft.`)) return;
  inspectionBusy = true; stopCamera(); previewPreparationControls();
  message('Activating the tested HEF on the Pi...');
  try {
    const result = await api('/api/hailo/activate', {weights:record.path,sha256:record.sha256,confirmed:true},180000);
    message(`Pi active detector: ${result.weights} (${result.model_input_shape.slice(0,2).join(' x ')}).`);
  } finally { inspectionBusy = false; previewPreparationControls(); }
});
$('predictLive').onclick = () => run(async () => {
  if (predictionActive) { stopPrediction(); return; }
  if ($('modelFormat').value === 'hailo') { await startPiLive(); return; }
  predictionActive = true; const generation = ++predictionGeneration; previewPreparationControls();
  async function tick() { if (!predictionActive || generation !== predictionGeneration) return; try { await predict(true,generation); if (predictionActive && generation === predictionGeneration) predictionTimer = setTimeout(tick, 200); } catch (error) { if (generation === predictionGeneration) { stopPrediction(); message(error.message, true); } } } await tick();
});
document.addEventListener('visibilitychange', () => { if (document.hidden) { stopCapture(); stopCamera(); stopPrediction(); } });
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('keydown', event => {
  if (event.key === 'Delete' && event.target.closest('#libraryPanel')) {
    event.preventDefault(); $('deleteImages').click(); return;
  }
  if (event.ctrlKey && event.key.toLowerCase() === 's') { event.preventDefault(); run(() => save()); return; }
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName)) return;
  if (event.key === '1') chooseClass(0); if (event.key === '2') chooseClass(1);
  if (event.key === 'Delete') $('deleteBox').click();
  if (event.key === 'Escape') { drag = null; stopCapture(); stopPrediction(); draw(); }
});
async function poll() { try { await trainingStatus(); } catch (error) { message(error.message, true); } setTimeout(poll, 2500); }
run(async () => { draw(); await refresh(); }); poll();
let extractionSeen = '';
$('extractFrames').onclick = () => run(async () => {
  const id = $('recordingSelect').value;
  if (!id) throw Error('Choose a completed recording first.');
  if (!$('extractionLimit').reportValidity()) return;
  await api('/api/video-extraction', {id, split: $('split').value, interval: Number($('sampleInterval').value),
    max_frames: Number($('extractionLimit').value)});
  $('extractFrames').disabled = true; message('Downloading the clean clip and extracting frames.');
});
async function extractionStatus() {
  try {
    const status = await api('/api/video-extraction');
    $('extractFrames').dataset.busy = status.active ? 'true' : 'false';
    $('extractFrames').disabled = status.active || !$('recordingSelect').value;
    $('extractionStatus').textContent = status.state === 'idle' ? 'Extracted frames appear in the image library, ready for your labels.' :
      `${status.state} · ${status.added} new frames${status.error ? ' · ' + status.error : ''}`;
    const signature = `${status.id}:${status.state}:${status.sampled}`;
    if (status.state === 'completed' && signature !== extractionSeen) { extractionSeen = signature; await refresh(); message(`${status.added} frames added. Label the plane and target in the image library.`); }
  } catch (error) { $('extractionStatus').textContent = error.message; }
  setTimeout(extractionStatus, 1200);
}
extractionStatus();
