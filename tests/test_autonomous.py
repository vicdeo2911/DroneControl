import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pi_vision.control.autonomous import Autonomous
from pi_vision.control.remote_control import Remote
from pi_vision.control.control_calibration import Calibration
from test_remote_control import FakeBus, PROFILE


class AutonomousTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.bus = FakeBus()
        self.remote = Remote(self.bus)
        self.remote.calibration = Calibration(PROFILE)
        self.auto = self.remote.autonomy = Autonomous(self.remote, clock=lambda: self.now)
        self.auto.save(dict(launch_delay=1, launch_seconds=.1, confirm_frames=2,
                            acquire_timeout=.5, loss_timeout=.3))
        self.frame = 0

    def tick(self, dt=.1, x=.3, y=.22, **changes):
        self.now += dt
        self.auto.last_heartbeat = self.now
        self.frame += 1
        info = dict(ready=True, frames=self.frame, frame_age_seconds=0, frame_size=[1000, 500],
                    detections=[] if x is None else [dict(label='plane', confidence=.9, box=[x*1000-10, y*500-10, x*1000+10, y*500+10])])
        info.update(changes)
        self.auto.tick(info)

    def track(self, live=False):
        with patch.object(self.remote, 'initialize'):
            self.auto.start('behind', dry_run=not live)
        self.tick()
        self.tick(1.01)
        for _ in range(25):
            if self.auto.state == 'launch':
                break
            self.tick()
        self.tick(.11)
        self.assertEqual(self.auto.state, 'tracking')

    def test_dry_run_tracks_and_never_writes(self):
        before = list(self.bus.writes)
        self.track()
        self.tick()
        self.assertGreater(self.auto.command['roll'], 0)
        self.assertGreater(self.auto.command['throttle'], 0)
        self.assertEqual(self.bus.writes, before)
        self.assertIsNone(self.remote.token)

    def test_launch_behind_without_detection_is_bounded(self):
        self.auto.start('behind')
        self.tick(x=None)
        self.tick(1.01, x=None)
        self.assertEqual(self.auto.state, 'spooling')
        for _ in range(25):
            if self.auto.state == 'launch':
                break
            self.tick(x=None)
        self.assertEqual(self.auto.state, 'launch')
        self.tick(.11, x=None)
        self.tick(.51, x=None)
        self.assertFalse(self.auto.active)
        self.assertIn('never acquired', self.auto.reason)

    def test_visible_waits_for_consecutive_frames_then_idle_countdown(self):
        self.auto.start('visible')
        self.tick()
        self.assertEqual(self.auto.state, 'waiting_view')
        self.tick()
        self.assertEqual(self.auto.state, 'countdown')
        self.assertEqual(self.auto.command, dict(throttle=0, roll=0))

    def test_missing_initial_view_and_prelaunch_loss_stop(self):
        self.auto.start('visible')
        self.tick(x=None)
        self.tick(.51, x=None)
        self.assertFalse(self.auto.active)
        self.auto.start('visible')
        self.tick()
        self.tick()
        self.tick(.31, x=None)
        self.assertFalse(self.auto.active)

    def test_brief_loss_centers_then_requires_confirmation(self):
        self.track()
        self.tick(x=None)
        self.assertEqual(self.auto.state, 'lost')
        self.assertEqual(self.auto.command, dict(throttle=0, roll=0))
        self.tick()
        self.assertEqual(self.auto.state, 'lost')
        self.tick()
        self.assertEqual(self.auto.state, 'tracking')

    def test_live_loss_returns_calibrated_idle_and_latches_stop(self):
        self.track(live=True)
        self.tick(x=None)
        self.tick(.31, x=None)
        self.assertFalse(self.auto.active)
        self.assertIsNone(self.remote.token)
        self.assertEqual((self.remote.throttle, self.remote.roll), (.7, 1.6))
        self.tick()
        self.assertFalse(self.auto.active)

    def test_interleaved_detections_cannot_extend_loss_forever(self):
        self.track()
        self.tick(x=None)
        self.tick()
        self.tick(x=None)
        self.tick(.11)
        self.assertFalse(self.auto.active)

    def test_ambiguous_low_confidence_and_jump_are_missing(self):
        for changes in (dict(detections=[dict(label='plane', confidence=.1, box=[290,100,310,120])]),
                        dict(detections=[dict(label='plane', confidence=.9, box=[290,100,310,120])]*2),
                        dict(detections=[dict(label='plane', confidence=.9, box=[890,100,910,120])])):
            self.track()
            self.tick(**changes)
            self.assertEqual(self.auto.state, 'lost')
            self.auto.stop('test')

    def test_stale_failed_and_frozen_camera_stop(self):
        for changes in (dict(ready=False), dict(error='failed'), dict(frame_age_seconds=.9),
                        dict(frame_age_seconds=float('nan')), dict(frame_size=None)):
            self.track()
            self.tick(**changes)
            self.assertFalse(self.auto.active)
        self.track()
        last = self.auto.last_frame
        self.tick(.51, frames=last)
        self.assertFalse(self.auto.active)

    def test_heartbeat_timeout_and_wrong_owner(self):
        session = self.auto.start('behind')
        with self.assertRaises(ValueError): self.auto.heartbeat('wrong')
        self.auto.heartbeat(session['owner'])
        self.now += 1.3
        self.auto.tick({})
        self.assertFalse(self.auto.active)

    def test_stop_off_and_manual_exclusion(self):
        self.auto.start('behind')
        with self.assertRaises(ValueError): self.remote.enable()
        with self.assertRaises(ValueError): self.auto.save(dict(kp=1))
        self.remote.stop()
        self.assertFalse(self.auto.active)
        self.auto.start('behind')
        self.remote.power_off(True)
        self.assertFalse(self.auto.active)

    def test_zero_mode_requires_explicit_restore_live_dry_run_preserves_it(self):
        self.remote.power_off(True)
        with self.assertRaises(ValueError): self.auto.start('behind', False)
        before = list(self.bus.writes)
        self.auto.start('behind')
        self.tick()
        self.assertTrue(self.remote.outputs_zero)
        self.assertEqual(before, self.bus.writes)

    def test_live_limits_and_validation(self):
        self.auto.save(dict(launch_throttle=51))
        with self.assertRaises(ValueError): self.auto.start('behind', False)
        for changes in (dict(kp=float('nan')), dict(kp=True), dict(unknown=1), dict(confirm_frames=1.5), dict(roll_direction=0)):
            with self.assertRaises(ValueError): self.auto.save(changes)

    def test_pd_polarity_deadband_and_saturation(self):
        self.auto.save(dict(kp=200, kd=0, max_roll=3, roll_direction=-1))
        self.track()
        self.tick()
        self.assertEqual(self.auto.command['roll'], -3)

    def test_vertical_feedback_is_disabled_by_default(self):
        self.track()
        feedback = self.auto.status()['vertical_feedback']
        self.assertLess(feedback['error'], 0)
        self.assertEqual(feedback['throttle_correction'], 0)

    def test_vertical_throttle_direction_deadband_and_bounds(self):
        self.auto.save(dict(setpoint_y=.22, filter_alpha=1, vertical_kp=100,
                            vertical_kd=0, max_throttle_correction=6, association_gate=.4))
        self.remote.limits['throttle'] = 32
        self.auto.save(dict(cruise_throttle=30))
        self.track()
        for y, correction in ((.24, 0), (.4, 2), (.1, -6)):
            self.tick(y=y)
            self.assertAlmostEqual(self.auto.status()['vertical_feedback']['throttle_correction'], correction)
        self.tick(x=None)
        self.assertEqual(self.auto.state, 'lost')
        self.assertIsNone(self.auto.status()['vertical_feedback'])
        self.assertEqual(self.auto.command['throttle'], 0)

    def test_vertical_velocity_uses_fresh_frames_and_resets_after_loss(self):
        self.auto.save(dict(setpoint_y=.22, filter_alpha=1, vertical_kp=0,
                            vertical_kd=10, max_throttle_correction=10))
        self.track()
        self.tick(y=.27)
        self.assertAlmostEqual(self.auto.status()['vertical_feedback']['throttle_correction'], 5)
        self.tick(y=.22, frames=self.frame)
        self.assertAlmostEqual(self.auto.status()['vertical_feedback']['throttle_correction'], 5)
        self.tick(x=None)
        self.tick(y=.27)
        self.tick(y=.27)
        self.assertEqual(self.auto.state, 'tracking')
        self.assertEqual(self.auto.status()['vertical_feedback']['throttle_correction'], 0)

    def test_settings_persist_but_session_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'autonomous-settings.json'
            auto = Autonomous(self.remote, path)
            auto.save(dict(kp=52))
            restored = Autonomous(self.remote, path)
            self.assertEqual(restored.config['kp'], 52)
            self.assertFalse(restored.active)

    def test_max_duration_and_arming_wait(self):
        self.auto.save(dict(max_flight_seconds=1))
        with patch.object(self.remote, 'initialize'):
            self.auto.start('behind', False)
        self.remote.initializing = True
        self.tick()
        self.assertEqual(self.auto.state, 'arming')
        self.tick(1.01)
        self.assertFalse(self.auto.active)

    def test_feedback_rate_validation_and_legacy_settings_default(self):
        self.auto.save(dict(feedback_hz=60))
        self.assertEqual(self.auto.config['feedback_hz'], 60)
        for value in (0, 1, 61, True, float('nan')):
            with self.assertRaises(ValueError): self.auto.save(dict(feedback_hz=value))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'kp': 42}))
            restored = Autonomous(self.remote, path)
            self.assertEqual(restored.config['feedback_hz'], 10)
            self.assertEqual(restored.config['kp'], 42)

    def test_repeated_frames_do_not_inflate_feedback_observations(self):
        self.auto.start('behind')
        for index in range(10):
            self.tick(frames=index // 2)
        rates = self.auto.status()['feedback']
        self.assertAlmostEqual(rates['loop_hz'], 10)
        self.assertAlmostEqual(rates['observation_hz'], 5)
        self.assertEqual(self.auto.confirmed, 5)

    def test_capture_resolution_change_stops_active_tracking(self):
        self.track()
        self.tick(frame_size=[1920,1080])
        self.assertFalse(self.auto.active)
        self.assertIn('geometry changed',self.auto.reason)


if __name__ == '__main__':
    unittest.main()
