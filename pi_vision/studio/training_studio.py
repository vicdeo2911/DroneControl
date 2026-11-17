"""Loopback-only labeling, local training and read-only detection preview."""
import argparse
import io
import json
import logging
import math
import re
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

from flask import Flask, Response, jsonify, render_template, request, send_file
from PIL import Image, UnidentifiedImageError

from pi_vision.studio.training_data import CLASSES, Dataset
from pi_vision.shared.spatial_detection import search_regions, translate_detections, merge_detections
from pi_vision.studio.video_collection import VideoExtraction, camera_json
from pi_vision.studio.hailo_inspection import compiled_models, inspect_on_pi, activate_on_pi

ROOT = Path(__file__).resolve().parents[2]


class TrainingJob:
    def __init__(self, runs):
        self.runs = Path(runs).resolve()
        self.runs.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.log = None
        self.name = None
        self.stopped = False
        self.target = 'yolo'
        self.lock = threading.RLock()

    def status(self):
        with self.lock:
            code = self.process.poll() if self.process else None
            active = self.process is not None and code is None
            log = ''
            if self.log and self.log.exists():
                with self.log.open('rb') as stream:
                    stream.seek(max(0, self.log.stat().st_size - 12000))
                    log = stream.read().decode('utf-8', errors='replace')
                    log = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', log).replace('\r', '\n')
            return dict(active=active, exit_code=code, name=self.name, log=log, target=self.target,
                        state='running' if active else 'stopped' if self.stopped else
                        'ready' if self.process is None else 'completed' if code == 0 else 'failed')

    def weights(self):
        return {p.relative_to(self.runs).as_posix(): p for p in self.runs.glob('*/weights/best.pt')}

    def start(self, dataset, options):
        with self.lock:
            if self.status()['active']:
                raise ValueError('Training is already running.')
            numbers = {}
            for key, low, high, default in [('epochs', 1, 1000, 100), ('batch', 1, 64, 8), ('imgsz', 320, 1920, 640)]:
                value = options.get(key, default)
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    raise ValueError(f'{key} must be a whole number from {low} to {high}.')
                numbers[key] = value
            if numbers['imgsz'] % 32:
                raise ValueError('Image size must be divisible by 32.')
            model = options.get('model', 'yolov8s.pt')
            if model not in ('yolov8n.pt', 'yolov8s.pt', 'yolov8m.pt'):
                raise ValueError('Choose YOLOv8n, YOLOv8s or YOLOv8m.')
            preparation = options.get('preparation', 'full')
            if preparation not in ('full', 'adaptive'):
                raise ValueError('Choose full or adaptive dataset preparation.')
            if preparation == 'adaptive' and numbers['imgsz'] != 640:
                raise ValueError('The adaptive flight pipeline uses 640 input. Choose 640 for matching training crops.')
            device = options.get('device', 'auto')
            if device not in ('auto', 'cpu', '0'):
                raise ValueError('Choose auto, cpu or 0 (CUDA).')
            target = options.get('target', 'yolo')
            if target not in ('yolo', 'hailo'):
                raise ValueError('Choose standard YOLO or Hailo compilation.')
            if target == 'hailo':
                try:
                    subprocess.run(['docker', 'exec', 'pi-vision-hailo-compiler', 'python', '-c',
                                    'import hailo_sdk_client'], check=True, capture_output=True, timeout=30)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValueError('Start or unpause the configured pi-vision-hailo-compiler Docker container before choosing Hailo.') from exc
            folder = dataset.export(profile=preparation, crop_size=numbers['imgsz']) if preparation == 'adaptive' else dataset.export()
            self.name = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
            self.log = self.runs / (self.name + '.log')
            cmd = [sys.executable, '-u', '-m', 'pi_vision.studio.train_yolo', 'train', '--data', str(folder / 'data.yaml'),
                   '--project', str(self.runs), '--name', self.name, '--model', model, '--device', device,
                   '--target', target]
            for key, value in numbers.items():
                cmd.extend(['--' + key, str(value)])
            with self.log.open('wb') as output:
                self.process = subprocess.Popen(cmd, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            self.stopped = False
            self.target = target
            return self.status()

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.stopped = True
                self.process.terminate()
                if self.target == 'hailo':
                    from pi_vision.studio.training_output import stop_compiler
                    stop_compiler(self.name)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            return self.status()


def fetch_camera(camera):
    """Never fall back to annotated snapshots or silently accept stale frames."""
    try:
        with urllib.request.urlopen(camera.rstrip('/') + '/raw.jpg', timeout=3) as response:
            age = float(response.headers.get('X-Frame-Age', 'inf'))
            sequence = response.headers.get('X-Frame-Sequence')
            if not math.isfinite(age) or age < 0 or age > 1 or sequence is None:
                raise ValueError('Camera frame is stale or missing freshness headers. Update app.py on the Pi.')
            content = response.read(20_000_001)
            if len(content) > 20_000_000:
                raise ValueError('Camera image exceeds 20 MB.')
            # Verify JPEG rather than returning an upstream error page as an image.
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
            return content, sequence
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError('Cannot fetch clean camera frame. Check Pi connection/camera and deploy updated app.py. ' + str(exc)) from exc


def relative_geometry(detections, width, height):
    """Image-space observations only; a 2D overlap does not establish passage."""
    selected = {}
    for name in CLASSES:
        matches = [d for d in detections if d['label'] == name]
        if len(matches) != 1:
            return dict(ready=False, reason=f'Need exactly one {name}; found {len(matches)}.')
        selected[name] = matches[0]
    def center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2, (y1 + y2) / 2)
    px, py = center(selected['plane']['box'])
    tx, ty = center(selected['target']['box'])
    return dict(ready=True, dx=round((tx - px) / width, 4), dy=round((ty - py) / height, 4),
                reason='Target minus plane center, normalized to image size. Depth and passage are unknown.')


def prediction_source(dataset, camera, ident):
    if ident:
        if not any(r['id'] == ident for r in dataset.list()):
            raise ValueError('Image not found.')
        return (dataset.images / (ident + '.jpg')).read_bytes(), None
    return fetch_camera(camera)


def create_app(data_root=ROOT / 'data/plane-target', camera='http://192.168.137.190:8000', runs=ROOT / 'runs/plane-target'):
    app = Flask(__name__, root_path=str(Path(__file__).resolve().parent))

    @app.get('/static/recording.js')
    def recording_script():
        return send_file(Path(__file__).resolve().parents[1] / 'shared/static/recording.js', mimetype='text/javascript')

    app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024
    dataset, job = Dataset(data_root), TrainingJob(runs)
    extraction = VideoExtraction(dataset, camera)
    app.extensions.update(dataset=dataset, training_job=job, video_extraction=extraction)
    gpu_lock = threading.Lock()
    hef_lock = threading.Lock()
    tested_hefs = {}
    def pi_host():
        host=urllib.parse.urlparse(camera).hostname
        return '192.168.137.190' if host in (None,'localhost','127.0.0.1') else host

    def compiled_status():
        records=compiled_models(job.runs,job.name if job.status()['active'] else None)
        return [{**record,'tested':record['sha256'] in tested_hefs,
                 'tested_input_size':tested_hefs.get(record['sha256'],{}).get('input_size')}
                for record in records]
    detector = {'path': None, 'mtime': None, 'model': None}

    @app.before_request
    def local_only():
        host = request.host.split(':')[0].lower()
        if host not in ('localhost', '127.0.0.1'):
            return jsonify(error='Use localhost or 127.0.0.1.'), 403
        if request.method != 'GET':
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                return jsonify(error='Use this local page to make changes.'), 403
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return jsonify(error='Cross-site requests are blocked.'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.errorhandler(ValueError)
    @app.errorhandler(UnidentifiedImageError)
    @app.errorhandler(Image.DecompressionBombError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(413)
    def too_large(exc):
        return jsonify(error='Upload at most 25 MB per request.'), 413

    @app.get('/')
    def index():
        return render_template('training.html', camera=camera)

    @app.post('/api/images/discard')
    def discard_many():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Send selected image IDs as an object.')
        return jsonify(deleted=dataset.remove_many(data.get('ids')))

    @app.get('/api/images')
    def images():
        return jsonify(classes=CLASSES, images=dataset.list())

    @app.get('/images/<ident>.jpg')
    def image(ident):
        if not any(row['id'] == ident for row in dataset.list()):
            return jsonify(error='Image not found.'), 404
        return send_file(dataset.images / (ident + '.jpg'), mimetype='image/jpeg')

    @app.post('/api/import')
    def upload():
        files = request.files.getlist('images')
        if not files:
            raise ValueError('Choose one or more images.')
        added, duplicates = [], 0
        for file in files:
            ident, created = dataset.add(file.read(), request.form.get('session'), request.form.get('split'))
            added.append(ident)
            duplicates += int(not created)
        return jsonify(ids=added, duplicates=duplicates)

    @app.get('/camera.jpg')
    def camera_image():
        content, sequence = fetch_camera(camera)
        return Response(content, mimetype='image/jpeg', headers={'X-Frame-Sequence': sequence})

    def proxy_camera_stream(endpoint, expected_sha=None):
        try:
            upstream = urllib.request.urlopen(camera.rstrip('/') + endpoint, timeout=8)
        except OSError as exc:
            raise ValueError('Camera stream unavailable. Check the Pi camera service.') from exc
        content_type = upstream.headers.get('Content-Type', '')
        if not content_type.startswith('multipart/x-mixed-replace'):
            upstream.close()
            raise ValueError('Update the Pi camera service to support clean streaming.')
        if expected_sha is not None and upstream.headers.get('X-Model-SHA256') != expected_sha:
            upstream.close()
            raise ValueError('The active Pi model changed before streaming. Select the active HEF and try again.')
        def chunks():
            try:
                while True:
                    chunk = upstream.read1(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()
        return Response(chunks(), content_type=content_type, headers={'X-Accel-Buffering': 'no'})

    @app.get('/camera.mjpg')
    def camera_stream():
        return proxy_camera_stream('/raw.mjpg')

    def live_hef_status():
        relative=request.args.get('weights','')
        records={r['path']:r for r in compiled_status()}
        if relative not in records:
            raise ValueError('Select a completed HEF for live Pi testing.')
        record=records[relative]
        expected=request.args.get('sha256')
        if expected is not None and expected!=record['sha256']:
            raise ValueError('The selected HEF changed. Stop and restart live testing.')
        status=camera_json(camera,'/api/status')
        if not status.get('ready'):
            raise ValueError('The Pi camera is not ready for live testing.')
        if not status.get('model_sha256'):
            raise ValueError('Update the Pi camera service to identify the active HEF for live testing.')
        if status['model_sha256']!=record['sha256']:
            raise ValueError('This HEF is not active on the Pi. Run a Full frame test and use Activate on Pi first.')
        if status.get('preview_fps_limit')==0:
            raise ValueError('Pi preview is disabled. Enable camera preview before starting live testing.')
        age=status.get('preview_age_seconds')
        if type(age) not in (int,float) or not 0 <= age < 2:
            raise ValueError('The Pi preview is stale or unavailable. Wait for fresh frames and retry.')
        return status,record

    @app.get('/api/hailo/live')
    def hailo_live_status():
        status,record=live_hef_status()
        fields=('model','model_sha256','model_input_shape','frame_size','frames','fps','capture_fps',
                'camera_fps','inference_ms','frame_age_seconds','preview_age_seconds','preview_fps_limit',
                'preview_frames','instance_id','confidence_threshold','configured_detection_mode',
                'detection_mode','inference_region','detections')
        result={key:status.get(key) for key in fields}
        return jsonify(**result,weights=record['path'],sha256=record['sha256'],backend='hailo_live',
                       geometry=relative_geometry(status.get('detections',[]),*status['frame_size']))

    @app.get('/camera/hailo.mjpg')
    def hailo_live_stream():
        _,record=live_hef_status()
        return proxy_camera_stream('/stream.mjpg',record['sha256'])

    @app.get('/api/recordings')
    def recordings():
        return jsonify(camera_json(camera, '/api/recordings'))

    @app.post('/api/recordings/<action>')
    def recording_action(action):
        if action not in ('start', 'stop'):
            return jsonify(error='Unknown recording action.'), 404
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Send recording options as an object.')
        return jsonify(camera_json(camera, '/api/recordings/' + action, data))

    @app.route('/api/video-extraction', methods=['GET', 'POST'])
    def video_extraction():
        if request.method == 'GET':
            return jsonify(extraction.status())
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Choose a recording and extraction settings.')
        return jsonify(extraction.start(data.get('id'), data.get('split', 'train'),
                                       data.get('interval', 1), data.get('max_frames', 1000)))

    @app.post('/api/capture')
    def capture():
        data = request.get_json()
        content, sequence = fetch_camera(camera)
        ident, created = dataset.add(content, data.get('session'), data.get('split'))
        return jsonify(id=ident, created=created, sequence=sequence)

    @app.post('/api/images/<ident>')
    def annotate(ident):
        data = request.get_json()
        if not isinstance(data, dict) or 'boxes' not in data:
            raise ValueError('Send boxes, or an empty list to confirm a negative image.')
        dataset.save(ident, data['boxes'])
        return jsonify(saved=True)

    @app.post('/api/images/<ident>/discard')
    def discard(ident):
        dataset.remove(ident)
        return jsonify(deleted=True)

    @app.post('/api/export')
    def export():
        options = request.get_json()
        if not isinstance(options, dict):
            raise ValueError('Send dataset preparation options as JSON.')
        folder = dataset.export(profile=options.get('preparation', 'full'))
        archive = folder.with_suffix('.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
            for file in folder.rglob('*'):
                if file.is_file():
                    # Download is portable; local data.yaml retains its absolute path.
                    if file.name == 'data.yaml':
                        config = json.loads(file.read_text())
                        # With no explicit path, Ultralytics uses the YAML's directory.
                        config.pop('path', None)
                        zipped.writestr('data.yaml', json.dumps(config, indent=2))
                    else:
                        zipped.write(file, file.relative_to(folder))
        return jsonify(path=str(folder / 'data.yaml'), download='/api/exports/' + folder.name + '.zip')

    @app.get('/api/exports/<ident>.zip')
    def download_export(ident):
        if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            return jsonify(error='Export not found.'), 404
        archive = dataset.root / 'exports' / (ident + '.zip')
        if not archive.is_file():
            return jsonify(error='Export not found.'), 404
        return send_file(archive, as_attachment=True)

    @app.get('/api/training')
    def training_status():
        status = job.status()
        return jsonify(**status, weights=sorted(job.weights()),hef_activation=True,hef_live=True,
                       compiled=compiled_status())

    @app.post('/api/hailo/activate')
    def activate_hef():
        options=request.get_json()
        if not isinstance(options,dict) or options.get('confirmed') is not True:
            raise ValueError('Explicitly confirm activation of the selected tested HEF.')
        records={r['path']:r for r in compiled_status()}
        relative=options.get('weights')
        if not isinstance(relative,str) or relative not in records:
            raise ValueError('Select a completed compiled HEF first.')
        record=records[relative]
        proof=tested_hefs.get(record['sha256'])
        if options.get('sha256') != record['sha256'] or proof is None:
            raise ValueError('Test this exact HEF on the Pi before activating it.')
        if not hef_lock.acquire(blocking=False):
            raise ValueError('Another compiled-model operation is running.')
        try:
            result=activate_on_pi(job.runs/relative,proof,host=pi_host())
            return jsonify(**result,weights=relative)
        finally:
            hef_lock.release()

    @app.post('/api/train')
    def train():
        options = request.get_json()
        if not isinstance(options, dict):
            raise ValueError('Send training options as an object.')
        with gpu_lock:
            had_model = detector['model'] is not None
            detector.update(path=None, mtime=None, model=None)
            if had_model:
                import gc
                import torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            return jsonify(job.start(dataset, options))

    @app.post('/api/train/stop')
    def stop():
        return jsonify(job.stop())

    @app.post('/api/predict')
    def predict():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Select trained weights.')
        relative = data.get('weights', '')
        available = job.weights()
        compiled = {r['path']:r for r in compiled_models(job.runs,job.name if job.status()['active'] else None)}
        if not isinstance(relative,str) or relative not in available and relative not in compiled:
            raise ValueError('Select best.pt or a completed compiled HEF from a local training run.')
        is_hef = relative in compiled
        confidence = data.get('confidence', 0.5)
        imgsz = data.get('imgsz', 640)
        preparation = data.get('preparation', 'full')
        if preparation not in ('full', 'adaptive') or not is_hef and preparation == 'adaptive' and imgsz != 640:
            raise ValueError('Choose full-frame prediction, or adaptive native tiles with 640 input.')
        if not is_hef and (type(imgsz) is not int or not 320 <= imgsz <= 1920 or imgsz % 32):
            raise ValueError('Detector input size must be 320–1920 and divisible by 32.')
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.05 <= confidence <= 0.95:
            raise ValueError('Confidence must be from 0.05 to 0.95.')
        if is_hef:
            content, sequence = prediction_source(dataset,camera,data.get('image_id'))
            if not hef_lock.acquire(blocking=False):
                raise ValueError('A compiled-model inspection is already running.')
            try:
                started = time.monotonic()
                result = inspect_on_pi(job.runs / relative,content,confidence,preparation,host=pi_host())
                record=compiled[relative]
                if result.get('model_sha256') is not None and result['model_sha256'] != record['sha256']:
                    raise ValueError('The HEF changed during inspection. Select and test it again.')
                if (preparation == 'full' and result.get('preparation') == 'full'
                        and result.get('model_sha256') == record['sha256']
                        and result.get('inspection_job')):
                    tested_hefs[record['sha256']]=dict(sha256=record['sha256'],
                        inspection_job=result['inspection_job'],input_size=result['model_input_shape'][0])
            finally:
                hef_lock.release()
            import base64
            width,height = result['frame_size']
            detections = result['detections']
            return jsonify(detections=detections,geometry=relative_geometry(detections,width,height),
                width=width,height=height,sequence=sequence,detector_input_size=result['model_input_shape'][0],
                model_input_shape=result['model_input_shape'],preparation=preparation,tiles=result['tiles'],
                elapsed_ms=result['elapsed_ms'],inference_ms=result['inference_ms'],
                round_trip_ms=round((time.monotonic()-started)*1000),backend='hailo',
                activation_ready=record['sha256'] in tested_hefs,artifact_sha256=record['sha256'],
                image='data:image/jpeg;base64,'+base64.b64encode(content).decode(),
                mode='observation_only',passage_confirmed=False)
        with gpu_lock:
            if job.status()['active']:
                raise ValueError('Stop or finish training before inference preview.')
            from ultralytics import YOLO
            path = available[relative]
            stamp = path.stat().st_mtime_ns
            if detector['path'] != path or detector['mtime'] != stamp:
                detector.update(path=path, mtime=stamp, model=YOLO(str(path)))
            model = detector['model']
            if [model.names[k] for k in sorted(model.names)] != CLASSES:
                raise ValueError('Weights must have classes plane, target in that order.')
            content, sequence = prediction_source(dataset,camera,data.get('image_id'))
            started = time.monotonic()
            with Image.open(io.BytesIO(content)) as source:
                source = source.convert('RGB')
                width, height = source.size
                regions = search_regions(width,height,640) if preparation == 'adaptive' else [(0,0,width,height)]
                detections = []
                for x,y,w,h in regions:
                    crop = source.crop((x,y,x+w,y+h))
                    if preparation == 'adaptive':
                        padded = Image.new('RGB',(640,640),(114,114,114))
                        padded.paste(crop,(0,0))
                        crop = padded
                    result = model.predict(crop, conf=confidence, imgsz=imgsz, rect=False, verbose=False)[0]
                    local = []
                    for b in result.boxes:
                        left,top,right,bottom = b.xyxy[0].tolist()
                        box = [max(0,left),max(0,top),min(w,right),min(h,bottom)]
                        if box[0] < box[2] and box[1] < box[3]:
                            local.append(dict(label=model.names[int(b.cls.item())],
                                              confidence=round(b.conf.item(),4),box=box))
                    detections.extend(translate_detections(local,(x,y,w,h)))
                if preparation == 'adaptive':
                    detections = merge_detections(detections)
            import base64
            return jsonify(detections=detections, geometry=relative_geometry(detections, width, height),
                           width=width, height=height, sequence=sequence,
                           detector_input_size=imgsz,
                           preparation=preparation, tiles=len(regions),
                           backend='yolo',
                           elapsed_ms=round((time.monotonic() - started) * 1000),
                           image='data:image/jpeg;base64,' + base64.b64encode(content).decode(),
                           mode='observation_only', passage_confirmed=False)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8010)
    parser.add_argument('--camera', default='http://192.168.137.190:8000')
    parser.add_argument('--data', type=Path, default=ROOT / 'data/plane-target')
    args = parser.parse_args()
    if urllib.parse.urlparse(args.camera).scheme not in ('http', 'https'):
        parser.error('--camera must be an HTTP(S) camera service URL.')
    app = create_app(args.data, args.camera)
    logging.basicConfig(level=logging.INFO)
    print(f'Training studio: http://localhost:{args.port}', flush=True)
    try:
        app.run(host='127.0.0.1', port=args.port, debug=False, threaded=True, use_reloader=False)
    finally:
        app.extensions['training_job'].stop()


if __name__ == '__main__':
    main()
