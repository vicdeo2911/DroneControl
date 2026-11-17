from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from test_camera_recording import fake_camera_app
from spatial_detection import search_regions


class DetectionPipelineTests(unittest.TestCase):
    def test_quantized_real_plane_score_survives_deployed_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            app,_=fake_camera_app(directory)
            module=app.extensions['camera_module']
            frame=np.zeros((1080,1920,3),np.uint8)
            _,transform=module.letterbox(frame,(640,640,3))
            detections=module.decode_detections([
                np.array([[.57,.53,.69,.73,.586],[.4,.2,.42,.22,.264]]),
                np.empty((0,5))], ['plane','target'],.55,(640,640,3),frame.shape,transform)
            self.assertEqual(len(detections),1)
            self.assertEqual(detections[0]['confidence'],.586)

    def test_headless_preview_does_not_encode(self):
        with tempfile.TemporaryDirectory() as directory:
            app,shared=fake_camera_app(directory)
            module=app.extensions['camera_module']
            with patch.object(module.cv2,'imencode') as encode:
                module.preview_worker(SimpleNamespace(preview_fps=0),shared)
                encode.assert_not_called()

    def test_control_observation_does_not_wait_for_jpeg_and_crop_restores_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            app, shared = fake_camera_app(directory)
            module = app.extensions['camera_module']
            inputs, outputs = queue.Queue(1), queue.Queue(2)
            outputs.put(dict(kind='ready', model_input_shape=[640,640,3]))
            context = MagicMock()
            context.Queue.side_effect = [inputs, outputs]
            # Queue cleanup uses multiprocessing's additional methods.
            for channel in (inputs,outputs):
                channel.cancel_join_thread = channel.close = lambda: None
            with patch.object(module.multiprocessing, 'get_context', return_value=context):
                shared.capture_image = np.zeros((1080,1920,3),np.uint8)
                shared.capture_sequence = 2
                shared.capture_revision = shared.revision
                shared.capture_time = time.monotonic()
                shared.jpeg = shared.raw_jpeg = None
                worker = threading.Thread(target=module.inference_worker,
                    args=(SimpleNamespace(detection_mode='adaptive'),shared))
                worker.start()
                try:
                    crop = inputs.get(timeout=2)
                    self.assertEqual(crop.shape, (640,640,3))
                    outputs.put(dict(kind='frame', inference_ms=10,
                        detections=[dict(label='plane', confidence=.9, box=[10,20,22,28])]))
                    with shared.condition:
                        self.assertTrue(shared.condition.wait_for(lambda:shared.sequence==2,timeout=2))
                    x,y,_,_ = search_regions(1920,1080,640)[0]
                    # A single crop candidate must not reach the controller as a confirmed plane.
                    self.assertEqual(shared.info['detections'],[])
                    self.assertEqual(shared.preview_job[2],(x,y,640,640))
                    self.assertIsNone(shared.jpeg)
                    self.assertEqual(shared.last_frame,shared.capture_time)
                    self.assertIsNotNone(shared.preview_job)
                finally:
                    shared.stop.set()
                    with shared.condition:
                        shared.condition.notify_all()
                    worker.join(timeout=3)
                self.assertFalse(worker.is_alive())

    def test_preview_preserves_clean_full_resolution_and_original_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            app, shared = fake_camera_app(directory)
            module = app.extensions['camera_module']
            frame = np.zeros((1080,1920,3),np.uint8)
            captured_at = time.monotonic()-.1
            shared.preview_job = (frame,[dict(label='plane',confidence=.9,box=[10,20,30,40])],
                                  (0,0,640,640),'tracking',9,captured_at,shared.revision)
            worker=threading.Thread(target=module.preview_worker,
                args=(SimpleNamespace(preview_fps=60,quality=80),shared))
            worker.start()
            try:
                with shared.condition:
                    self.assertTrue(shared.condition.wait_for(lambda:shared.preview_sequence==9,timeout=3))
                raw=cv2.imdecode(np.frombuffer(shared.raw_jpeg,np.uint8),cv2.IMREAD_COLOR)
                self.assertEqual(raw.shape,frame.shape)
                self.assertEqual(raw.max(),0)
                self.assertEqual(shared.preview_time,captured_at)
                self.assertEqual(frame.max(),0)
                self.assertNotEqual(shared.raw_jpeg,shared.jpeg)
            finally:
                shared.stop.set()
                with shared.condition:
                    shared.condition.notify_all()
                worker.join(timeout=3)

    def test_native_letterbox_pads_small_frames_without_enlarging(self):
        with tempfile.TemporaryDirectory() as directory:
            app,_=fake_camera_app(directory)
            module=app.extensions['camera_module']
            rgb,transform=module.letterbox(np.zeros((192,320,3),np.uint8),(640,640,3),False)
            self.assertEqual(transform,(0,0,1,1))
            self.assertEqual(rgb.shape,(640,640,3))
            expected=np.full((640,640,3),114,np.uint8)
            expected[:192,:320]=0
            np.testing.assert_array_equal(rgb,expected)


if __name__ == '__main__':
    unittest.main()
