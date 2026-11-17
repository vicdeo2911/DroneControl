"""Moved application assets must remain available at their existing URLs."""
import re
import tempfile
import unittest
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from pi_vision.control.remote_control import Remote, handler_for
from pi_vision.studio.training_studio import create_app
from test_camera_recording import fake_camera_app
from test_remote_control import FakeBus


def asset_urls(html):
    return re.findall(r'(?:src|href)=["\'](/[^"\']+\.(?:js|css))["\']', html)


class WebAssetTests(unittest.TestCase):
    def assert_flask_assets(self, app):
        with app.test_client() as client:
            page = client.get('/')
            self.assertEqual(page.status_code, 200)
            urls = asset_urls(page.get_data(as_text=True))
            self.assertTrue(urls)
            for url in urls:
                with self.subTest(url=url), client.get(url) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.data)

    def test_camera_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assert_flask_assets(fake_camera_app(folder)[0])

    def test_studio_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assert_flask_assets(create_app(Path(folder)/'data', runs=Path(folder)/'runs'))

    def test_controller_page_assets(self):
        remote = Remote(FakeBus())
        with ThreadingHTTPServer(('127.0.0.1', 0), handler_for(remote)) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                for page in ('/', '/autonomous', '/joystick', '/flights', '/calibration'):
                    with urlopen(base+page) as response:
                        html = response.read().decode()
                    for url in asset_urls(html):
                        with self.subTest(page=page, url=url), urlopen(base+url) as response:
                            self.assertEqual(response.status, 200)
                            self.assertTrue(response.read())
            finally:
                server.shutdown()
                worker.join(timeout=3)
