import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from pi_vision.studio.training_studio import create_app


class HailoLiveTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        root=Path(self.directory.name)
        self.model=root/'runs'/'test'/'compiled'/'model.hef'
        self.model.parent.mkdir(parents=True)
        self.model.write_bytes(b'live-model-test')
        self.sha=hashlib.sha256(self.model.read_bytes()).hexdigest()
        self.app=create_app(root/'data',camera='http://127.0.0.1:1',runs=root/'runs')
        self.client=self.app.test_client()
        self.query='weights=test/compiled/model.hef&sha256='+self.sha
        self.status=dict(ready=True,model='active.hef',model_sha256=self.sha,
            model_input_shape=[640,640,3],frame_size=[1920,1080],detections=[],
            preview_fps_limit=15,preview_age_seconds=.1,instance_id='one',fps=35,
            frame_age_seconds=.08,confidence_threshold=.55)

    def tearDown(self):
        self.directory.cleanup()

    def test_live_status_uses_active_pi_results_without_inspection_or_activation(self):
        with patch('pi_vision.studio.training_studio.camera_json',return_value=self.status), \
             patch('pi_vision.studio.training_studio.inspect_on_pi') as inspect, \
             patch('pi_vision.studio.training_studio.activate_on_pi') as activate:
            response=self.client.get('/api/hailo/live?'+self.query)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['backend'],'hailo_live')
        self.assertEqual(response.json['fps'],35)
        self.assertEqual(response.json['confidence_threshold'],.55)
        inspect.assert_not_called();activate.assert_not_called()

    def test_wrong_model_stale_disabled_or_unsupported_preview_is_refused(self):
        for changes in (dict(model_sha256='f'*64),dict(model_sha256=None),dict(ready=False),
                        dict(preview_fps_limit=0),dict(preview_age_seconds=3)):
            with self.subTest(changes=changes),patch('pi_vision.studio.training_studio.camera_json',return_value={**self.status,**changes}):
                response=self.client.get('/api/hailo/live?'+self.query)
                self.assertEqual(response.status_code,400,response.json)

    def test_changed_local_hef_is_refused(self):
        self.model.write_bytes(b'replaced-model')
        with patch('pi_vision.studio.training_studio.camera_json') as camera:
            response=self.client.get('/api/hailo/live?'+self.query)
            self.assertEqual(response.status_code,400)
            camera.assert_not_called()

    def test_live_stream_proxies_annotated_pi_frames_and_checks_response_hash(self):
        upstream=MagicMock()
        upstream.headers={'Content-Type':'multipart/x-mixed-replace; boundary=frame','X-Model-SHA256':self.sha}
        upstream.read1.side_effect=[b'--frame\r\npi-annotated-jpeg',b'']
        with patch('pi_vision.studio.training_studio.camera_json',return_value=self.status), \
             patch('pi_vision.studio.training_studio.urllib.request.urlopen',return_value=upstream) as fetch:
            response=self.client.get('/camera/hailo.mjpg?'+self.query)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'pi-annotated-jpeg',response.data)
            self.assertEqual(fetch.call_args.args[0],'http://127.0.0.1:1/stream.mjpg')
        upstream.close.assert_called_once()

    def test_model_change_between_status_and_stream_does_not_display_wrong_model(self):
        upstream=MagicMock()
        upstream.headers={'Content-Type':'multipart/x-mixed-replace; boundary=frame','X-Model-SHA256':'f'*64}
        with patch('pi_vision.studio.training_studio.camera_json',return_value=self.status), \
             patch('pi_vision.studio.training_studio.urllib.request.urlopen',return_value=upstream):
            response=self.client.get('/camera/hailo.mjpg?'+self.query)
            self.assertEqual(response.status_code,400)
        upstream.close.assert_called_once()
        upstream.read1.assert_not_called()
