import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
import threading

from remote_control import Remote, control_worker, handler_for
from test_remote_control import FakeBus


class FeedbackTests(unittest.TestCase):
    def test_worker_subtracts_request_time_from_feedback_period(self):
        remote, done = MagicMock(), MagicMock()
        remote.autonomy.active = True
        remote.autonomy.config = {'feedback_hz': 40}
        done.is_set.side_effect = [False, True]
        with patch('remote_control.camera_status', return_value={}), patch('remote_control.time.monotonic', side_effect=[1, 1.006]):
            control_worker(remote, done)
        self.assertAlmostEqual(done.wait.call_args.args[0], .019)

    def setUp(self):
        self.remote = Remote(FakeBus())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.remote))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, options):
        req = Request(self.base + '/api/autonomous/camera', data=json.dumps({'version': 2, **options}).encode(),
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=2) as response: return json.load(response)

    def test_camera_fps_validation_and_active_session_guard(self):
        with patch('remote_control.urllib.request.urlopen') as upstream:
            for value in (0, 61, 30.5, True):
                with self.assertRaises(HTTPError): self.post({'fps': value})
            self.remote.autonomy.active = True
            with self.assertRaises(HTTPError): self.post({'fps': 30})
            upstream.assert_not_called()

    def test_camera_fps_forwards_only_the_rate_to_fixed_loopback(self):
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"settings":{"fps":30}}')
        with patch('remote_control.urllib.request.urlopen', return_value=response) as upstream:
            self.assertEqual(self.post({'fps': 30, 'url': 'http://example.com'})['settings']['fps'], 30)
        request = upstream.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8000/api/settings')
        self.assertEqual(json.loads(request.data), {'fps': 30})

    def test_preview_preserves_sequence_and_age_without_dac_writes(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {'Content-Type': 'image/jpeg', 'X-Frame-Sequence': '12', 'X-Frame-Age': '0.06'}
        response.read.return_value = b'jpeg'
        before = list(self.remote.bus.writes)
        with patch('remote_control.urllib.request.urlopen', return_value=response) as upstream:
            with urlopen(self.base + '/camera/preview.jpg?after=11', timeout=2) as result:
                self.assertEqual(result.read(), b'jpeg')
                self.assertEqual(result.headers['X-Frame-Sequence'], '12')
                self.assertEqual(result.headers['X-Frame-Age'], '0.06')
        self.assertEqual(upstream.call_args.args[0], 'http://127.0.0.1:8000/snapshot.jpg?after=11')
        self.assertEqual(before, self.remote.bus.writes)

    def test_web_start_cannot_disable_its_heartbeat_by_claiming_gpio_source(self):
        camera = dict(ready=True, labels=['plane'], frame_age_seconds=0, frame_size=[960, 540])
        req = Request(self.base + '/api/autonomous/start',
            data=json.dumps(dict(version=2, mode='behind', dry_run=True, source='gpio')).encode(),
            headers={'Content-Type': 'application/json'})
        with patch('autonomous.camera_status', return_value=camera):
            with urlopen(req, timeout=2) as response: result = json.load(response)
        self.assertEqual(result['source'], 'web')
        self.assertIsInstance(result['owner'], str)


if __name__ == '__main__':
    unittest.main()
