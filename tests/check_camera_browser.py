"""Camera UI regressions with a synthetic camera; never touches the Pi."""
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import jsonify
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_camera_recording import fake_camera_app


def main():
    with tempfile.TemporaryDirectory() as temporary:
        app, shared = fake_camera_app(Path(temporary))
        app.extensions['camera_module'].ROOT = Path(__file__).resolve().parents[1]
        shared.info['preview_fps_limit'] = 15
        @app.before_request
        def reject_setting():
            from flask import request
            if request.method == 'POST' and request.path == '/api/settings':
                return jsonify(error='Camera settings are locked for this test.'), 409
        stopped = threading.Event()
        def produce():
            while not stopped.wait(.05):
                with shared.condition:
                    shared.last_frame = shared.preview_time = time.monotonic()
                    shared.preview_sequence += 1
                    shared.condition.notify_all()
        threading.Thread(target=produce, daemon=True).start()
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.wait_for_function("!document.querySelector('#apply').disabled")
                page.locator('#rate').fill('50')
                page.locator('#apply').click()
                page.wait_for_function("document.querySelector('#settings-message').textContent.includes('locked for this test')")
                assert 'undefined' not in page.locator('#settings-message').inner_text()
                assert page.evaluate('''() => {
                  const p = Object.create(LatestCameraPreview.prototype); p.previewLimit = 15;
                  p.setRate({settings:{fps:50},preview_fps_limit:15}); if(p.fps!==15) return false;
                  p.setRate({settings:{fps:10}}); if(p.fps!==10) return false;
                  p.setRate({settings:{fps:50},preview_fps_limit:30}); return p.fps===30;
                }''')
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path='test-artifacts/camera-mobile.png', full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            stopped.set()
            shared.stop.set()
            server.shutdown()
            server.server_close()
    print('Camera browser checks passed: preview rate, API error messages, mobile layout.')


if __name__ == '__main__':
    main()
