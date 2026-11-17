import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pi_vision.control.autonomous import Autonomous
from pi_vision.control.launch_input import GpioLaunch, load_config
from pi_vision.control.remote_control import Remote
from test_remote_control import FakeBus, PROFILE
from pi_vision.control.control_calibration import Calibration


class LaunchInputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'gpio-launch.json'
        self.now = 100.
        self.remote = Remote(FakeBus())
        self.remote.calibration = Calibration(PROFILE)
        self.remote.autonomy = Autonomous(self.remote, clock=lambda: self.now)
        self.device = MagicMock()
        self.device.is_active = False
        self.path.write_text(json.dumps(dict(enabled=True, pin=17)))  # Fixture pin, not a project assignment.
        self.input = GpioLaunch(self.remote, self.path, clock=lambda: self.now,
                                device_factory=lambda config: self.device)

    def tearDown(self):
        self.input.close()
        self.directory.cleanup()

    def stable(self, active):
        self.device.is_active = active
        self.input.poll()
        self.now += .06
        self.input.poll()

    def test_no_pin_is_assigned_or_opened_by_default(self):
        factory = MagicMock()
        empty = GpioLaunch(self.remote, None, device_factory=factory)
        self.assertIsNone(empty.config['pin'])
        self.assertFalse(empty.config['enabled'])
        factory.assert_not_called()

    def test_invalid_or_conflicting_pins_and_settings_rejected(self):
        for changes in (dict(enabled=True), dict(pin=2), dict(pin=3), dict(pin=True),
                        dict(pin=40), dict(debounce_seconds=float('nan')), dict(enabled='yes')):
            self.path.write_text(json.dumps(changes))
            with self.assertRaises(ValueError): load_config(self.path)

    def test_startup_high_never_launches_without_inactive_then_active(self):
        with patch.object(self.remote.autonomy, 'start_from_camera') as start:
            self.stable(True)
            self.now += 5
            self.input.poll()
            start.assert_not_called()
            self.stable(False)
            self.stable(True)
            start.assert_called_once_with('behind', dry_run=True, restore_outputs=False, source='gpio')

    def test_debounce_consumes_one_start_and_does_not_repeat_held_high(self):
        self.stable(False)
        with patch.object(self.remote.autonomy, 'start_from_camera') as start:
            self.device.is_active = True
            self.input.poll()
            self.now += .01
            self.device.is_active = False
            self.input.poll()
            start.assert_not_called()
            self.stable(True)
            self.now += 10
            self.input.poll()
            start.assert_called_once()

    def test_busy_edge_is_dropped_not_queued(self):
        self.stable(False)
        self.remote.autonomy.active = True
        with patch.object(self.remote.autonomy, 'start_from_camera') as start:
            self.stable(True)
            self.remote.autonomy.active = False
            self.now += 1
            self.input.poll()
            start.assert_not_called()
            self.stable(False)
            self.stable(True)
            start.assert_called_once()

    def test_rejected_start_needs_a_new_edge(self):
        self.stable(False)
        with patch.object(self.remote.autonomy, 'start_from_camera', side_effect=ValueError('Camera unavailable')) as start:
            self.stable(True)
            self.now += 1
            self.input.poll()
            start.assert_called_once()
            self.assertIn('rejected', self.input.event)

    def test_gpio_session_has_no_browser_heartbeat_and_dry_run_has_no_outputs(self):
        before = list(self.remote.bus.writes)
        self.stable(False)
        camera = dict(ready=True, labels=['plane'], frames=1, frame_age_seconds=0,
                      frame_size=[1000, 500], detections=[])
        with patch('pi_vision.control.autonomous.camera_status', return_value=camera):
            self.stable(True)
        auto = self.remote.autonomy
        self.assertEqual(auto.source, 'gpio')
        self.assertIsNone(auto.owner)
        auto.tick(camera)
        self.now += 2
        camera['frames'] = 2
        auto.tick(camera)
        self.assertTrue(auto.active)
        self.assertEqual(before, self.remote.bus.writes)
        with self.assertRaises(ValueError): auto.heartbeat('browser-cannot-own-gpio')

    def test_gpio_session_still_stops_for_stale_camera_and_duration(self):
        self.remote.autonomy.start('behind', source='gpio')
        self.remote.autonomy.tick(dict(ready=False))
        self.assertFalse(self.remote.autonomy.active)
        self.remote.autonomy.start('behind', source='gpio')
        self.now += 31
        self.remote.autonomy.tick(None)
        self.assertFalse(self.remote.autonomy.active)

    def test_failed_device_open_does_not_enable_a_launch(self):
        def fail(config): raise OSError('unavailable')
        broken = GpioLaunch(self.remote, self.path, device_factory=fail)
        self.assertFalse(broken.status()['available'])
        self.assertIn('unavailable', broken.error)


if __name__ == '__main__':
    unittest.main()
