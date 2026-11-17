"""Browser checks against a fake DAC and synthetic detections; no Pi access."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import io
from PIL import Image
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from pi_vision.control.control_calibration import Calibration
from pi_vision.control.remote_control import Remote, handler_for
from test_remote_control import FakeBus, PROFILE


def main():
    bus = FakeBus()
    remote = Remote(bus)
    remote.calibration = Calibration(PROFILE)
    info = dict(ready=True, labels=['plane', 'target'], frame_age_seconds=0, frame_size=[960, 540],
                settings={'fps': 30}, limits={'fps_max': 60}, camera_fps=30, fps=30, inference_ms=14)
    jpeg = io.BytesIO()
    Image.new('RGB', (960, 540), '#314047').save(jpeg, format='JPEG')
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(remote))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with patch('pi_vision.control.remote_control.camera_status', return_value=info), patch('pi_vision.control.autonomous.camera_status', return_value=info), sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            sequence = [0]
            def preview(route):
                sequence[0] += 1
                route.fulfill(body=jpeg.getvalue(), content_type='image/jpeg', headers={
                    'X-Frame-Sequence': str(sequence[0]), 'X-Frame-Age': '0.05'})
            page.route('**/camera/preview.jpg?*', preview)
            page.goto(f'http://127.0.0.1:{server.server_port}/autonomous')
            page.locator('#kp').wait_for()
            assert page.locator('nav [aria-current="page"]').inner_text() == 'Autonomous'
            assert page.locator('nav a').count() == 5
            page.wait_for_function("document.querySelector('#cameraPreview').naturalWidth === 960")
            page.locator('#feedback_hz').fill('30')
            page.locator('#kp').fill('42')
            page.locator('#save').click()
            page.wait_for_function("document.querySelector('#message').textContent === 'Parameters saved.'")
            assert remote.autonomy.config['kp'] == 42
            assert remote.autonomy.config['feedback_hz'] == 30
            before = list(bus.writes)
            page.locator('#start').click()
            page.wait_for_function("document.querySelector('#start').disabled")
            assert remote.autonomy.active
            assert bus.writes == before
            assert page.locator('#kp').is_disabled()
            page.keyboard.press('Escape')
            page.wait_for_function("!document.querySelector('#start').disabled")
            assert not remote.autonomy.active
            # A second tab cannot inherit the heartbeat owner by reading status.
            page.locator('#start').click()
            page.wait_for_function("document.querySelector('#start').disabled")
            other = browser.new_page()
            response = other.request.get(f'http://127.0.0.1:{server.server_port}/api/autonomous')
            assert 'owner' not in response.json()
            page.locator('#stop').click()
            page.wait_for_function("!document.querySelector('#start').disabled")
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            Path('test-artifacts').mkdir(exist_ok=True)
            page.screenshot(path='test-artifacts/autonomous-mobile.png', full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print('Autonomous browser checks passed: save, dry run, exclusive owner, Stop/Escape, mobile layout.')


if __name__ == '__main__':
    main()
