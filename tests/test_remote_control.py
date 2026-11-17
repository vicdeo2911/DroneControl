import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control_calibration import Calibration
from remote_control import Remote, handler_for

PROFILE = dict(throttle_idle=.7, throttle_full=2.8, roll_center=1.6, roll_left=.4, roll_right=3.1)

class FakeBus:
    def __init__(self): self.writes = []
    def write_i2c_block_data(self, address, register, data): self.writes.append((address, register, data))
    def write_byte_data(self, *args): pass

class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.remote = Remote(self.bus)
        self.remote.calibration = Calibration(PROFILE)
        self.token = self.remote.enable()['token']

    def test_invalid_commands_do_not_write(self):
        before = len(self.bus.writes)
        for value in (51, math.nan, math.inf, True, '25', None):
            with self.assertRaises(ValueError): self.remote.update(self.token, 0, value, 0)
            with self.assertRaises(ValueError): self.remote.update(self.token, 0, 0, value)
        for t, r in ((-1, 0), (0, -51)):
            with self.assertRaises(ValueError): self.remote.update(self.token, 0, t, r)
        self.assertEqual(len(self.bus.writes), before)

    def test_normal_mapping_and_channels(self):
        self.remote.update(self.token, 0, 50, -50)
        self.assertAlmostEqual(self.remote.throttle, 1.75)
        self.assertAlmostEqual(self.remote.roll, 1.0)
        self.assertEqual([x[1] for x in self.bus.writes[-2:]], [4, 2])
        self.remote.update(self.token, 1, 0, 50)
        self.assertAlmostEqual(self.remote.throttle, .7)
        self.assertAlmostEqual(self.remote.roll, 2.35)

    def test_stop_uses_calibration_and_blocks_queued_commands(self):
        self.remote.update(self.token, 0, 50, 50)
        self.remote.stop()
        self.assertEqual((self.remote.throttle, self.remote.roll), (.7, 1.6))
        with self.assertRaises(ValueError): self.remote.update(self.token, 1, 50, 50)
        before = len(self.bus.writes)
        self.remote._sweep(self.token)
        self.assertEqual(len(self.bus.writes), before)

    def test_expired_commands_do_not_revive_outputs(self):
        self.remote.update(self.token, 0, 50, 50)
        self.remote.last_seen -= 2
        with self.assertRaises(ValueError): self.remote.update(self.token, 1, 50, 50)
        self.assertFalse(self.remote.status()['enabled'])
        self.assertEqual((self.remote.throttle, self.remote.roll), (.7, 1.6))

    def test_watchdog_uses_idle_and_center(self):
        self.remote.update(self.token, 0, 50, 50)
        self.remote.last_seen -= 2
        self.remote.watchdog()
        self.assertEqual((self.remote.throttle, self.remote.roll), (.7, 1.6))
        self.assertIsNone(self.remote.token)

    def test_initialization_uses_calibrated_full(self):
        self.bus.writes.clear()
        with patch('remote_control.time.sleep'): self.remote._sweep(self.token)
        volts = [((d[0] | d[1] << 8) >> 1) / 32767 * 5 for _, reg, d in self.bus.writes if reg == 4]
        self.assertLessEqual(max(volts), 2.8)
        self.assertGreater(max(volts), 2.79)
        self.assertGreater(min(volts), .69)
        self.assertEqual((self.remote.throttle, self.remote.roll), (.7, 1.6))
        self.assertEqual(self.remote.token, self.token)
        self.assertFalse(self.remote.initializing)
        self.remote.update(self.token, 0, 25, 0)
        self.assertAlmostEqual(self.remote.throttle, 1.225)

    def test_limits_persist_and_enforce_calibrated_endpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'remote-calibration.json'
            Calibration(PROFILE).save(path)
            remote = Remote(FakeBus(), path)
            remote.set_limits(80, 100)
            restored = Remote(FakeBus(), path)
            self.assertEqual(restored.status()['limits'], {'throttle': 80, 'roll': 100})
            token = restored.enable()['token']
            restored.update(token, 0, 80, -100)
            self.assertAlmostEqual(restored.throttle, 2.38)
            self.assertAlmostEqual(restored.roll, .4)
            before = len(restored.bus.writes)
            with self.assertRaises(ValueError): restored.update(token, 1, 81, 0)
            with self.assertRaises(ValueError): restored.update(token, 1, 80, 101)
            with self.assertRaises(ValueError): restored.set_limits(50, 50)
            self.assertEqual(len(restored.bus.writes), before)
            restored.stop()
            self.assertEqual((restored.throttle, restored.roll), (.7, 1.6))
            self.assertEqual(restored.calibration.points, PROFILE)

    def test_invalid_limits_and_incomplete_calibration(self):
        remote = Remote(FakeBus())
        with self.assertRaises(ValueError): remote.set_limits(80, 80)
        remote.calibration = Calibration(PROFILE)
        for value in (0, -1, 101, math.nan, math.inf, True, '80', None):
            with self.assertRaises(ValueError): remote.set_limits(value, 50)
            with self.assertRaises(ValueError): remote.set_limits(50, value)
        self.assertEqual(remote.limits, {'throttle': 50, 'roll': 50})

    def test_sensitivity_migrates_and_persists_with_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'remote-calibration.json'
            Calibration(PROFILE).save(path)
            settings = path.with_name('remote-settings.json')
            settings.write_text(json.dumps({'throttle': 80, 'roll': 60}))
            remote = Remote(FakeBus(), path)
            self.assertEqual(remote.status()['sensitivity'], {'rise': 30, 'fall': 30, 'decay': 10})
            before = list(remote.bus.writes)
            remote.set_sensitivity(10, 70)
            self.assertEqual(remote.bus.writes, before)
            remote.set_limits(90, 50)
            restored = Remote(FakeBus(), path)
            self.assertEqual(restored.status()['sensitivity'], {'rise': 10, 'fall': 70, 'decay': 10})
            self.assertEqual(restored.limits, {'throttle': 90, 'roll': 50})
            restored.set_sensitivity(1, 100)
            self.assertEqual(Remote(FakeBus(), path).limits, {'throttle': 90, 'roll': 50})

    def test_invalid_and_active_sensitivity_changes_are_rejected(self):
        with self.assertRaises(ValueError): self.remote.set_sensitivity(10, 60)
        self.remote.stop()
        before = list(self.bus.writes)
        for value in (0, -1, 101, math.nan, math.inf, True, '30', None):
            with self.assertRaises(ValueError): self.remote.set_sensitivity(value, 30)
            with self.assertRaises(ValueError): self.remote.set_sensitivity(30, value)
        self.assertEqual(self.remote.sensitivity, {'rise': 30, 'fall': 30, 'decay': 10})
        self.assertEqual(self.bus.writes, before)

    def test_decay_migrates_persists_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'remote-calibration.json'
            settings = path.with_name('remote-settings.json')
            settings.write_text(json.dumps(dict(throttle=80, roll=50, sensitivity=dict(rise=20, fall=40))))
            remote = Remote(FakeBus(), path)
            self.assertEqual(remote.sensitivity, dict(rise=20, fall=40, decay=10))
            remote.set_sensitivity(20, 40, 7)
            restored = Remote(FakeBus(), path)
            self.assertEqual(restored.sensitivity['decay'], 7)
            restored.set_sensitivity(30, 50)  # Older clients preserve the saved decay.
            self.assertEqual(restored.sensitivity['decay'], 7)
            restored.set_sensitivity(30, 50, 0)
            self.assertEqual(Remote(FakeBus(), path).sensitivity['decay'], 0)

    def test_invalid_decay_is_rejected_without_changing_settings(self):
        self.remote.stop()
        before = list(self.bus.writes)
        for value in (-1, 101, math.nan, math.inf, True, '10'):
            with self.assertRaises(ValueError): self.remote.set_sensitivity(30, 30, value)
        self.assertEqual(self.remote.sensitivity['decay'], 10)
        self.assertEqual(self.bus.writes, before)

    def test_stop_during_arming_does_not_reenable(self):
        def stop_during_pause(_):
            self.remote.stop()
        self.remote.initializing = True
        with patch('remote_control.time.sleep', side_effect=stop_during_pause):
            self.remote._sweep(self.token)
        self.assertIsNone(self.remote.token)
        self.assertFalse(self.remote.initializing)
        self.assertEqual((self.remote.throttle, self.remote.roll), (.7, 1.6))

    def test_timeout_still_stops_after_successful_arming(self):
        with patch('remote_control.time.sleep'): self.remote._sweep(self.token)
        self.assertTrue(self.remote.status()['enabled'])
        self.remote.last_seen -= 2
        self.remote.watchdog()
        self.assertFalse(self.remote.status()['enabled'])

    def test_off_requires_confirmation_and_preserves_calibration(self):
        before = list(self.bus.writes)
        for value in (False, None, 1, 'true'):
            with self.assertRaises(ValueError): self.remote.power_off(value)
        self.assertEqual(self.bus.writes, before)
        self.remote.power_off(True)
        self.assertEqual(self.bus.writes[-1], (0x58, 2, [0, 0, 0, 0]))
        self.assertEqual((self.remote.throttle, self.remote.roll), (0, 0))
        self.assertTrue(self.remote.status()['outputs_zero'])
        self.assertIsNone(self.remote.token)
        self.assertEqual(self.remote.calibration.points, PROFILE)

    def test_off_survives_stop_watchdog_stale_commands_and_arming(self):
        self.remote.initializing = True
        self.remote.power_off(True)
        self.remote.stop()
        self.remote.watchdog()
        self.remote._sweep(self.token)
        with self.assertRaises(ValueError): self.remote.update(self.token, 1, 25, 0)
        with self.assertRaises(ValueError): self.remote.enable()
        self.assertTrue(self.remote.outputs_zero)
        self.assertFalse(self.remote.initializing)
        self.assertEqual((self.remote.throttle, self.remote.roll), (0, 0))

    def test_off_persists_across_restart_and_requires_explicit_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'remote-calibration.json'
            Calibration(PROFILE).save(path)
            remote = Remote(FakeBus(), path)
            remote.power_off(True)
            bus = FakeBus()
            restored = Remote(bus, path)
            self.assertTrue(restored.outputs_zero)
            self.assertEqual(bus.writes, [(0x58, 2, [0, 0, 0, 0])])
            with self.assertRaises(ValueError): restored.enable('calibration')
            restored.enable(restore_outputs=True)
            self.assertFalse(restored.outputs_zero)
            self.assertEqual((restored.throttle, restored.roll), (.7, 1.6))
            restarted = Remote(FakeBus(), path)
            self.assertFalse(restarted.outputs_zero)

    def test_off_write_failure_retries_zero_instead_of_idle(self):
        original = self.bus.write_i2c_block_data
        self.bus.write_i2c_block_data = lambda *args: (_ for _ in ()).throw(OSError('Disconnected'))
        with self.assertRaises(OSError): self.remote.power_off(True)
        self.assertTrue(self.remote.outputs_zero)
        self.assertIsNotNone(self.remote.error)
        self.assertIsNone(self.remote.token)
        self.bus.write_i2c_block_data = original
        self.remote.watchdog()
        self.assertEqual(self.bus.writes[-1], (0x58, 2, [0, 0, 0, 0]))
        self.assertIsNone(self.remote.error)

    def test_roll_restore_attempted_when_throttle_fails(self):
        original = self.bus.write_i2c_block_data
        def fail(address, register, data):
            if register == 4: raise OSError('Disconnected')
            original(address, register, data)
        self.bus.write_i2c_block_data = fail
        with self.assertRaises(OSError): self.remote.stop()
        self.assertEqual(self.bus.writes[-1][1], 2)
        self.assertIsNone(self.remote.token)
        self.assertIsNotNone(self.remote.error)

    def test_unknown_idle_never_assumed_zero(self):
        bus = FakeBus()
        remote = Remote(bus)
        self.assertEqual(bus.writes, [])
        token = remote.enable('calibration')['token']
        remote.update(token, 0, 1.1, None)
        before = list(bus.writes)
        remote.stop()
        self.assertEqual(bus.writes, before)
        self.assertEqual(remote.throttle, 1.1)

    def test_setup_persistence_and_startup_without_zero_pulse(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'calibration.json'
            remote = Remote(FakeBus(), path)
            token = remote.enable('calibration')['token']
            with self.assertRaises(ValueError): remote.update(token, 0, None, 1.6)
            for seq, (name, volts) in enumerate(PROFILE.items()): remote.save_point(token, seq, name, volts)
            self.assertTrue(remote.status()['calibration_complete'])
            self.assertEqual((remote.throttle, remote.roll), (.7, 1.6))
            bus = FakeBus()
            restored = Remote(bus, path)
            self.assertEqual(restored.calibration.points, PROFILE)
            self.assertEqual((restored.throttle, restored.roll), (.7, 1.6))
            self.assertTrue(all(any(data) for _, _, data in bus.writes))

    def test_old_center_preserved_normal_control_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'old.json'
            path.write_text(json.dumps({'roll_center_percent': 48}))
            remote = Remote(FakeBus(), path)
            self.assertAlmostEqual(remote.calibration.points['roll_center'], 1.584)
            with self.assertRaises(ValueError): remote.enable()

    def test_raw_bounds_idle_first_and_no_setup_initialization(self):
        remote = Remote(FakeBus())
        token = remote.enable('calibration')['token']
        for value in (-.01, 3.31, True, math.nan, math.inf):
            with self.assertRaises(ValueError): remote.update(token, 0, value, None)
        with self.assertRaises(ValueError): remote.save_point(token, 0, 'throttle_full', 3.0)
        with self.assertRaises(ValueError): remote.initialize(token)
        remote.save_point(token, 0, 'throttle_idle', .7)
        remote.update(token, 1, None, 2.0)
        self.assertEqual(remote.throttle, .7)

    def test_inverted_wiring_and_geometry_validation(self):
        inverted = Calibration({**PROFILE, 'throttle_idle': 2.8, 'throttle_full': .7, 'roll_left': 3.0, 'roll_right': .2})
        t, r = inverted.outputs(50, -50)
        self.assertAlmostEqual(t, 1.75)
        self.assertAlmostEqual(r, 2.3)
        for changes in ({'throttle_full': .7}, {'roll_center': 3.2}):
            with self.assertRaises(ValueError): Calibration({**PROFILE, **changes})

    def test_shared_mapper_blocks_partial_and_full_normal_throttle(self):
        with self.assertRaises(ValueError): Calibration({'throttle_idle': .7}).outputs(0, 0)
        with self.assertRaises(ValueError): Calibration(PROFILE).outputs(100, 0)
        self.assertEqual(Calibration(PROFILE).outputs(100, 0, initialization=True), (2.8, 1.6))

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.remote = Remote(self.bus)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.remote))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, path, data):
        request = Request(self.base + '/api/' + path, data=json.dumps(data).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=2) as result:
            return json.load(result)

    def test_old_page_cannot_issue_new_commands(self):
        with self.assertRaises(HTTPError) as raised:
            self.post('enable', {'mode': 'calibration'})
        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(self.bus.writes, [])

    def test_separate_pages_assets_and_limit_endpoint(self):
        for path, mime in (('/', 'text/html'), ('/calibration', 'text/html'),
                           ('/joystick', 'text/html'), ('/joystick.js', 'text/javascript'),
                           ('/remote.js', 'text/javascript'), ('/remote.css', 'text/css')):
            with urlopen(self.base + path) as result:
                self.assertEqual(result.status, 200)
                self.assertIn(mime, result.headers['Content-Type'])
        with self.assertRaises(HTTPError): urlopen(self.base + '/remote-calibration.json')
        self.remote.calibration = Calibration(PROFILE)
        state = self.post('limits', dict(version=2, throttle=75, roll=90))
        self.assertEqual(state['limits'], {'throttle': 75, 'roll': 90})

    def test_off_endpoint_requires_confirmation_and_stop_keeps_zero(self):
        with self.assertRaises(HTTPError): self.post('off', dict(version=2))
        state = self.post('off', dict(version=2, confirmed=True))
        self.assertTrue(state['outputs_zero'])
        self.assertEqual((state['throttle_volts'], state['roll_volts']), (0, 0))
        state = self.post('stop', {})
        self.assertTrue(state['outputs_zero'])
        self.assertEqual((state['throttle_volts'], state['roll_volts']), (0, 0))

    def test_sensitivity_endpoint_validates_and_requires_stopped_session(self):
        state = self.post('sensitivity', dict(version=2, rise=12, fall=65))
        self.assertEqual(state['sensitivity'], {'rise': 12, 'fall': 65, 'decay': 10})
        with self.assertRaises(HTTPError): self.post('sensitivity', dict(version=2, rise=0, fall=30))
        self.post('enable', dict(version=2, mode='calibration'))
        with self.assertRaises(HTTPError): self.post('sensitivity', dict(version=2, rise=30, fall=30))
        self.assertEqual(self.remote.sensitivity, {'rise': 12, 'fall': 65, 'decay': 10})

    def test_decay_endpoint_saves_and_validates(self):
        state = self.post('sensitivity', dict(version=2, rise=30, fall=40, decay=8))
        self.assertEqual(state['sensitivity']['decay'], 8)
        with self.assertRaises(HTTPError):
            self.post('sensitivity', dict(version=2, rise=30, fall=40, decay=-1))
        self.assertEqual(self.remote.sensitivity['decay'], 8)

    def test_complete_setup_and_normal_control_over_http(self):
        with self.assertRaises(HTTPError):
            self.post('enable', {'version': 2})
        state = self.post('enable', {'version': 2, 'mode': 'calibration'})
        token = state['token']
        for seq, (name, volts) in enumerate(PROFILE.items()):
            state = self.post('save-point', dict(version=2, token=token, sequence=seq, point=name, volts=volts))
        self.assertTrue(state['calibration_complete'])
        self.post('stop', {})
        state = self.post('enable', {'version': 2})
        state = self.post('control', dict(version=2, token=state['token'], sequence=0, throttle=50, roll=0))
        self.assertAlmostEqual(state['throttle_volts'], 1.75)
        self.assertAlmostEqual(state['roll_volts'], 1.6)
        state = self.post('stop', {})
        self.assertEqual((state['throttle_volts'], state['roll_volts']), (.7, 1.6))


if __name__ == '__main__': unittest.main()
