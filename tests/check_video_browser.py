"""Record -> reload browser -> extract -> label-ready frames, with a fake camera by default.

--live-camera URL explicitly records a three-second clip on the selected Pi.
Extracted QA frames/videos remain in a temporary local library, never training data.
"""
import argparse
from pathlib import Path
import sys
import tempfile
import threading
import time

import cv2
import numpy as np
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pi_vision.studio.training_studio import create_app
from test_camera_recording import fake_camera_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-camera')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        servers, producers = [], []
        stopped = threading.Event()
        shared = None
        if args.live_camera:
            camera = args.live_camera
        else:
            camera_app, shared = fake_camera_app(root / 'pi')
            source_server = make_server('127.0.0.1', 0, camera_app, threaded=True)
            servers.append(source_server)
            camera = 'http://127.0.0.1:' + str(source_server.server_port)
            def produce():
                index = 0
                while not stopped.wait(1/shared.settings['fps']):
                    index += 1
                    settings = dict(shared.settings)
                    frame = np.full((settings['height'], settings['width'], 3), 30 + index % 150, np.uint8)
                    jpeg = cv2.imencode('.jpg', frame)[1].tobytes()
                    shared.recorder.submit(frame, index)
                    with shared.condition:
                        shared.raw_jpeg = shared.jpeg = jpeg
                        shared.sequence = index
                        shared.last_frame = time.monotonic()
                        shared.frame_size = (settings['width'], settings['height'])
                        shared.applied_revision = shared.revision
                        shared.info['camera_fps'] = settings['fps']
                        shared.condition.notify_all()
            producer = threading.Thread(target=produce, daemon=True)
            producer.start()
            producers.append(producer)
        app = create_app(root / 'library', camera=camera, runs=root / 'runs')
        server = make_server('127.0.0.1', 0, app, threaded=True)
        servers.append(server)
        threads = []
        for item in servers:
            thread = threading.Thread(target=item.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1200, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = 'http://127.0.0.1:' + str(server.server_port)
                page.goto(base)
                status = page.request.get(base + '/api/recordings').json()
                assert not status['current']['active'], 'An existing clip is recording; leave it undisturbed.'
                page.locator('#showCamera').click()
                page.wait_for_function("document.getElementById('liveCamera').naturalWidth > 0")
                page.locator('#recordSeconds').fill('3')
                page.locator('#startRecording').click()
                page.wait_for_function("document.getElementById('recordingStatus').textContent.startsWith('Recording on Pi')")
                ident = page.request.get(base + '/api/recordings').json()['current']['id']
                # Closing/reloading a browser must not stop the source recording.
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("document.getElementById('recordingStatus').textContent.startsWith('completed')", timeout=30000)
                metadata = page.request.get(base + '/api/recordings').json()['current']
                assert metadata['id'] == ident and metadata['frames'] >= 25, metadata
                assert metadata['dropped'] == 0, metadata
                page.locator('#recordingSelect').select_option(ident)
                page.locator('#sampleInterval').select_option('0.5')
                page.locator('#extractFrames').click()
                page.wait_for_function("document.getElementById('extractionStatus').textContent.startsWith('completed')", timeout=60000)
                page.locator('#annotation').wait_for(state='visible')
                rows = app.extensions['dataset'].list()
                assert 3 <= len(rows) <= 7, len(rows)
                assert all(not row['reviewed'] and row['session'] == 'clip-' + ident for row in rows)
                assert not errors, errors
                print('Record/reload/extract browser checks passed:', metadata, 'Extracted:', len(rows), flush=True)
                browser.close()
        finally:
            extraction = app.extensions['video_extraction']
            if extraction.thread:
                extraction.thread.join(timeout=15)
            stopped.set()
            if shared:
                shared.stop.set()
                with shared.condition:
                    shared.condition.notify_all()
                shared.recorder.stop()
            for item in servers:
                item.shutdown()
                item.server_close()
            for thread in threads + producers:
                thread.join(timeout=5)


if __name__ == '__main__':
    main()
