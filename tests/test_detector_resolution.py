import json
from pathlib import Path
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import onnx
from onnx import helper, TensorProto
from PIL import Image

from pi_vision.studio.compile_hailo import calibration_samples, check_stage_input, nms_config, onnx_image_size, main


class DetectorResolutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def onnx(self, shape):
        path = self.root / 'model.onnx'
        graph = helper.make_graph([], 'shape-test', [helper.make_tensor_value_info('images', TensorProto.FLOAT, shape)], [])
        onnx.save(helper.make_model(graph), path)
        return path

    def test_input_size_is_read_from_real_onnx_metadata(self):
        for size in (640, 960, 1280):
            self.assertEqual(onnx_image_size(self.onnx([1, 3, size, size])), size)

    def test_dynamic_wrong_layout_and_unsupported_shapes_rejected(self):
        for shape in ([1, 3, 'height', 'width'], [1, 960, 960, 3], [2, 3, 960, 960],
                      [1, 3, 960, 640], [1, 3, 961, 961], [1, 3, 2048, 2048]):
            with self.assertRaises(ValueError): onnx_image_size(self.onnx(shape))

    def test_calibration_letterboxes_1080p_to_selected_size_in_rgb(self):
        path = self.root / 'frame.png'
        Image.new('RGB', (1920, 1080), (240, 20, 10)).save(path)
        for size in (640, 960, 1280):
            samples = calibration_samples([path], size)
            self.assertEqual(samples.shape, (1, size, size, 3))
            self.assertEqual(samples.dtype, np.uint8)
            content_height = round(1080 * size / 1920)
            top = (size - content_height) // 2
            np.testing.assert_array_equal(samples[0, top - 1, size // 2], [114, 114, 114])
            np.testing.assert_array_equal(samples[0, top, size // 2], [240, 20, 10])
            np.testing.assert_array_equal(samples[0, top + content_height, size // 2], [114, 114, 114])

    def test_nms_uses_selected_resolution_with_original_head_mapping(self):
        outputs = ['reg8', 'cls8', 'reg16', 'cls16', 'reg32', 'cls32']
        for size in (640, 960, 1280):
            config = nms_config(outputs, size)
            self.assertEqual(config['image_dims'], [size, size])
            self.assertEqual(config['bbox_decoders'][1], dict(name='bbox_decoder_16', stride=16, reg_layer='reg16', cls_layer='cls16'))

    def test_resume_rejects_another_resolution_or_onnx_checkpoint(self):
        metadata = dict(input_size=960, onnx_sha256='new-model')
        path = self.root / 'quantized-input.json'
        for stale in (dict(input_size=640, onnx_sha256='new-model'), dict(input_size=960, onnx_sha256='old-model')):
            path.write_text(json.dumps(stale))
            with self.assertRaises(ValueError): check_stage_input(self.root, 'quantized', metadata)
        path.write_text(json.dumps(metadata))
        check_stage_input(self.root, 'quantized', metadata)

    def test_unidentified_large_har_cannot_be_resumed(self):
        with self.assertRaises(ValueError):
            check_stage_input(self.root, 'parsed', dict(input_size=1280, onnx_sha256='model'))

    def test_compile_pipeline_uses_one_resolution_for_calibration_and_nms(self):
        source = self.onnx([1, 3, 1920, 1920])
        Image.new('RGB', (1920, 1080), (240, 20, 10)).save(self.root / 'frame.png')
        runner = MagicMock()
        runner.get_hn_model.return_value.get_output_layers.return_value = [
            SimpleNamespace(inputs=['plane_target/' + name]) for name in
            ('reg8', 'cls8', 'reg16', 'cls16', 'reg32', 'cls32')]
        runner.compile.return_value = b'fake-hef-for-unit-test'
        output = self.root / 'compiled'
        sdk = SimpleNamespace(ClientRunner=MagicMock(return_value=runner))
        with patch.dict(sys.modules, {'hailo_sdk_client': sdk}), patch.object(sys, 'argv', [
            'compile_hailo.py', '--onnx', str(source), '--calibration', str(self.root), '--output', str(output)]):
            main()
        self.assertEqual(runner.optimize.call_args.args[0].shape, (1, 1920, 1920, 3))
        self.assertEqual(json.loads((output / 'nms_config.json').read_text())['image_dims'], [1920, 1920])
        self.assertEqual(json.loads((output / 'model-input.json').read_text())['input_size'], 1920)

    def test_cli_can_compile_existing_weights_without_training(self):
        import pi_vision.studio.train_yolo as train_yolo
        data = self.root / 'data.yaml'
        data.write_text('{}')
        weights = self.root / 'best.pt'
        torch = MagicMock()
        torch.__version__ = 'test'
        torch.cuda.is_available.return_value = False
        ultralytics = MagicMock()
        with patch.dict(sys.modules, {'torch': torch, 'ultralytics': ultralytics}), \
             patch.object(sys, 'argv', ['train_yolo.py', 'compile', '--data', str(data),
                 '--model', str(weights), '--imgsz', '960', '--name', 'candidate-960']), \
             patch('pi_vision.studio.train_yolo.compile_weights') as compile_weights:
            train_yolo.main()
        compile_weights.assert_called_once_with(weights, data, 'candidate-960', 960)
        ultralytics.YOLO.assert_not_called()
