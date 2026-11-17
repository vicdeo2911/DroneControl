"""Shared camera settings and latest-frame slots, with no camera/NPU imports."""
import json
import math
from pathlib import Path
import threading
import time
import uuid

from pi_vision.camera.camera_recording import CameraRecorder, validate_duration
from pi_vision.camera.camera_modes import RESOLUTIONS, choose_sensor_mode, validate_capture
from pi_vision.shared.state_store import save_json
from pi_vision.shared.camera_operation import operation_guard

ROOT = Path(__file__).resolve().parents[2]


class LatestFrame:
    def __init__(self, args):
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.jpeg = None
        self.raw_jpeg = None
        self.frame_size = (args.width, args.height)
        self.recorder = CameraRecorder(ROOT / 'data/recordings')
        self.last_frame = 0.0
        self.sequence = 0
        self.preview_sequence = 0
        self.preview_time = 0
        self.preview_job = None
        self.capture_sequence = 0
        self.capture_image = None
        self.capture_time = 0
        self.capture_revision = -1
        self.sensor_modes = []
        self.settings_path = ROOT / 'camera-settings.json'
        self.settings = {'fps': getattr(args, 'fps', 15), 'zoom': 1.0, 'width': args.width, 'height': args.height}
        if self.settings_path.exists():
            self.settings.update(json.loads(self.settings_path.read_text()))
        validate_capture(self.settings['width'], self.settings['height'], self.settings['fps'])
        self.frame_size = (self.settings['width'], self.settings['height'])
        self.revision = 0
        self.applied_revision = -1
        self.limits = {'fps_min': 1, 'fps_max': 60, 'zoom_min': 1.0,
                       'zoom_max': 4.0, 'zoom_supported': False}
        self.info = {'state': 'starting', 'instance_id':uuid.uuid4().hex, 'camera_index': args.camera, 'fps': 0,
                     'inference_ms': 0, 'detections': [], 'error': None}

    def settings_status(self):
        with self.condition:
            return {'settings': dict(self.settings), 'revision': self.revision,
                    'applied_revision': self.applied_revision, 'limits': dict(self.limits),
                    'resolutions': [dict(width=w, height=h, label=f'{w} x {h}') for w, h in RESOLUTIONS]}

    def change_settings(self, changes):
        if not isinstance(changes, dict) or not changes or changes.keys() - {'fps', 'zoom', 'width', 'height'}:
            raise ValueError('Send fps, zoom and/or width/height as a JSON object.')
        with self.condition:
            if self.recorder.status()['active']:
                raise ValueError('Stop the recording before changing camera rate or zoom.')
            for name, value in changes.items():
                if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                        isinstance(value, float) and not math.isfinite(value)):
                    raise ValueError(f'{name} must be a finite number.')
                if name == 'fps' and (not 1 <= value <= self.limits['fps_max'] or int(value) != value):
                    raise ValueError(f"fps must be a whole number from 1 to {self.limits['fps_max']}.")
                if name == 'zoom' and (not self.limits['zoom_supported'] or not 1 <= value <= 4):
                    raise ValueError('Digital zoom must be from 1 to 4 and supported by the camera.')
            desired = {**self.settings, **changes}
            validate_capture(desired['width'], desired['height'], desired['fps'])
            if self.sensor_modes:
                choose_sensor_mode(self.sensor_modes, desired['width'], desired['height'], desired['fps'])
            save_json(self.settings_path, desired, indent=2)
            self.settings = desired
            self.revision += 1
            return self.settings_status()

    def start_recording(self, options):
        with operation_guard():
            return self._start_recording(options)

    def _start_recording(self, options):
        validate_duration(options.get('seconds', 60))
        changes = {}
        if 'resolution' in options:
            choices = {f'{w}x{h}': (w, h) for w, h in RESOLUTIONS}
            resolution = options['resolution']
            if not isinstance(resolution, str) or resolution not in choices:
                raise ValueError('Choose 960x540, 1280x720 or 1920x1080 recording resolution.')
            changes['width'], changes['height'] = choices[resolution]
        if 'fps' in options:
            changes['fps'] = options['fps']
        with self.condition:
            if self.recorder.status()['active']:
                raise ValueError('A clip is already recording.')
            if any(self.settings.get(k) != v for k, v in changes.items()):
                self.change_settings(changes)
            expected_revision = self.revision
            ready = self.condition.wait_for(lambda: self.stop.is_set() or (
                self.applied_revision >= expected_revision and self.status()['ready'] and
                abs(self.info.get('camera_fps', 0) - self.settings['fps']) < 1), timeout=10)
            if not ready or self.stop.is_set():
                raise ValueError('Camera did not reach the selected recording mode. Check camera status.')
            return self.recorder.start(self.settings['fps'], self.frame_size, options.get('seconds', 60))

    def status(self):
        with self.condition:
            age = time.monotonic() - self.last_frame if self.last_frame else None
            ready = age is not None and age < 5 and self.info['error'] is None
            info = dict(self.info)
            if 'usage' in info and time.time() - info['usage']['sampled_at'] > 5:
                info['usage'] = {**info['usage'], 'cpu_percent': None, 'gpu_percent': None,
                                 'npu_percent': None, 'process_cpu_percent': None,
                                 'memory_percent': None, 'cpu_cores': {}, 'gpu_engines': {}}
            return {**info, 'ready': ready, 'frames': self.sequence,
                    'preview_frames':self.preview_sequence,
                    'preview_age_seconds':round(time.monotonic()-self.preview_time,2) if self.preview_time else None,
                    'frame_size': list(self.frame_size),
                    **self.settings_status(),
                    'frame_age_seconds': round(age, 2) if age is not None else None}

