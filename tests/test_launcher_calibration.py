from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, call, patch

from pi_vision.control.launcher_calibration import jog, positions, save_position, stop_signal, reload_launcher, prepare_launch, release_launcher
from pi_vision.control.remote_control import Remote
from test_remote_control import FakeBus


class LauncherCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.remote = Remote(FakeBus())
        self.data = dict(direction='clockwise', unloaded=True)
        self.channel = MagicMock()
        self.attributes = {name: MagicMock() for name in ('enable', 'duty_cycle', 'period', 'polarity')}
        self.channel.__truediv__.side_effect = self.attributes.__getitem__
        patcher = patch('pi_vision.control.launcher_calibration.pwm_channel', return_value=self.channel)
        self.lookup = patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch('pi_vision.control.launcher_calibration.time.sleep')
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_hardware_pwm_sequence(self):
        self.assertEqual(jog(self.remote, self.data), {'direction': 'clockwise', 'pulse_us': 1200, 'duration_seconds': 1.5})
        self.attributes['period'].write_text.assert_called_once_with('20000000')
        self.attributes['polarity'].write_text.assert_called_once_with('normal')
        self.assertEqual(self.attributes['duty_cycle'].write_text.call_args_list,
                         [call('0'), call('1500000'), call('1200000'), call('1500000'), call('0')])
        self.assertEqual(self.attributes['enable'].write_text.call_args_list,
                         [call('0'), call('1'), call('0')])
        self.assertEqual(self.sleep.call_args_list, [call(1), call(1.5), call(1.5)])

    def test_interrupted_move_disables_hardware(self):
        self.sleep.side_effect = RuntimeError('test failure')
        with self.assertRaisesRegex(ValueError, 'test failure'):
            jog(self.remote, self.data)
        self.attributes['duty_cycle'].write_text.assert_called_with('0')
        self.attributes['enable'].write_text.assert_called_with('0')

    def test_anticlockwise_uses_1800_microseconds(self):
        jog(self.remote, {**self.data, 'direction': 'anticlockwise'})
        self.attributes['duty_cycle'].write_text.assert_any_call('1800000')

    def test_unsafe_or_invalid_requests_never_open_gpio(self):
        for change in (dict(unloaded=False), dict(direction='wrong'), dict(direction=[]),
                       dict(direction=None), dict(pulse_us=1500), dict(duration_seconds=5)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                jog(self.remote, {**self.data, **change})
        self.remote.autonomy.active = True
        with self.assertRaisesRegex(ValueError, 'Stop aircraft controls'):
            jog(self.remote, self.data)
        self.remote.autonomy.active = False
        self.remote.launch_input = SimpleNamespace(config={'pin': 12})
        with self.assertRaisesRegex(ValueError, 'launch input'):
            jog(self.remote, self.data)
        self.lookup.assert_not_called()

    def test_positions_are_persisted_and_recalled_without_center_return(self):
        with TemporaryDirectory() as directory:
            self.remote.calibration_path = Path(directory) / 'remote-calibration.json'
            for position, pulse in (('lock', 1400), ('release', 2550)):
                jog(self.remote, dict(pulse_us=pulse, unloaded=True))
                save_position(self.remote, dict(position=position, pulse_us=pulse))
            self.assertEqual(positions(self.remote)['lock_us'], 1400)
            restarted = Remote(FakeBus(), self.remote.calibration_path)
            self.assertEqual(positions(restarted)['release_us'], 2550)
            self.attributes['duty_cycle'].write_text.reset_mock()
            self.sleep.reset_mock()
            jog(restarted, dict(position='release', unloaded=True))
            self.assertEqual(self.attributes['duty_cycle'].write_text.call_args_list,
                             [call('0'), call('2550000')])
            self.assertEqual(self.sleep.call_args_list, [call(1.5)])
            self.attributes['enable'].write_text.assert_called_with('1')
            stop_signal(restarted)
            self.attributes['enable'].write_text.assert_called_with('0')
            self.assertIsNone(positions(restarted)['last_pulse_us'])

    def test_failed_position_command_disables_pwm(self):
        self.sleep.side_effect = RuntimeError('test failure')
        with self.assertRaisesRegex(ValueError, 'test failure'):
            jog(self.remote, dict(pulse_us=2300, unloaded=True))
        self.attributes['enable'].write_text.assert_called_with('0')

    def test_unsaved_or_unapplied_positions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Save this launcher position first'):
            jog(self.remote, dict(position='lock', unloaded=True))
        with self.assertRaisesRegex(ValueError, 'Apply this pulse'):
            save_position(self.remote, dict(position='lock', pulse_us=1400))
        self.lookup.assert_not_called()

    def test_reload_and_launch_use_saved_endpoints(self):
        with TemporaryDirectory() as directory:
            self.remote.calibration_path = Path(directory) / 'remote-calibration.json'
            for position, pulse in (('lock', 2400), ('release', 2600)):
                jog(self.remote, dict(pulse_us=pulse, unloaded=True))
                save_position(self.remote, dict(position=position, pulse_us=pulse))
            with self.assertRaisesRegex(ValueError, 'Reload the launcher'):
                prepare_launch(self.remote)
            self.assertTrue(reload_launcher(self.remote)['loaded'])
            for name, value in dict(enable='1', period='20000000', duty_cycle='2400000').items():
                self.attributes[name].read_text.return_value = value
            pulse = prepare_launch(self.remote)
            self.assertEqual(pulse, 2600)
            self.assertFalse(positions(self.remote)['loaded'])
            self.sleep.reset_mock()
            release_launcher(self.remote, pulse)
            self.attributes['duty_cycle'].write_text.assert_called_with('2600000')
            self.sleep.assert_not_called()
            self.remote.autonomy.active = True
            with self.assertRaisesRegex(ValueError, 'Stop aircraft controls'):
                reload_launcher(self.remote)
            with self.assertRaisesRegex(ValueError, 'Stop autonomous controls'):
                stop_signal(self.remote)
