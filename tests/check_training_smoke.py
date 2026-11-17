"""One synthetic GPU epoch: verifies setup, not real detection accuracy."""
import io
from pathlib import Path
import sys
import tempfile
import time

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training_data import Dataset
from training_studio import create_app
from test_training import BOXES


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        dataset = Dataset(root / 'data')
        for split, count in [('train', 8), ('val', 4)]:
            for index in range(count):
                image = Image.new('RGB', (320, 192), (40 + index * 10, 50, 80 if split == 'train' else 90))
                draw = ImageDraw.Draw(image)
                for box in BOXES:
                    x, y, w, h = [box[k] for k in ('x', 'y', 'w', 'h')]
                    draw.rectangle([(x-w/2)*320, (y-h/2)*192, (x+w/2)*320, (y+h/2)*192],
                                   fill='white' if box['class_id'] == 0 else 'orange')
                content = io.BytesIO()
                image.save(content, format='PNG')
                ident, _ = dataset.add(content.getvalue(), split + '-synthetic', split)
                dataset.save(ident, BOXES)
        app = create_app(root / 'data', camera='http://127.0.0.1:1', runs=root / 'runs')
        client = app.test_client()
        job = app.extensions['training_job']
        try:
            job.start(dataset, dict(model='yolov8n.pt', epochs=1, batch=2, imgsz=320, device='0'))
            deadline = time.monotonic() + 300
            while job.status()['active'] and time.monotonic() < deadline:
                time.sleep(1)
            status = job.status()
            if status['active'] or status['exit_code'] != 0:
                raise RuntimeError(status['log'])
            weights = root / 'runs' / status['name'] / 'weights/best.pt'
            assert weights.is_file(), status['log']
            from ultralytics import YOLO
            model = YOLO(str(weights))
            assert model.names == {0: 'plane', 1: 'target'}, model.names
            result = model.predict(Image.new('RGB', (320, 192)), verbose=False, device=0)[0]
            assert result.orig_shape == (192, 320)
            # Exercise the studio's real checkpoint selection and prediction API.
            available = client.get('/api/training').json['weights']
            assert len(available) == 1
            prediction = client.post('/api/predict', json=dict(weights=available[0], image_id=dataset.list()[0]['id']))
            assert prediction.status_code == 200, prediction.json
            assert prediction.json['mode'] == 'observation_only'
            assert prediction.json['passage_confirmed'] is False
            assert prediction.json['width'] == 320
            exported = model.export(format='onnx', imgsz=320, opset=13, simplify=False, dynamic=False, nms=False)
            import onnx
            onnx.checker.check_model(onnx.load(exported))
            app.extensions['training_job'].stop()
            # Release model references before Windows cleans up temporary weights.
            prediction.close()
            print(status['log'][-2200:])
            print('Synthetic CUDA training + checkpoint + studio inference + ONNX checks passed. This is not a trained aircraft model.')
        finally:
            job.stop()


if __name__ == '__main__':
    main()
