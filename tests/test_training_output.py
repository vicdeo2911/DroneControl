from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pi_vision.studio.training_studio import TrainingJob
from pi_vision.studio.training_output import workdir, stop_compiler, export_onnx


class TrainingOutputTests(unittest.TestCase):
    @patch('pi_vision.studio.training_studio.subprocess.Popen')
    def test_adaptive_training_and_medium_model_are_explicit_and_size_matched(self, popen):
        popen.return_value.poll.return_value = None
        with tempfile.TemporaryDirectory() as temporary:
            job, dataset = self.job(temporary)
            with self.assertRaisesRegex(ValueError, '640'):
                job.start(dataset, dict(preparation='adaptive', imgsz=960))
            dataset.export.assert_not_called()
            job.start(dataset, dict(preparation='adaptive', imgsz=640, model='yolov8m.pt'))
            dataset.export.assert_called_once_with(profile='adaptive', crop_size=640)
            self.assertIn('yolov8m.pt', popen.call_args.args[0])

    def test_shared_onnx_export_preserves_hailo_options(self):
        model = MagicMock()
        model.export.return_value = 'model.onnx'
        self.assertEqual(export_onnx(model, 640), Path('model.onnx'))
        model.export.assert_called_once_with(format='onnx', imgsz=640, opset=13,
                                            simplify=False, dynamic=False, nms=False)

    def test_higher_resolution_onnx_export(self):
        for size in (960, 1280):
            model = MagicMock()
            model.export.return_value = 'model.onnx'
            export_onnx(model, size)
            self.assertEqual(model.export.call_args.kwargs['imgsz'], size)
            self.assertFalse(model.export.call_args.kwargs['dynamic'])

    def job(self, directory):
        dataset = MagicMock()
        dataset.export.return_value = Path(directory) / 'snapshot'
        return TrainingJob(Path(directory) / 'runs'), dataset

    @patch('pi_vision.studio.training_studio.subprocess.Popen')
    def test_standard_default_never_calls_docker(self, popen):
        popen.return_value.poll.return_value = None
        with tempfile.TemporaryDirectory() as temporary:
            job, dataset = self.job(temporary)
            with patch('pi_vision.studio.training_studio.subprocess.run') as run:
                job.start(dataset, {})
                run.assert_not_called()
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index('--target') + 1], 'yolo')
            self.assertEqual(job.status()['target'], 'yolo')

    @patch('pi_vision.studio.training_studio.subprocess.Popen')
    @patch('pi_vision.studio.training_studio.subprocess.run')
    def test_hailo_checks_compiler_and_passes_target(self, run, popen):
        popen.return_value.poll.return_value = None
        with tempfile.TemporaryDirectory() as temporary:
            job, dataset = self.job(temporary)
            job.start(dataset, dict(target='hailo'))
            self.assertIn('pi-vision-hailo-compiler', run.call_args.args[0])
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index('--target') + 1], 'hailo')

    def test_invalid_target_size_or_missing_compiler_never_start_training(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, dataset = self.job(temporary)
            with patch('pi_vision.studio.training_studio.subprocess.Popen') as popen:
                for options in (dict(target='invalid'), dict(target='hailo', imgsz=1936)):
                    with self.assertRaises(ValueError): job.start(dataset, options)
                with patch('pi_vision.studio.training_studio.subprocess.run', side_effect=FileNotFoundError):
                    with self.assertRaisesRegex(ValueError, 'Docker container'):
                        job.start(dataset, dict(target='hailo'))
                popen.assert_not_called()
                dataset.export.assert_not_called()

    @patch('pi_vision.studio.training_studio.subprocess.Popen')
    @patch('pi_vision.studio.training_studio.subprocess.run')
    def test_hailo_accepts_higher_detector_sizes(self, run, popen):
        popen.return_value.poll.return_value = None
        for size in (960, 1280):
            with tempfile.TemporaryDirectory() as temporary:
                job, dataset = self.job(temporary)
                job.start(dataset, dict(target='hailo', imgsz=size))
                cmd = popen.call_args.args[0]
                self.assertEqual(cmd[cmd.index('--imgsz') + 1], str(size))

    def test_compiler_cancel_is_scoped_to_validated_run_directory(self):
        with self.assertRaises(ValueError): workdir('../escape')
        with patch('pi_vision.studio.training_output.docker') as docker:
            stop_compiler('test-run')
            args = docker.call_args.args
            self.assertEqual(args[-1], '/tmp/pi-vision-test-run/compiler.pid')
            self.assertIn('/proc/', args[-2])


if __name__ == '__main__':
    unittest.main()
