#!/usr/bin/env python3
"""CAM0 -> Picamera2 -> Hailo YOLOv8 -> annotated JPEG -> browser.

Camera capture, NPU inference and browser encoding each consume the latest
available frame. Slow viewers never build a queue or delay flight observations.
"""
import argparse
import hashlib
from collections import Counter, deque
import logging
import math
import multiprocessing
import queue
from pathlib import Path
import signal
import subprocess
import threading
import time
from urllib.request import urlopen

import cv2
from flask import Flask, Response, jsonify, render_template, request, send_file
from libcamera import controls
import numpy as np
from picamera2 import Picamera2
from werkzeug.serving import make_server

from pi_vision.camera.inference import HailoModel, hailo_architecture
from pi_vision.camera.monitoring import SystemMonitor
from pi_vision.camera.camera_recording import CameraRecorder
from pi_vision.camera.camera_modes import choose_sensor_mode
from pi_vision.camera.camera_state import LatestFrame
from pi_vision.shared.spatial_detection import SpatialDetector, translate_detections, search_regions

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("pi-vision")


def choose_model(architecture):
    """Use the apt-supplied HEF compiled for the actual NPU architecture."""
    suffix = {"HAILO8": "h8", "HAILO8L": "h8l"}.get(architecture)
    if suffix is None:
        raise RuntimeError(f"Unsupported NPU architecture: {architecture!r}")
    for folder in (Path('/usr/share/hailo-models'), Path('/usr/share/rpi-camera-assets')):
        for name in (f'yolov8s_{suffix}.hef', f'yolov8s_{suffix.upper()}.hef'):
            candidate = folder / name
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(f"No YOLOv8s {suffix} HEF found. Install hailo-models or pass --model.")


def letterbox(frame, shape, allow_upscale=True):
    """Resize without stretching; return RGB input and the exact padding transform."""
    height, width, channels = shape
    if channels != 3:
        raise ValueError(f"Expected a 3-channel model, got {shape}")
    source_h, source_w = frame.shape[:2]
    scale = min(width / source_w, height / source_h)
    if not allow_upscale:
        scale = min(scale, 1)
    resized_w, resized_h = round(source_w * scale), round(source_h * scale)
    left, top = (width - resized_w) // 2, (height - resized_h) // 2
    if not allow_upscale:
        # Match native training/inspection crops: pad only the right/bottom.
        left = top = 0
    canvas = np.full((height, width, 3), 114, dtype=np.uint8)
    canvas[top:top + resized_h, left:left + resized_w] = cv2.resize(frame, (resized_w, resized_h))
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB), (left, top, resized_w / source_w, resized_h / source_h)


def decode_detections(result, labels, threshold, model_shape, frame_shape, transform):
    """Hailo NMS output is per class: rows of ymin,xmin,ymax,xmax,score."""
    if isinstance(result, dict):
        if len(result) != 1:
            raise ValueError('This demo requires a YOLO HEF with one built-in NMS output.')
        result = next(iter(result.values()))
    if len(result) != len(labels):
        raise ValueError(f'Expected {len(labels)} classes, got {len(result)}; check --labels and the HEF.')
    model_h, model_w = model_shape[:2]
    frame_h, frame_w = frame_shape[:2]
    left, top, scale_x, scale_y = transform
    detections = []
    for class_id, rows in enumerate(result):
        rows = np.asarray(rows)
        if rows.size == 0:
            continue
        if rows.ndim != 2 or rows.shape[1] != 5:
            raise ValueError(f'Unexpected Hailo NMS shape {rows.shape}; expected (N, 5).')
        for ymin, xmin, ymax, xmax, score in rows:
            if not np.isfinite([ymin, xmin, ymax, xmax, score]).all() or score < threshold:
                continue
            x1 = int(np.clip((xmin * model_w - left) / scale_x, 0, frame_w - 1))
            y1 = int(np.clip((ymin * model_h - top) / scale_y, 0, frame_h - 1))
            x2 = int(np.clip((xmax * model_w - left) / scale_x, 0, frame_w - 1))
            y2 = int(np.clip((ymax * model_h - top) / scale_y, 0, frame_h - 1))
            if x2 > x1 and y2 > y1:
                detections.append({'label': labels[class_id], 'confidence': round(float(score), 3),
                                   'box': [x1, y1, x2, y2]})
    return sorted(detections, key=lambda item: item['confidence'], reverse=True)



def centered_crop(base, zoom):
    """Crop within the configured camera mode, retaining its sensor origin."""
    x, y, width, height = base
    crop_w, crop_h = max(2, int(width / zoom) // 2 * 2), max(2, int(height / zoom) // 2 * 2)
    return (x + (width - crop_w) // 2 // 2 * 2,
            y + (height - crop_h) // 2 // 2 * 2, crop_w, crop_h)


def monitor_worker(shared):
    monitor = SystemMonitor()
    next_power = 0
    while not shared.stop.wait(1):
        try:
            sample = monitor.sample()
            extra = telemetry() if time.monotonic() >= next_power else {}
            if extra:
                next_power = time.monotonic() + 5
            with shared.condition:
                shared.info.update(usage=sample, **extra)
        except Exception:
            LOG.exception('System usage sample failed')


def telemetry():
    info = {}
    try:
        info['temperature_c'] = round(int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000, 1)
        raw = subprocess.check_output(['vcgencmd', 'get_throttled'], text=True, timeout=2).strip()
        flags = int(raw.split('=')[1], 16)
        info.update(power_flags=raw, undervoltage_now=bool(flags & 1),
                    undervoltage_since_boot=bool(flags & (1 << 16)))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return info


def capture_worker(args, shared):
    """Own the camera and recording feed; never wait for NPU inference or JPEGs."""
    try:
        cameras = Picamera2.global_camera_info()
        if not 0 <= args.camera < len(cameras):
            raise RuntimeError('Camera missing. Run rpicam-hello --list-cameras.')
        camera_info = cameras[args.camera]
        with Picamera2(args.camera) as camera:
            modes = camera.sensor_modes
            with shared.condition:
                shared.sensor_modes = modes
                shared.info.update(camera=camera_info.get('Model'), camera_id=camera_info.get('Id'))
            current_size = current_mode = None
            applied = -1
            started = False
            capture_times = deque(maxlen=100)
            while not shared.stop.is_set():
                with shared.condition:
                    desired, revision = dict(shared.settings), shared.revision
                if revision != applied:
                    size = (desired['width'], desired['height'])
                    mode = choose_sensor_mode(modes, *size, desired['fps'])
                    mode_key = (tuple(mode['size']), mode['bit_depth'])
                    if size != current_size or mode_key != current_mode:
                        with shared.condition:
                            shared.info.update(state='reconfiguring', detections=[])
                            shared.jpeg = shared.raw_jpeg = None
                            shared.last_frame = 0
                            shared.capture_image = None
                        if started:
                            camera.stop()
                        config = camera.create_video_configuration(
                            main={'size': size, 'format': 'RGB888'},
                            sensor={'output_size': tuple(mode['size']), 'bit_depth': mode['bit_depth']},
                            controls={'FrameRate': desired['fps']}, buffer_count=6, queue=False)
                        camera.configure(config)
                        base_crop = tuple(camera.camera_properties.get('ScalerCropMaximum', ()))
                        with shared.condition:
                            shared.limits['zoom_supported'] = 'ScalerCrop' in camera.camera_controls and len(base_crop) == 4
                            shared.info['sensor_mode'] = dict(size=list(mode['size']), max_fps=mode['fps'])
                            shared.frame_size = size
                        if 'AfMode' in camera.camera_controls:
                            camera.set_controls({'AfMode': controls.AfModeEnum.Continuous})
                        camera.start()
                        started = True
                        current_size, current_mode = size, mode_key
                    updates = {'FrameRate': desired['fps']}
                    if shared.limits['zoom_supported']:
                        updates['ScalerCrop'] = centered_crop(base_crop, desired['zoom'])
                    camera.set_controls(updates)
                    applied = revision
                    with shared.condition:
                        shared.applied_revision = revision
                    capture_times.clear()
                captured = camera.capture_request()
                try:
                    frame = captured.make_array('main').copy()
                    metadata = captured.get_metadata()
                finally:
                    captured.release()
                now = time.monotonic()
                stamp = metadata.get('SensorTimestamp')
                age = max(0, time.clock_gettime(time.CLOCK_BOOTTIME) - stamp / 1e9) if stamp is not None else 0
                capture_times.append(now)
                capture_fps = ((len(capture_times) - 1) / (capture_times[-1] - capture_times[0])
                               if len(capture_times) > 1 else 0)
                # This array is immutable after publication. Annotation uses its own copy.
                shared.recorder.submit(frame, shared.capture_sequence + 1, copy=False)
                with shared.condition:
                    shared.capture_image = frame
                    shared.capture_sequence += 1
                    shared.capture_time = now - age
                    shared.capture_revision = applied
                    shared.info.update(camera_fps=round(1_000_000 / metadata['FrameDuration'], 1),
                                       capture_fps=round(capture_fps, 1), crop=list(metadata.get('ScalerCrop', [])))
                    shared.condition.notify_all()
    except Exception as exc:
        LOG.exception('Camera worker stopped')
        with shared.condition:
            shared.info.update(state='error', error=str(exc))
        shared.stop.set()
    finally:
        shared.recorder.stop()
        with shared.condition:
            shared.condition.notify_all()


def inference_process(args, inputs, outputs, stopped):
    """Hailo calls may hold the GIL; isolate them from camera/recording threads."""
    try:
        architecture = hailo_architecture()
        model = Path(args.model).resolve() if args.model else choose_model(architecture)
        labels = Path(args.labels).read_text().splitlines()
        if not labels or any(not label.strip() for label in labels):
            raise ValueError('Labels must contain one non-empty label per class.')
        with model.open('rb') as stream:
            model_sha256 = hashlib.file_digest(stream,'sha256').hexdigest()
        with HailoModel(str(model)) as hailo:
            shape = hailo.get_input_shape()
            with model.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=model_sha256:
                    raise ValueError('The HEF changed while loading. Restart with a completed model file.')
            outputs.put(dict(kind='ready', architecture=architecture, model=model.name,
                             labels=labels, model_input_shape=list(shape),model_sha256=model_sha256))
            if args.detection_mode == 'adaptive' and (shape[0] != 640 or shape[1] != 640):
                raise ValueError('Adaptive native-pixel tracking requires a 640x640 HEF.')
            while not stopped.is_set():
                try:
                    frame = inputs.get(timeout=.2)
                except queue.Empty:
                    continue
                input_rgb, transform = letterbox(frame, shape, args.detection_mode != 'adaptive')
                began = time.monotonic()
                result = hailo.run(input_rgb)
                inference_ms = (time.monotonic() - began) * 1000
                detections = decode_detections(result, labels, args.threshold, shape, frame.shape, transform)
                outputs.put(dict(kind='frame', detections=detections,
                                 inference_ms=round(inference_ms, 1)), timeout=1)
    except Exception as exc:
        LOG.exception('Inference process stopped')
        try:
            outputs.put(dict(kind='error', error=str(exc)), timeout=1)
        except queue.Full:
            pass


def inference_worker(args, shared):
    context = multiprocessing.get_context('spawn')
    inputs, outputs = context.Queue(maxsize=1), context.Queue(maxsize=2)
    stopped = context.Event()
    process = context.Process(target=inference_process, args=(args, inputs, outputs, stopped), daemon=True)
    process.start()
    try:
        def receive():
            while not shared.stop.is_set():
                try:
                    result = outputs.get(timeout=.25)
                    if result['kind'] == 'error':
                        raise RuntimeError(result['error'])
                    return result
                except queue.Empty:
                    if not process.is_alive():
                        raise RuntimeError('Inference process exited unexpectedly.')
            return None
        ready = receive()
        if ready is None:
            return
        with shared.condition:
            shared.info.update({k:v for k,v in ready.items() if k != 'kind'})
        last_sequence = -1
        spatial = SpatialDetector()
        previous = time.monotonic()
        fps = 0
        while not shared.stop.is_set():
            with shared.condition:
                shared.condition.wait_for(lambda: shared.stop.is_set() or
                    shared.capture_image is not None and shared.capture_sequence != last_sequence, timeout=1)
                if shared.stop.is_set():
                    return
                if shared.capture_image is None or shared.capture_sequence == last_sequence:
                    continue
                frame = shared.capture_image
                sequence = last_sequence = shared.capture_sequence
                captured_at = shared.capture_time
                revision = shared.capture_revision
            # At most one in-flight inference. After it returns, fetch the newest capture.
            height, width = frame.shape[:2]
            region, mode = (spatial.region(width, height, captured_at, revision)
                            if args.detection_mode == 'adaptive' else ((0, 0, width, height), 'full'))
            x, y, w, h = region
            inputs.put(frame[y:y+h, x:x+w].copy(), timeout=1)
            result = receive()
            if result is None:
                return
            now = time.monotonic()
            fps = 1 / max(now - previous, .001) if not fps else fps * .9 + .1 / max(now - previous, .001)
            previous = now
            with shared.condition:
                if revision != shared.revision:
                    continue
                shared.last_frame = captured_at
                shared.sequence = sequence
                detections = translate_detections(result['detections'], region)
                if args.detection_mode == 'adaptive':
                    confirmed = spatial.observe(detections, captured_at)
                    if not confirmed:
                        detections = [d for d in detections if d['label'] != 'plane']
                shared.preview_job = (frame, detections, region, mode, sequence, captured_at, revision)
                shared.info.update(state='running', fps=round(fps, 1), inference_ms=result['inference_ms'],
                                   detections=detections, counts=dict(Counter(d['label'] for d in detections)),
                                   detection_mode=mode, inference_region=list(region),
                                   search_tiles=len(search_regions(width, height, 640)) if mode != 'full' else 1)
                shared.condition.notify_all()
    except Exception as exc:
        LOG.exception('Inference worker stopped')
        with shared.condition:
            shared.info.update(state='error', error=str(exc))
        shared.stop.set()
        with shared.condition:
            shared.condition.notify_all()
    finally:
        stopped.set()
        process.join(timeout=3)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        for channel in (inputs, outputs):
            channel.cancel_join_thread()
            channel.close()


def preview_worker(args, shared):
    """Encode a bounded latest-only preview, outside the controller result path."""
    if args.preview_fps == 0:
        return
    previous = 0
    while not shared.stop.is_set():
        with shared.condition:
            shared.condition.wait_for(lambda: shared.stop.is_set() or shared.preview_job is not None, timeout=1)
            if shared.stop.is_set():
                return
            job = shared.preview_job
            shared.preview_job = None
        if job is None:
            continue
        if shared.stop.wait(max(0, 1 / args.preview_fps - (time.monotonic() - previous))):
            return
        # Replace a superseded job after rate limiting, retaining at most one.
        with shared.condition:
            if shared.preview_job is not None:
                job, shared.preview_job = shared.preview_job, None
        frame, detections, region, mode, sequence, captured_at, revision = job
        try:
            raw_ok, raw = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
            annotated = frame.copy()
            if mode != 'full':
                x, y, w, h = region
                cv2.rectangle(annotated, (x, y), (x+w-1, y+h-1), (240, 180, 70), 1)
            for detection in detections:
                x1, y1, x2, y2 = detection['box']
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (90, 235, 120), 2)
                cv2.putText(annotated, f"{detection['label']} {detection['confidence']:.0%}",
                            (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, .6, (90, 235, 120), 2)
            ok, jpeg = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
            if not raw_ok or not ok:
                raise RuntimeError('JPEG encoding failed')
            with shared.condition:
                if revision == shared.revision:
                    shared.jpeg, shared.raw_jpeg = jpeg.tobytes(), raw.tobytes()
                    shared.preview_sequence, shared.preview_time = sequence, captured_at
                    shared.condition.notify_all()
        except Exception:
            LOG.exception('Preview encoding failed')
        previous = time.monotonic()


def create_app(shared):
    app = Flask(__name__, root_path=str(Path(__file__).resolve().parent))

    @app.get('/static/recording.js')
    def recording_script():
        return send_file(Path(__file__).resolve().parents[1] / 'shared/static/recording.js', mimetype='text/javascript')


    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/camera-preview.js')
    def preview_script():
        return send_file(Path(__file__).resolve().parents[1] / 'shared/static/camera-preview.js', mimetype='text/javascript')

    @app.get('/api/status')
    def status():
        return jsonify(shared.status())

    @app.route('/api/settings', methods=['GET', 'POST'])
    def settings():
        if request.method == 'GET':
            return jsonify(shared.settings_status())
        if not request.is_json:
            return jsonify(error='Use application/json.'), 415
        if not shared.status()['ready']:
            return jsonify(error='Wait for the camera to be ready.'), 503
        try:
            return jsonify(shared.change_settings(request.get_json())), 202
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/healthz')
    def health():
        info = shared.status()
        return jsonify(info), 200 if info['ready'] else 503

    @app.get('/api/recordings')
    def recordings():
        return jsonify(current=shared.recorder.status(), recordings=shared.recorder.list(), capture=shared.settings_status())

    @app.post('/api/recordings/<action>')
    def recording_action(action):
        if request.headers.get('Origin') not in (None, request.host_url.rstrip('/')) or not request.is_json:
            return jsonify(error='Use same-origin JSON requests.'), 403
        try:
            if action == 'stop':
                return jsonify(shared.recorder.stop())
            if action != 'start':
                return jsonify(error='Unknown recording action.'), 404
            options = request.get_json()
            if not isinstance(options, dict):
                raise ValueError('Send recording options as an object.')
            if not shared.status()['ready']:
                return jsonify(error='Camera is not ready.'), 503
            return jsonify(shared.start_recording(options))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/api/recordings/<ident>.<suffix>')
    def recording_file(ident, suffix):
        try:
            return send_file(shared.recorder.file(ident, suffix), as_attachment=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/snapshot.jpg')
    def snapshot():
        after = request.args.get('after', default=None, type=int)
        with shared.condition:
            if after is not None:
                shared.condition.wait_for(lambda: shared.preview_sequence != after or shared.stop.is_set(), timeout=2)
            if not shared.status()['ready'] or shared.jpeg is None or time.monotonic() - shared.preview_time > 5:
                return Response('Camera is not ready\n', status=503)
            jpeg, sequence = shared.jpeg, shared.preview_sequence
            headers = {'X-Frame-Sequence': str(sequence),
                       'X-Frame-Age': str(time.monotonic() - shared.preview_time)}
        if request.args.get('noise') == '1':
            try:
                url = f'http://127.0.0.1:8001/api/autonomous/preview-noise?frame={sequence}'
                with urlopen(url, timeout=.15) as result:
                    for key in ('X-Stationary-Timeout', 'X-Noise-Boxes'):
                        if key in result.headers:
                            headers[key] = result.headers[key]
            except OSError:
                pass  # The camera preview also works without the optional controller service.
        return Response(jpeg, mimetype='image/jpeg', headers=headers)

    @app.get('/raw.jpg')
    def raw_snapshot():
        with shared.condition:
            if not shared.status()['ready'] or shared.raw_jpeg is None or time.monotonic() - shared.preview_time > 5:
                return Response('Camera is not ready\n', status=503)
            return Response(shared.raw_jpeg, mimetype='image/jpeg', headers={
                'X-Frame-Sequence': str(shared.preview_sequence),
                'X-Frame-Age': str(time.monotonic() - shared.preview_time)})

    @app.get('/raw.mjpg')
    @app.get('/stream.mjpg')
    def stream():
        clean = request.path == '/raw.mjpg'
        def frames():
            sequence = -1
            while not shared.stop.is_set():
                with shared.condition:
                    updated = shared.condition.wait_for(
                        lambda: shared.preview_sequence != sequence or shared.stop.is_set(), timeout=5)
                    if shared.stop.is_set() or not updated:
                        return
                    if shared.jpeg is None:
                        sequence = shared.preview_sequence
                        continue
                    sequence, jpeg = shared.preview_sequence, shared.raw_jpeg if clean else shared.jpeg
                    if jpeg is None:
                        continue
                yield (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                       str(len(jpeg)).encode() + b'\r\nX-Frame-Sequence: ' +
                       str(sequence).encode() + b'\r\n\r\n' + jpeg + b'\r\n')
        return Response(frames(), mimetype='multipart/x-mixed-replace; boundary=frame',headers={
            'X-Model-SHA256':shared.info.get('model_sha256',''),
            'X-Camera-Instance':shared.info['instance_id']})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--model', help='Override the architecture-specific YOLOv8s HEF path')
    parser.add_argument('--labels', default=str(ROOT / 'config/labels.txt'), help='One label per model class')
    parser.add_argument('--fps', type=int, default=15)
    parser.add_argument('--width', type=int, default=960)
    parser.add_argument('--height', type=int, default=540)
    parser.add_argument('--threshold', type=float, default=0.5)
    parser.add_argument('--quality', type=int, default=80)
    parser.add_argument('--detection-mode', choices=('full', 'adaptive'), default='full',
                        help='Adaptive is experimental and requires crop-trained, validated 640 weights.')
    parser.add_argument('--preview-fps', type=int, default=60, help='JPEG limit independent of detection rate (0-60; 0 disables preview).')
    args = parser.parse_args()
    if not (1 <= args.port <= 65535 and 1 <= args.fps <= 60 and
            args.width >= 64 and args.width % 2 == 0 and args.height >= 64 and args.height % 2 == 0 and
            0 < args.threshold <= 1 and 1 <= args.quality <= 100 and 0 <= args.preview_fps <= 60):
        parser.error('Check port, fps (1-60), even dimensions (>=64), threshold (0-1), and quality (1-100).')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    shared = LatestFrame(args)
    shared.info.update(configured_detection_mode=args.detection_mode, preview_fps_limit=args.preview_fps,
                       confidence_threshold=args.threshold)

    def stop(signum, frame):
        shared.stop.set()
        with shared.condition:
            shared.condition.notify_all()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Bind before opening hardware, so a second instance fails without touching CAM0.
    server = make_server(args.host, args.port, create_app(shared), threaded=True)
    server.timeout = 1
    worker = threading.Thread(target=capture_worker, args=(args, shared), daemon=True)
    detector = threading.Thread(target=inference_worker, args=(args, shared), daemon=True)
    preview = threading.Thread(target=preview_worker, args=(args, shared), daemon=True)
    monitor = threading.Thread(target=monitor_worker, args=(shared,), daemon=True)
    monitor.start()
    worker.start()
    detector.start()
    preview.start()
    LOG.info('Browser view: http://%s:%d', args.host, args.port)
    try:
        while not shared.stop.is_set():
            server.handle_request()
    finally:
        shared.stop.set()
        with shared.condition:
            shared.condition.notify_all()
        server.server_close()
        worker.join(timeout=15)
        detector.join(timeout=15)
        preview.join(timeout=3)
        shared.recorder.stop()
        monitor.join(timeout=3)
    return 1 if shared.info['error'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
