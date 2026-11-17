import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import MagicMock, patch

from PIL import Image

from pi_vision.studio.training_data import Dataset, validate_boxes
from pi_vision.studio.training_studio import create_app, fetch_camera, relative_geometry


def picture(color='white', size=(160, 90)):
    result = io.BytesIO()
    Image.new('RGB', size, color).save(result, format='PNG')
    return result.getvalue()


BOXES = [dict(class_id=0, x=.25, y=.4, w=.2, h=.2), dict(class_id=1, x=.7, y=.4, w=.1, h=.3)]


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / 'data', runs=self.root / 'runs')
        self.app.testing = True
        self.client = self.app.test_client()
        self.dataset = self.app.extensions['dataset']

    def tearDown(self):
        self.app.extensions['training_job'].stop()
        self.temp.cleanup()

    def seed(self):
        a, _ = self.dataset.add(picture('white'), 'flight-a', 'train')
        b, _ = self.dataset.add(picture('blue'), 'flight-b', 'val')
        self.dataset.save(a, BOXES)
        self.dataset.save(b, BOXES)
        return a, b

    def test_import_normalize_deduplicate_and_session_separation(self):
        first, added = self.dataset.add(picture(), 'a', 'train')
        second, duplicate = self.dataset.add(picture(), 'b', 'val')
        self.assertEqual(first, second)
        self.assertTrue(added)
        self.assertFalse(duplicate)
        with self.assertRaises(ValueError):
            self.dataset.add(picture('red'), 'a', 'val')
        with self.assertRaises(ValueError):
            self.dataset.add(picture(), '../escape', 'train')

    def test_export_is_immutable_and_excludes_unreviewed(self):
        a, _ = self.seed()
        negative, _ = self.dataset.add(picture('black'), 'flight-a', 'train')
        self.dataset.save(negative, [])
        self.dataset.add(picture('red'), 'flight-a', 'train')
        folder = self.dataset.export()
        config = json.loads((folder / 'data.yaml').read_text())
        self.assertEqual(config['names'], ['plane', 'target'])
        self.assertEqual(len(list((folder / 'images/train').iterdir())), 2)
        self.assertEqual((folder / 'labels/train' / (negative + '.txt')).read_text(), '')
        label = folder / 'labels/train' / (a + '.txt')
        before = label.read_text()
        self.dataset.save(a, [])
        self.assertEqual(label.read_text(), before)

    def test_export_requires_both_classes_in_independent_splits(self):
        a, _ = self.dataset.add(picture(), 'a', 'train')
        self.dataset.save(a, BOXES)
        with self.assertRaisesRegex(ValueError, 'separate train'):
            self.dataset.export()
        b, _ = self.dataset.add(picture('blue'), 'b', 'val')
        self.dataset.save(b, BOXES[:1])
        with self.assertRaisesRegex(ValueError, 'target in val'):
            self.dataset.export()

    def test_native_crop_export_keeps_session_splits_and_originals(self):
        first, second = self.seed()
        before = self.dataset.list()
        response = self.client.post('/api/export', json={'preparation': 'adaptive'})
        self.assertEqual(response.status_code, 200, response.json)
        folder = Path(response.json['path']).parent
        self.assertEqual(json.loads((folder/'preparation.json').read_text())['profile'], 'adaptive')
        for ident, split in ((first,'train'),(second,'val')):
            crops = list((folder/'images'/split).glob(ident+'-crop-*.jpg'))
            self.assertTrue(crops)
            with Image.open(crops[0]) as image:
                self.assertEqual(image.size,(640,640))
            other = 'val' if split == 'train' else 'train'
            self.assertFalse(list((folder/'images'/other).glob(ident+'*')))
            self.assertTrue((folder/'images'/split/(ident+'.jpg')).exists())
        self.assertEqual(self.dataset.list(), before)

    def test_box_validation_rejects_outside_nan_invalid_class(self):
        for changes in [dict(x=-.1), dict(w=0), dict(x=float('nan')), dict(class_id=2), dict(class_id=True), dict(h=True)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_boxes([{**BOXES[0], **changes}])
        self.assertEqual(validate_boxes([]), [])

    def test_api_save_export_and_origin_protection(self):
        response = self.client.post('/api/import', data=dict(images=(io.BytesIO(picture()), 'a.png'), session='a', split='train'))
        self.assertEqual(response.status_code, 200)
        ident = response.json['ids'][0]
        response = self.client.post('/api/images/' + ident, json={'boxes': BOXES})
        self.assertEqual(response.status_code, 200)
        downloaded_image = self.client.get('/images/' + ident + '.jpg')
        self.assertEqual(downloaded_image.mimetype, 'image/jpeg')
        downloaded_image.close()
        b, _ = self.dataset.add(picture('blue'), 'b', 'val')
        self.dataset.save(b, BOXES)
        exported = self.client.post('/api/export', json={})
        self.assertEqual(exported.status_code, 200)
        downloaded = self.client.get(exported.json['download'])
        self.assertEqual(downloaded.status_code, 200)
        downloaded.close()
        self.assertEqual(self.client.post('/api/train/stop', json={}, headers={'Origin': 'https://elsewhere.test'}).status_code, 403)
        self.assertEqual(self.client.get('/', headers={'Host': 'elsewhere.test'}).status_code, 403)

    def test_unknown_model_and_bad_training_options_do_not_spawn(self):
        self.seed()
        for options in [{'model': '../malicious.pt'}, {'epochs': True}, {'batch': 0}, {'imgsz': 641}, {'device': 'bad'}]:
            response = self.client.post('/api/train', json=options)
            self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.app.extensions['training_job'].process)
        self.assertEqual(self.client.post('/api/predict', json={'weights': '../../arbitrary.pt'}).status_code, 400)

    def test_geometry_never_claims_passage_or_guesses_ambiguous_target(self):
        detections = [dict(label='plane', box=[0, 0, 20, 20]), dict(label='target', box=[40, 20, 60, 40])]
        geometry = relative_geometry(detections, 100, 100)
        self.assertEqual(geometry['dx'], .4)
        self.assertEqual(geometry['dy'], .2)
        self.assertFalse(relative_geometry(detections + [detections[1]], 100, 100)['ready'])
        self.assertFalse(relative_geometry([], 100, 100)['ready'])

    def test_camera_requires_fresh_clean_frame(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {'X-Frame-Age': '.1', 'X-Frame-Sequence': '42'}
        response.read.return_value = picture()
        with patch('pi_vision.studio.training_studio.urllib.request.urlopen', return_value=response) as opened:
            content, sequence = fetch_camera('http://camera')
            self.assertEqual(sequence, '42')
            self.assertEqual(content, picture())
            self.assertEqual(opened.call_args.args[0], 'http://camera/raw.jpg')
            response.headers['X-Frame-Age'] = '5'
            with self.assertRaisesRegex(ValueError, 'stale'):
                fetch_camera('http://camera')
            response.headers = {}
            with self.assertRaisesRegex(ValueError, 'stale'):
                fetch_camera('http://camera')

    def test_predict_uses_selected_static_detector_size(self):
        ident, _ = self.seed()
        weights = self.root / 'runs' / 'run' / 'weights' / 'best.pt'
        weights.parent.mkdir(parents=True)
        weights.touch()
        model = MagicMock()
        model.names = {0: 'plane', 1: 'target'}
        result = MagicMock()
        result.orig_shape = (90, 160)
        result.boxes = []
        model.predict.return_value = [result]
        module = MagicMock()
        module.YOLO.return_value = model
        with patch.dict(sys.modules, {'ultralytics': module}):
            response = self.client.post('/api/predict', json=dict(weights='run/weights/best.pt',
                image_id=ident, imgsz=1280))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['detector_input_size'], 1280)
        self.assertEqual(model.predict.call_args.kwargs['imgsz'], 1280)
        self.assertFalse(model.predict.call_args.kwargs['rect'])

    def test_native_tile_prediction_keeps_full_camera_geometry(self):
        ident,_ = self.dataset.add(picture(size=(1920,1080)), 'flight-a', 'train')
        weights = self.root/'runs'/'run'/'weights'/'best.pt'
        weights.parent.mkdir(parents=True)
        weights.touch()
        model = MagicMock()
        model.names = {0:'plane',1:'target'}
        sizes=[]
        def prediction(source,**options):
            sizes.append(source.size)
            result=MagicMock()
            result.boxes=[]
            return [result]
        model.predict.side_effect=prediction
        module=MagicMock()
        module.YOLO.return_value=model
        with patch.dict(sys.modules,{'ultralytics':module}):
            response=self.client.post('/api/predict',json=dict(weights='run/weights/best.pt',
                image_id=ident,imgsz=640,preparation='adaptive'))
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['tiles'],8)
        self.assertEqual(response.json['width'],1920)
        self.assertEqual(response.json['height'],1080)
        self.assertEqual(sizes,[(640,640)]*8)
        self.assertEqual(response.json['mode'],'observation_only')

    def test_batch_discard_validates_before_deleting(self):
        first, _ = self.dataset.add(picture('red'), 'batch', 'train')
        second, _ = self.dataset.add(picture('blue'), 'batch', 'train')
        for ids in ([], [first, 'bad'], [first, 'a' * 32], 'bad', None):
            response = self.client.post('/api/images/discard', json={'ids': ids})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(len(self.dataset.list()), 2)
        response = self.client.post('/api/images/discard', json={'ids': [first, second, first]})
        self.assertEqual(response.json, {'deleted': 2})
        self.assertEqual(self.dataset.list(), [])
        self.assertFalse((self.dataset.images / (first + '.jpg')).exists())
        self.assertFalse((self.dataset.images / (second + '.jpg')).exists())

    def test_discard_preserves_export_snapshot(self):
        a, _ = self.seed()
        export = self.dataset.export()
        result = self.client.post('/api/images/' + a + '/discard', json={})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.dataset.list()), 1)
        self.assertFalse((self.dataset.images / (a + '.jpg')).exists())
        self.assertTrue((export / 'images/train' / (a + '.jpg')).is_file())
        self.assertEqual(self.client.post('/api/images/../../unrelated/discard', json={}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
