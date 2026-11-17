from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from camera_modes import choose_sensor_mode
from camera_state import LatestFrame

MODES = [dict(size=(1536, 864), bit_depth=10, fps=120.13, crop_limits=(784,456,3072,1728)),
         dict(size=(2304,1296), bit_depth=10, fps=56.03, crop_limits=(16,24,4608,2592)),
         dict(size=(4608,2592), bit_depth=10, fps=14.35, crop_limits=(16,24,4608,2592))]


class CaptureModeTests(unittest.TestCase):
    def test_1080p50_uses_full_view_binned_sensor_mode(self):
        mode = choose_sensor_mode(MODES, 1920, 1080, 50)
        self.assertEqual(mode['size'], (2304,1296))
        self.assertGreater(mode['fps'], 50)
        with self.assertRaises(ValueError): choose_sensor_mode(MODES, 1920,1080,60)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        with patch('camera_state.ROOT', self.root):
            self.shared = LatestFrame(SimpleNamespace(width=960,height=540,fps=15,camera=0))
        self.shared.sensor_modes = MODES
        self.shared.recorder = MagicMock()
        self.shared.recorder.status.return_value = {'active':False}

    def tearDown(self):
        self.directory.cleanup()

    def test_resolution_and_rate_persist_together_and_reject_invalid_mode(self):
        self.shared.change_settings(dict(width=1920,height=1080,fps=50))
        self.assertTrue((self.root/'camera-settings.json').exists())
        before = dict(self.shared.settings)
        with self.assertRaises(ValueError): self.shared.change_settings(dict(fps=60))
        self.assertEqual(self.shared.settings,before)
        with patch('camera_state.ROOT',self.root):
            reloaded = LatestFrame(SimpleNamespace(width=960,height=540,fps=15,camera=0))
        self.assertEqual(reloaded.settings['width'],1920)
        self.assertEqual(reloaded.settings['fps'],50)

    def test_active_recording_locks_geometry(self):
        self.shared.recorder.status.return_value = {'active':True}
        with self.assertRaises(ValueError): self.shared.change_settings(dict(width=1920,height=1080,fps=50))
        self.assertEqual(self.shared.revision,0)

    def test_maintenance_guard_rejects_recording_before_reconfiguration(self):
        with patch('camera_state.operation_guard',side_effect=ValueError('Inspection busy')):
            with self.assertRaisesRegex(ValueError,'Inspection busy'):
                self.shared.start_recording(dict(seconds=10))
        self.shared.recorder.start.assert_not_called()

    def test_float_zoom_validation_is_finite(self):
        self.shared.limits['zoom_supported'] = True
        self.shared.change_settings({'zoom':1.5})
        for value in (float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.shared.change_settings({'zoom': value})

    def test_record_start_waits_for_applied_1080p50_mode(self):
        def camera_applies():
            with self.shared.condition:
                self.shared.condition.wait_for(lambda:self.shared.revision>0,timeout=2)
                self.shared.frame_size=(1920,1080)
                self.shared.applied_revision=self.shared.revision
                self.shared.last_frame=time.monotonic()
                self.shared.info.update(state='running',camera_fps=50)
                self.shared.condition.notify_all()
        worker=threading.Thread(target=camera_applies)
        worker.start()
        # The producer normally wakes once per camera frame; notify it after settings change.
        original=self.shared.change_settings
        def change(changes):
            result=original(changes)
            self.shared.condition.notify_all()
            return result
        with patch.object(self.shared,'change_settings',side_effect=change):
            self.shared.start_recording(dict(resolution='1920x1080',fps=50,seconds=3))
        worker.join(timeout=3)
        self.shared.recorder.start.assert_called_once_with(50,(1920,1080),3)
