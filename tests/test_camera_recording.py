import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pi_vision.camera.camera_state as camera_state
from pi_vision.camera.camera_recording import CameraRecorder
from pi_vision.studio.training_data import Dataset
from pi_vision.studio.video_collection import VideoExtraction
from pi_vision.studio.import_video import extract_video

ROOT = Path(__file__).resolve().parents[1]


def fake_camera_app(folder):
    # Import the real camera web API, substituting only hardware-only imports.
    spec = importlib.util.spec_from_file_location('camera_app_test', ROOT / 'pi_vision/camera/app.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'libcamera': SimpleNamespace(controls=None),
                                 'picamera2': SimpleNamespace(Picamera2=None),
                                 'pi_vision.camera.inference': SimpleNamespace(HailoModel=None, hailo_architecture=None),
                                 'pi_vision.camera.monitoring': SimpleNamespace(SystemMonitor=None)}):
        spec.loader.exec_module(module)
    module.ROOT = Path(folder)
    with patch('pi_vision.camera.camera_state.ROOT', Path(folder)):
        shared = module.LatestFrame(SimpleNamespace(width=160, height=96, camera=0, fps=15))
    # Import camera_state before patch.dict(sys.modules): otherwise its cleanup
    # removes the newly imported module and a later patch targets another copy.
    assert shared.recorder.root == Path(folder)/'data/recordings'
    assert shared.settings_path == Path(folder)/'camera-settings.json'
    frame = np.full((96, 160, 3), 100, dtype=np.uint8)
    shared.jpeg = shared.raw_jpeg = cv2.imencode('.jpg', frame)[1].tobytes()
    shared.last_frame = time.monotonic()
    shared.info['state'] = 'running'
    shared.info['camera_fps'] = 15
    shared.applied_revision = shared.revision
    shared.sequence = 1
    shared.preview_sequence = 1
    shared.preview_time = shared.last_frame
    app = module.create_app(shared)
    app.extensions['camera_module'] = module
    app.testing = True
    return app, shared


class CameraRecordingTests(unittest.TestCase):
    def test_preview_headers_use_encoded_frame_not_newer_detection(self):
        self.shared.sequence = 50
        self.shared.preview_sequence = 4
        self.shared.preview_time = time.monotonic() - .3
        for path in ('/snapshot.jpg', '/raw.jpg'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['X-Frame-Sequence'], '4')
            self.assertAlmostEqual(float(response.headers['X-Frame-Age']), .3, places=2)
        self.shared.preview_time = time.monotonic() - 6
        self.assertEqual(self.client.get('/raw.jpg').status_code, 503)

    def test_live_view_requests_noise_for_the_encoded_frame_only(self):
        self.shared.sequence = 50
        self.shared.preview_sequence = 4
        module = self.app.extensions['camera_module']
        with patch.object(module, 'urlopen') as upstream:
            upstream.return_value.__enter__.return_value.headers = {
                'X-Stationary-Timeout': '0.6', 'X-Noise-Boxes': '[[0.1,0.2,0.3,0.4]]'}
            self.client.get('/snapshot.jpg')
            upstream.assert_not_called()
            response = self.client.get('/snapshot.jpg?noise=1')
            upstream.assert_called_once_with('http://127.0.0.1:8001/api/autonomous/preview-noise?frame=4', timeout=.15)
            self.assertEqual(response.headers['X-Stationary-Timeout'], '0.6')
            self.assertEqual(json.loads(response.headers['X-Noise-Boxes']), [[.1, .2, .3, .4]])
            upstream.side_effect = OSError
            response = self.client.get('/snapshot.jpg?noise=1')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('X-Noise-Boxes', response.headers)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.app, self.shared = fake_camera_app(self.folder)
        self.recorder = self.shared.recorder
        self.client = self.app.test_client()

    def tearDown(self):
        self.shared.stop.set()
        self.recorder.stop()
        self.temp.cleanup()

    def frames(self, count=10):
        for i in range(count):
            frame = np.full((96, 160, 3), 20 + i * 10, dtype=np.uint8)
            self.recorder.submit(frame, i + 1)
            frame[:] = 255  # Changes after submit must not leak into the clean clip.

    def test_start_record_stop_metadata_and_readable_clean_avi(self):
        result = self.client.post('/api/recordings/start', json={'seconds': 10})
        self.assertEqual(result.status_code, 200)
        ident = result.json['id']
        self.assertTrue(self.client.get('/api/status').json['ready'])
        self.assertEqual(self.client.post('/api/settings', json={'fps': 20}).status_code, 400)
        self.assertEqual(self.client.get('/api/recordings/' + ident + '.avi').status_code, 400)
        self.frames()
        result = self.client.post('/api/recordings/stop', json={})
        self.assertEqual(result.json['state'], 'completed')
        self.assertEqual(result.json['frames'], 10)
        self.assertEqual(result.json['dropped'], 0)
        response = self.client.get('/api/recordings/' + ident + '.json')
        metadata = json.loads(response.data)
        response.close()
        self.assertEqual(len(metadata['frame_times']), 10)
        cap = cv2.VideoCapture(str(self.recorder.file(ident, 'avi')))
        try:
            ok, first = cap.read()
            self.assertTrue(ok)
            self.assertLess(first.mean(), 30)
            self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 10)
        finally:
            cap.release()
        response = self.client.get('/api/recordings/' + ident + '.avi')
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_duration_limit_and_stale_camera_stop_without_browser(self):
        self.recorder.start(15, (160, 96), seconds=1)
        self.frames(1)
        self.recorder.thread.join(timeout=3)
        self.assertFalse(self.recorder.status()['active'])
        self.assertEqual(self.recorder.status()['state'], 'completed')

    def test_size_limit_finishes_a_readable_clip(self):
        with patch.object(CameraRecorder,'FILE_LIMIT',1024):
            self.recorder.start(50,(160,96),seconds=60)
        for i in range(40):
            self.recorder.submit(np.full((96,160,3),i,np.uint8),i)
            time.sleep(.003)
        self.recorder.thread.join(timeout=3)
        status=self.recorder.status()
        self.assertFalse(status['active'])
        self.assertEqual(status['state'],'completed')
        self.assertIn('File size limit',status['stop_reason'])
        reader=cv2.VideoCapture(str(self.recorder.file(status['id'],'avi')))
        self.assertTrue(reader.read()[0])
        reader.release()

    def test_clean_stream_and_origin_validation(self):
        self.shared.info['model_sha256']='a'*64
        response = self.client.get('/raw.mjpg', buffered=False)
        self.assertEqual(response.headers['X-Model-SHA256'],'a'*64)
        chunk = next(response.response)
        self.assertIn(b'X-Frame-Sequence: 1', chunk)
        self.assertIn(self.shared.raw_jpeg, chunk)
        response.close()
        for value in (True, 0, 301, '60', float('nan')):
            self.assertEqual(self.client.post('/api/recordings/start', json={'seconds': value}).status_code, 400)
        self.assertEqual(self.client.post('/api/recordings/start', json={}, headers={'Origin': 'https://elsewhere.test'}).status_code, 403)
        self.assertEqual(self.client.get('/api/recordings/../../secret.avi').status_code, 404)

    def test_restart_marks_unclosed_recording_interrupted(self):
        ident = 'a' * 32
        path = self.recorder.root / (ident + '.json')
        path.write_text(json.dumps(dict(id=ident, state='recording')))
        recovered = CameraRecorder(self.recorder.root)
        self.assertEqual(recovered.list()[0]['state'], 'interrupted')

    def test_background_download_extract_retries_and_split_protection(self):
        video = self.folder / 'source.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 5, (160, 96))
        self.assertTrue(writer.isOpened())
        for i in range(10):
            writer.write(np.full((96, 160, 3), i * 20, np.uint8))
        writer.release()
        content = video.read_bytes()
        ident = 'b' * 32
        metadata = dict(id=ident, state='completed', frames=10, bytes=len(content), frame_times=[[i, i / 5] for i in range(10)])
        dataset = Dataset(self.folder / 'library')
        extraction = VideoExtraction(dataset, 'http://fake-camera')
        with patch('pi_vision.studio.video_collection.camera_json', return_value=metadata), patch('pi_vision.studio.video_collection.urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(content)):
            extraction.start(ident, 'train', interval=.4)
            extraction.thread.join(timeout=10)
            self.assertEqual(extraction.status()['state'], 'completed', extraction.status())
            self.assertEqual(extraction.status()['added'], 5)
            extraction.start(ident, 'train', interval=.4)
            extraction.thread.join(timeout=10)
            self.assertEqual(extraction.status()['added'], 0)
        with self.assertRaisesRegex(ValueError, 'another split'):
            extraction.start(ident, 'val')
        self.assertTrue(all(not row['reviewed'] for row in dataset.list()))
        self.assertTrue((dataset.root / 'videos' / (ident + '.avi')).is_file())

    def test_high_rate_extraction_counts(self):
        video = self.folder / 'rates.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 15, (160, 96))
        self.assertTrue(writer.isOpened())
        for i in range(30):
            writer.write(np.full((96, 160, 3), i * 7, np.uint8))
        writer.release()
        content = video.read_bytes()
        metadata = dict(state='completed', frames=30, bytes=len(content),
                        frame_times=[[i, .02 + i / 15] for i in range(30)])
        for rate in (5, 10, 15):
            with self.subTest(rate=rate):
                dataset = Dataset(self.folder / f'library-{rate}')
                extraction = VideoExtraction(dataset, 'http://fake-camera')
                with patch('pi_vision.studio.video_collection.camera_json', return_value=metadata), patch(
                        'pi_vision.studio.video_collection.urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(content)):
                    extraction.start('c' * 32, 'train', interval=1 / rate)
                    extraction.thread.join(timeout=10)
                status = extraction.status()
                self.assertEqual(status['state'], 'completed', status)
                self.assertEqual(status['sampled'], 2 * rate)
                self.assertEqual(status['added'], 2 * rate)

    def test_failed_video_and_extraction_options(self):
        dataset = Dataset(self.folder / 'library')
        with self.assertRaises(ValueError):
            extract_video(self.folder / 'missing.mp4', dataset, 'clip', 'train')
        extraction = VideoExtraction(dataset, 'http://fake-camera')
        for options in [dict(ident='bad', split='train'), dict(ident='a'*32, split='bad'),
                        dict(ident='a'*32, split='train', interval=True), dict(ident='a'*32, split='train', max_frames=True)]:
            with self.assertRaises(ValueError):
                extraction.start(**options)


if __name__ == '__main__':
    unittest.main()
