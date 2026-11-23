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
        self.auto.save(dict(launch_delay=1, launch_seconds=.1, post_release_throttle_hold=0, confirm_frames=2,
                            acquire_timeout=.5, loss_timeout=.3, stationary_timeout=0))
        self.frame = 0
        prepare = patch('pi_vision.control.autonomous.prepare_launch', return_value=2600)
        self.prepare_launcher = prepare.start()
        self.addCleanup(prepare.stop)
        release = patch('pi_vision.control.autonomous.release_launcher')
        self.release_launcher = release.start()
        self.addCleanup(release.stop)

    def test_launcher_releases_once_after_launch_throttle(self):
        def release(remote, pulse):
            self.assertEqual(pulse, 2600)
            self.assertAlmostEqual(self.auto.command['throttle'], self.auto.config['launch_throttle'])
            self.assertAlmostEqual(remote.throttle, remote.calibration.outputs(
                self.auto.config['launch_throttle'], 0, throttle_limit=remote.limits['throttle'],
                roll_limit=remote.limits['roll'])[0])
        self.release_launcher.side_effect = release
        with patch.object(self.remote, 'initialize'):
            self.auto.start('behind', dry_run=False)
        self.tick()
        self.tick(1.01)
        self.assertEqual(self.auto.state, 'spooling')
        self.release_launcher.assert_not_called()
        for _ in range(20):
            if self.auto.state == 'holding_throttle':
                break
            self.tick()
        self.assertEqual(self.auto.state, 'holding_throttle')
        self.tick(.875)
        self.release_launcher.assert_not_called()
        self.tick(.125)
        self.assertEqual(self.auto.state, 'launch')
        self.release_launcher.assert_called_once_with(self.remote, 2600)
        self.tick(.11)
        self.release_launcher.assert_called_once_with(self.remote, 2600)

    def test_visible_plane_loss_during_throttle_hold_cancels_release(self):
        with patch.object(self.remote, 'initialize'):
            self.auto.start('visible', dry_run=False)
        for _ in range(40):
            if self.auto.state == 'holding_throttle':
                break
            self.tick()
        self.assertEqual(self.auto.state, 'holding_throttle')
        self.tick(.375, x=None)
        self.assertFalse(self.auto.active)
        self.assertIn('Plane lost before launch', self.auto.reason)
        self.tick(1)
        self.release_launcher.assert_not_called()

    def test_launcher_failure_stops_without_retry(self):
        self.release_launcher.side_effect = OSError('PWM failure')
        with patch.object(self.remote, 'initialize'):
            self.auto.start('behind', dry_run=False)
        for _ in range(40):
            if not self.auto.active:
                break
            self.tick()
        self.assertFalse(self.auto.active)
        self.assertIn('Launcher release failed', self.auto.reason)
        self.release_launcher.assert_called_once()

    def test_launcher_preflight_failure_prevents_arming(self):
        self.prepare_launcher.side_effect = ValueError('Reload the launcher')
        with patch.object(self.remote, 'initialize') as initialize:
            with self.assertRaisesRegex(ValueError, 'Reload the launcher'):
                self.auto.start('behind', dry_run=False)
            initialize.assert_not_called()
        self.assertIsNone(self.remote.token)

    def tick(self, dt=.1, x=.3, y=.22, **changes):
        self.now += dt
        self.auto.last_heartbeat = self.now
        self.frame += 1
        info = dict(ready=True, frames=self.frame, frame_age_seconds=0, frame_size=[1000, 500],
                    detections=[] if x is None else [dict(label='plane', confidence=.9,
                                                         box=[x*1000-10, y*500-10, x*1000+10, y*500+10])])
        info.update(changes)
        self.auto.tick(info)

    def track(self, live=False, **options):
        with patch.object(self.remote, 'initialize'):
            self.auto.start('behind', dry_run=not live, **options)
        self.tick()
        self.tick(1.01)
        for _ in range(35):
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

    def test_throttle_output_reports_the_dac_write_during_arming_and_zero_mode(self):
        self.remote.calibration = Calibration(dict(PROFILE, throttle_idle=3.3, throttle_full=.7))
        with patch.object(self.remote, 'initialize'):
            self.auto.start('behind', dry_run=False)
        sent, _ = self.remote.calibration.outputs(100, 0, initialization=True)
        self.remote._output(1, sent)
        status = self.auto.status()
        self.assertEqual(status['command']['throttle'], 0)
        self.assertAlmostEqual(status['throttle_output']['percent'], 100)
        self.assertEqual(status['throttle_output']['volts'], sent)
        self.remote.power_off(confirmed=True)
        self.assertEqual(self.auto.status()['throttle_output'], dict(percent=None, volts=0, zero_mode=True))

    def test_tracking_throttle_applies_turn_boost_and_respects_remote_limit(self):
        self.auto.save(dict(setpoint_y=.1, vertical_kp=100, max_vertical_throttle=6,
                            turn_throttle_boost=25, kp=200, max_roll=20,
                            roll_slew=100, throttle_slew=100, cruise_throttle=30, lost_throttle=12))
        self.track(live=True)
        self.assertEqual(self.auto.throttle_feedback['vertical_correction'], 6)
        self.assertAlmostEqual(self.auto.throttle_feedback['turn_boost'],
                               25 * (self.auto.command['roll'] / 20) ** 2)
        self.tick(.2)
        self.assertEqual(self.auto.throttle_feedback['turn_boost'], 25)
        self.assertEqual(self.auto.command, dict(throttle=50, roll=20))
        throttle_volts, _ = self.remote.calibration.outputs(50, 20)
        self.assertEqual(self.remote.throttle, throttle_volts)
        self.tick(x=None)
        self.assertEqual(self.auto.state, 'lost')
        self.assertEqual(self.auto.command, dict(throttle=12, roll=0))
        self.assertIsNone(self.auto.status()['throttle_feedback'])

    def test_vertical_velocity_damps_a_plane_moving_toward_the_target(self):
        self.auto.save(dict(setpoint_y=.1, vertical_kp=40, vertical_kd=4,
                            vertical_deadband=.02, vertical_filter_alpha=1))
        self.track()
        self.assertAlmostEqual(self.auto.throttle_feedback['vertical_correction'], 4)
        self.tick(y=.2)
        self.assertAlmostEqual(self.auto.throttle_feedback['vertical_correction'], 2.4)
        self.tick(y=.24)
        self.assertAlmostEqual(self.auto.throttle_feedback['vertical_correction'], 6.4)

    def test_post_release_throttle_hold_preserves_steering_and_delays_fallback(self):
        self.auto.save(dict(post_release_throttle_hold=.5, launch_throttle=40, lost_throttle=12, throttle_slew=100))
        self.track(live=True)
        self.assertEqual(self.auto.command['throttle'], 40)
        self.assertGreater(self.auto.command['roll'], 0)
        self.assertIsNone(self.auto.throttle_feedback)

        self.tick(.2)
        self.tick(.18, x=None)  # 0.49 seconds after release.
        self.assertEqual(self.auto.state, 'lost')
        self.assertEqual(self.auto.command, dict(throttle=40, roll=0))
        self.tick(.02, x=None)
        self.assertEqual(self.auto.command, dict(throttle=12, roll=0))

    def test_stop_overrides_post_release_throttle_hold(self):
        self.auto.save(dict(post_release_throttle_hold=1))
        self.track(live=True)
        self.assertEqual(self.auto.command['throttle'], self.auto.config['launch_throttle'])
        self.auto.stop('Pilot stop')
        self.assertFalse(self.auto.active)
        self.assertEqual(self.auto.command, dict(throttle=0, roll=0))
        self.assertEqual(self.remote.throttle, self.remote.calibration.points['throttle_idle'])

    def test_test_target_changes_only_dry_run_steering(self):
        before = list(self.bus.writes)
        with self.assertRaisesRegex(ValueError, 'only for browser dry runs'):
            self.auto.start('behind', dry_run=False, test_target_x=.2)
        with self.assertRaisesRegex(ValueError, 'only for browser dry runs'):
            self.auto.start('behind', source='gpio', test_target_x=.2)
        with self.assertRaisesRegex(ValueError, 'setpoint_x'):
            self.auto.start('behind', test_target_x=1)
        self.track(test_target_x=.2)
        self.tick()
        self.assertLess(self.auto.command['roll'], 0)
        self.auto.stop('Test finished')
        self.assertIsNone(self.auto.status()['test_target_x'])
        self.assertEqual(self.auto.config['setpoint_x'], .5)
        self.assertEqual(self.bus.writes, before)

    def test_launch_behind_without_detection_is_bounded(self):
        self.auto.start('behind')
        self.tick(x=None)
        self.tick(1.01, x=None)
        self.assertEqual(self.auto.state, 'spooling')
        for _ in range(35):
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

    def test_live_motion_filter_ignores_resizing_static_boxes_and_tracks_moving_plane(self):
        self.auto.save(dict(stationary_timeout=.6, acquire_timeout=2))
        with patch.object(self.remote, 'initialize'):
            self.auto.start('behind', dry_run=False)
        self.tick(x=None)
        self.tick(1.01, x=None)
        for _ in range(35):
            if self.auto.state == 'launch':
                break
            self.tick(x=None)
        self.assertEqual(self.auto.state, 'launch')

        def plane_box(center, half_width):
            return dict(label='plane', confidence=.9, box=[center-half_width, 100, center+half_width, 120])

        for radius, noise_center in ((4, 760), (25, 780), (10, 780), (30, 760)):
            self.tick(detections=[plane_box(530, radius), plane_box(noise_center, 15)])
        self.assertEqual(self.auto.state, 'acquiring')
        self.assertEqual(self.auto.plane_detections, 0)
        for center in (300, 320, 338, 356):
            self.tick(detections=[plane_box(530, 15), plane_box(center, 10)])
        self.assertEqual(self.auto.state, 'tracking')
        self.assertEqual(self.auto.preview_noise(self.frame), ((.515, .2, .545, .24),))
        self.assertEqual(self.auto.preview_noise(self.frame + 1), ())
        self.assertEqual(self.auto.plane_detections, 2)
        self.assertGreater(self.auto.command['roll'], 0)
        for _ in range(7):
            self.tick(detections=[plane_box(530, 15), plane_box(356, 35)])
        self.assertEqual(self.auto.state, 'lost')
        self.assertEqual(self.auto.command['roll'], 0)
        self.auto.stop('Test complete.')
        self.assertEqual(self.auto.preview_noise(self.frame), ())

    def test_visible_start_requires_motion_and_can_hold_confirmed_plane(self):
        self.auto.save(dict(stationary_timeout=.2, acquire_timeout=2))
        self.auto.start('visible')
        for center in (.3, .3, .302):
            self.tick(x=center)
        self.assertEqual(self.auto.state, 'waiting_view')
        for center in (.32, .34, .36):
            self.tick(x=center)
        self.assertEqual(self.auto.state, 'countdown')
        for _ in range(12):
            self.tick(x=.36)
        self.assertEqual(self.auto.state, 'spooling')
        self.assertEqual(self.auto.plane_detections, 0)

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

    def start_motion_test(self):
        self.auto.start('behind', predict_gaps=True)
        self.tick()
        self.tick(1.01)
        for _ in range(35):
            if self.auto.state == 'launch':
                break
            self.tick()
        self.assertEqual(self.auto.state, 'launch')

    def test_constant_detection_cannot_acquire_in_motion_test(self):
        self.start_motion_test()
        self.tick(.11)
        for x in (.302, .299, .303):
            self.tick(x=x)
        self.assertEqual(self.auto.state, 'acquiring')
        self.assertIsNone(self.auto.predicted_x)

    def test_prediction_bridges_gap_and_reacquires_real_motion(self):
        before = list(self.bus.writes)
        self.start_motion_test()
        self.tick(.11, x=.3)
        self.tick(.02, x=.33)
        self.tick(.02, x=.35)
        self.assertEqual(self.auto.state, 'tracking')
        last_x = self.auto.filtered_x
        last_detection = self.auto.last_detection
        self.tick(.04, x=None)
        self.assertGreater(self.auto.predicted_x, last_x)
        self.assertLessEqual(self.auto.predicted_x - last_x, .061)
        self.assertEqual(self.auto.last_detection, last_detection)
        self.tick(.02, x=.37)
        self.tick(.02, x=.39)
        self.assertEqual(self.auto.state, 'tracking')
        self.assertIsNone(self.auto.predicted_x)
        self.tick(.03, x=None)
        self.tick(.23, x=None)
        self.assertIsNone(self.auto.predicted_x)
        self.assertEqual(self.auto.command['roll'], 0)
        self.tick(.05, x=None)
        self.assertFalse(self.auto.active)
        self.assertEqual(self.bus.writes, before)

    def test_prediction_is_observational_in_live_flight(self):
        self.track(live=True, predict_gaps=True)
        self.tick(.02, x=.32)
        self.tick(.02, x=None)
        self.assertEqual(self.auto.state, 'lost')
        self.assertIsNotNone(self.auto.predicted_x)
        self.assertEqual(self.auto.command, dict(throttle=0, roll=0))

    def test_prediction_rejects_gpio_start(self):
        with self.assertRaisesRegex(ValueError, 'only for browser sessions'):
            self.auto.start('behind', source='gpio', predict_gaps=True)

    def test_stationary_box_stops_refreshing_a_confirmed_track(self):
        self.start_motion_test()
        self.tick(.11, x=.3)
        self.tick(.02, x=.33)
        self.tick(.02, x=.35)
        self.assertEqual(self.auto.state, 'tracking')
        for _ in range(6):
            self.tick(.05, x=.35)
        self.assertEqual(self.auto.state, 'lost')
        last_detection = self.auto.last_detection
        self.tick(.05, x=.35)
        self.assertEqual(self.auto.last_detection, last_detection)

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
