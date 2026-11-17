"""Train, validate, or export a local two-class YOLOv8 detector."""
import argparse
import json
from pathlib import Path

from pi_vision.studio.training_output import compile_weights, export_onnx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['train', 'val', 'test', 'export', 'compile'])
    parser.add_argument('--data', type=Path)
    parser.add_argument('--model', default='yolov8s.pt')
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--batch', type=int, default=8)
    parser.add_argument('--imgsz', type=int, default=640)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--project', default='runs/plane-target')
    parser.add_argument('--name', default='train')
    parser.add_argument('--target', choices=['yolo', 'hailo'], default='yolo',
                        help='Keep standard best.pt or additionally compile a Hailo-8 HEF.')
    args = parser.parse_args()
    import torch
    from ultralytics import YOLO
    device = ('0' if torch.cuda.is_available() else 'cpu') if args.device == 'auto' else args.device
    print(json.dumps({'torch': torch.__version__, 'cuda': torch.cuda.is_available(), 'device': device,
                      'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}), flush=True)
    if args.mode != 'export' and (not args.data or not args.data.is_file()):
        parser.error('--data must point to an exported data.yaml.')
    if args.epochs < 1 or args.batch < 1 or args.imgsz < 64 or args.imgsz % 32:
        parser.error('Use positive epochs/batch and an image size divisible by 32 (>=64).')
    if (args.target == 'hailo' or args.mode == 'compile') and not 320 <= args.imgsz <= 1920:
        parser.error('Hailo input size must be from 320 to 1920, divisible by 32.')
    if args.mode == 'compile':
        compile_weights(Path(args.model), args.data, args.name, args.imgsz)
        return
    model_path = Path(args.model)
    if args.model in ('yolov8n.pt', 'yolov8s.pt', 'yolov8m.pt'):
        cache = Path(__file__).resolve().parents[2] / 'models/pretrained'
        cache.mkdir(parents=True, exist_ok=True)
        model_path = cache / args.model
    model = YOLO(str(model_path))
    if args.mode == 'train':
        model.train(data=str(args.data.resolve()), epochs=args.epochs, batch=args.batch,
                    imgsz=args.imgsz, device=device, workers=0, seed=42, deterministic=True,
                    project=args.project, name=args.name, exist_ok=False, patience=25, close_mosaic=10)
        best = Path(model.trainer.best)
        print('Standard YOLO weights ready: ' + str(best), flush=True)
        if args.target == 'hailo':
            compile_weights(best, args.data, args.name, args.imgsz)
    elif args.mode in ('val', 'test'):
        model.val(data=str(args.data.resolve()), split=args.mode, device=device, imgsz=args.imgsz, workers=0)
    else:
        export_onnx(model, args.imgsz)


if __name__ == '__main__':
    main()
