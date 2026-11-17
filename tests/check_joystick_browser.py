"""Browser integration checks with a simulated joystick and fake DAC.

Run: python tests/check_joystick_browser.py
Requires Playwright and Microsoft Edge. Never connects to the physical plane.
"""
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_remote_control import FakeBus, PROFILE
from pi_vision.control.control_calibration import Calibration
from pi_vision.control.remote_control import Remote, handler_for


def main():
    remote = Remote(FakeBus())
    remote.calibration = Calibration(PROFILE)
    remote.set_limits(80, 60)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(remote))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script("""
                window.fakePad = {id: 'BETAFPV test joystick', index: 0, connected: true,
                  axes: [0, 0, 0, 0], buttons: [], timestamp: 1};
                Object.defineProperty(navigator, 'getGamepads', {value: () =>
                  window.fakePad.connected ? [window.fakePad] : []});
            """)
            page.goto(base + '/joystick')
            page.wait_for_function("document.querySelectorAll('.axis-row').length === 4")
            assert page.locator('#arm').is_disabled()
            assert not remote.status()['enabled']
            setup_writes = len(remote.bus.writes)

            def axis(index, value):
                page.evaluate('([i, v]) => {fakePad.axes[i] = v; fakePad.timestamp++;}', [index, value])
                page.wait_for_timeout(80)

            def capture(point, index, value):
                axis(index, value)
                page.locator('[data-capture=' + point + ']').click()

            page.locator('[data-identify=throttleAxis]').click()
            axis(0, 1)
            page.wait_for_function("document.getElementById('throttleAxis').value === '0'")
            capture('idle', 0, 1)
            capture('full', 0, -1)
            page.locator('[data-identify=rollAxis]').click()
            axis(2, .9)
            page.wait_for_function("document.getElementById('rollAxis').value === '2'")
            capture('left', 2, .9)
            capture('center', 2, .1)
            capture('right', 2, -.8)
            page.wait_for_function("document.getElementById('joystickThrottlePreview').textContent === '80.0%'")
            assert page.locator('#joystickThrottlePreview').inner_text() == '80.0%'
            assert page.locator('#joystickRollPreview').inner_text() == '60.0%'
            assert len(remote.bus.writes) == setup_writes, 'Setup must never move outputs'
            page.locator('#saveJoystick').click()
            assert page.locator('#arm').is_disabled(), 'High throttle must block arming'
            page.reload()
            page.wait_for_function("document.getElementById('throttleAxis').value === '0'")
            assert page.locator('#rollAxis').input_value() == '2'
            axis(0, 1)
            axis(2, .1)
            page.wait_for_function("!document.getElementById('arm').disabled")
            page.locator('#arm').click()
            page.wait_for_function("typeof token === 'string' && !starting && !state.initializing && joystickEngaged")
            assert page.locator('#throttleAxis').is_disabled()
            assert page.locator('#throttle').is_disabled()
            axis(0, 0)
            axis(2, -.8)
            page.wait_for_function('throttle === 40 && roll === 60')
            page.wait_for_timeout(350)
            assert abs(remote.throttle - 1.54) < .001
            assert abs(remote.roll - 2.5) < .001
            axis(1, -1)
            axis(3, 1)
            page.wait_for_timeout(400)
            assert page.evaluate('throttle === 40 && roll === 60'), 'Pitch/yaw must stay unused'
            page.keyboard.press('w')
            page.wait_for_timeout(200)
            assert page.evaluate('throttle === 40'), 'Keyboard must not override joystick'
            axis(2, .12)
            page.wait_for_function('roll === 0')
            page.evaluate('fakePad.connected = false')
            page.wait_for_function('token === null')
            page.wait_for_timeout(200)
            assert not remote.status()['enabled']
            assert remote.throttle == PROFILE['throttle_idle']
            page.evaluate('fakePad.connected = true')
            page.wait_for_timeout(350)
            assert not remote.status()['enabled'], 'Reconnect must not rearm'
            axis(0, 1)
            axis(2, .1)
            page.wait_for_function("!document.getElementById('arm').disabled")
            page.locator('#arm').click()
            page.wait_for_function('joystickEngaged')
            page.evaluate('fakePad.id = "Another joystick"')
            page.wait_for_function('token === null')
            page.wait_for_timeout(250)
            assert not remote.status()['enabled'], 'Changed controller must stop'
            page.evaluate('fakePad.id = "BETAFPV test joystick"')
            page.wait_for_function("!document.getElementById('arm').disabled")
            page.locator('#rollAxis').select_option('0')
            page.wait_for_function("document.getElementById('saveJoystick').disabled && document.getElementById('arm').disabled")
            assert page.locator('#saveJoystick').is_disabled(), 'Duplicate axes must be rejected'
            assert page.locator('#arm').is_disabled(), 'Unsaved mapping must block arming'
            for width in [390, 800, 1280]:
                page.set_viewport_size(dict(width=width, height=900))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.goto(base + '/')
            page.wait_for_function("!document.getElementById('arm').disabled")
            page.locator('#arm').click()
            page.wait_for_function('!starting && !state.initializing && token !== null')
            page.keyboard.down('w')
            page.wait_for_timeout(500)
            page.keyboard.up('w')
            value = page.evaluate('throttle')
            assert value > 8
            page.wait_for_timeout(300)
            assert page.evaluate('throttle') < value, 'Keyboard decay must still work'
            page.keyboard.press('Space')
            page.goto(base + '/calibration')
            page.wait_for_timeout(200)
            assert not errors, errors
            browser.close()
        print('Joystick browser checks passed: axis detection/capture, reversed mappings, preview isolation, '
              'saved mapping, neutral arming, limits, dead zone, ignored axes/keyboard, disconnect/device '
              'change stop, reconnect, duplicate axes, mobile layout, keyboard decay, calibration page.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
