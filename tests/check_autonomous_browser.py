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
        with patch('pi_vision.control.remote_control.camera_status', return_value=info), patch('pi_vision.control.autonomous.camera_status', return_value=info), patch('pi_vision.control.autonomous.prepare_launch', return_value=2600), patch('pi_vision.control.launcher_calibration.reload_launcher', return_value=dict(lock_us=2400, release_us=2600)), sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            sequence = [0]
            mark_noise = [False]
            def preview(route):
                sequence[0] += 1
                headers = {'X-Frame-Sequence': str(sequence[0]), 'X-Frame-Age': '0.05'}
                if mark_noise[0]:
                    headers['X-Noise-Boxes'] = '[[0.1,0.2,0.3,0.4]]'
                route.fulfill(body=jpeg.getvalue(), content_type='image/jpeg', headers=headers)
            page.route('**/camera/preview.jpg?*', preview)
            page.goto(f'http://127.0.0.1:{server.server_port}/autonomous')
            page.locator('#kp').wait_for()
            assert page.locator('#post_release_throttle_hold').input_value() == '0.5'
            assert 'Stationary filter on (0.6 s)' in page.locator('#stationaryFilter').inner_text()
            page.locator('#stationary_timeout').fill('0.8')
            page.locator('#save').click()
            page.get_by_text('Stationary filter on (0.8 s).', exact=False).wait_for()
            page.get_by_role('button', name='Disable noise filter').click()
            page.get_by_text('Stationary filter off.', exact=True).wait_for()
            assert remote.autonomy.config['stationary_timeout'] == 0
            assert page.locator('#stationary_timeout').input_value() == '0'
            page.reload()
            page.get_by_role('button', name='Enable noise filter').click()
            page.get_by_text('Stationary filter on (0.8 s).', exact=False).wait_for()
            assert remote.autonomy.config['stationary_timeout'] == .8
            assert page.locator('nav [aria-current="page"]').inner_text() == 'Autonomous'
            assert page.locator('nav a').count() == 5
            page.wait_for_function("document.querySelector('#cameraPreview').naturalWidth === 960")
            sent, _ = remote.calibration.outputs(75, 0, initialization=True)
            remote._output(1, sent)
            page.get_by_text('Throttle sent: 75.0%', exact=True).wait_for()
            throttle_meter = page.locator('#throttleMeter')
            assert throttle_meter.is_visible()
            assert abs(throttle_meter.evaluate('meter => meter.value') - 75) < .01
            remote._safe()
            page.locator('#feedback_hz').fill('30')
            page.locator('#kp').fill('42')
            page.locator('#post_release_throttle_hold').fill('0.75')
            page.locator('#vertical_kp').fill('30')
            page.locator('#turn_throttle_boost').fill('4')
            page.locator('#save').click()
            page.wait_for_function("document.querySelector('#message').textContent === 'Parameters saved.'")
            assert remote.autonomy.config['kp'] == 42
            assert remote.autonomy.config['post_release_throttle_hold'] == .75
            assert remote.autonomy.config['vertical_kp'] == 30
            assert remote.autonomy.config['turn_throttle_boost'] == 4
            assert remote.autonomy.config['feedback_hz'] == 30
            assert page.locator('#dry').count() == 0
            before = list(bus.writes)
            page.locator('#cameraPreview').scroll_into_view_if_needed()
            box = page.locator('#cameraPreview').bounding_box()
            page.mouse.move(box['x'] + .55 * box['width'], box['y'] + .28 * box['height'])
            page.mouse.down()
            page.mouse.move(box['x'] + .85 * box['width'], box['y'] + .5 * box['height'], steps=5)
            page.mouse.up()
            assert page.locator('#liveTargetBox').is_visible()
            assert abs(float(page.locator('#setpoint_x').input_value()) - .7) < .005
            assert abs(float(page.locator('#setpoint_y').input_value()) - .39) < .005
            assert remote.autonomy.config['setpoint_x'] == .5
            page.locator('#start').click()
            page.get_by_text('Save the drawn target before launch.', exact=True).wait_for()
            page.locator('#saveLiveTarget').click()
            page.wait_for_function("document.querySelector('#liveTargetStatus').textContent.endsWith('saved.')")
            assert abs(remote.autonomy.config['setpoint_x'] - .7) < .005
            assert abs(remote.autonomy.config['setpoint_y'] - .39) < .005
            assert not remote.autonomy.active
            assert bus.writes == before
            page.on('dialog', lambda dialog: dialog.accept())
            remote.autonomy.config['max_roll'] = 100
            page.locator('#start').click()
            page.wait_for_function("document.querySelector('#actionError').textContent.includes('remote limits')")
            page.wait_for_timeout(600)
            assert 'remote limits' in page.locator('#actionError').inner_text()
            assert not remote.autonomy.active
            remote.autonomy.config['max_roll'] = 15
            page.locator('#reloadLauncher').click()
            page.wait_for_function("document.querySelector('#launcherStatus').textContent.includes('Lock commanded at 2400')")
            page.locator('#start').click()
            page.wait_for_function("document.querySelector('#message').textContent.startsWith('arming:')")
            assert remote.autonomy.active and not remote.autonomy.dry_run
            assert page.get_by_role('button', name='Disable noise filter').is_disabled()
            assert remote.autonomy.predict_gaps
            mark_noise[0] = True
            noise = page.locator('#noiseOverlay .noise-box')
            noise.get_by_text('Noise', exact=True).wait_for()
            assert abs(noise.bounding_box()['width'] / page.locator('#cameraPreview').bounding_box()['width'] - .2) < .01
            mark_noise[0] = False
            noise.wait_for(state='detached')
            remote.autonomy.command['roll'] = -25
            page.wait_for_function("document.querySelector('#rollCommand').textContent === 'Roll command: LEFT 25.0%'")
            assert page.locator('#rollMarker').evaluate("element => element.style.left") == '37.5%'
            remote.autonomy.command['roll'] = 40
            page.wait_for_function("document.querySelector('#rollCommand').textContent === 'Roll command: RIGHT 40.0%'")
            assert page.locator('#rollMarker').evaluate("element => element.style.left") == '70%'
            other = browser.new_page()
            response = other.request.get(f'http://127.0.0.1:{server.server_port}/api/autonomous')
            assert 'owner' not in response.json()
            page.keyboard.press('Escape')
            page.wait_for_function("!document.querySelector('#start').disabled")
            assert not remote.autonomy.active
            page.wait_for_function("document.querySelector('#rollCommand').textContent === 'Roll command: CENTER 0.0%'")
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            Path('test-artifacts').mkdir(exist_ok=True)
            page.screenshot(path='test-artifacts/autonomous-mobile.png', full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print('Autonomous browser checks passed: save target, live controls on fake hardware, exclusive owner, Stop/Escape, mobile layout.')


if __name__ == '__main__':
    main()
