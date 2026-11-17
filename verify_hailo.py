"""Run a compiled two-class detector on saved images on the Pi.

Stop the camera inference service before running: it owns the Hailo device.
Uses the same preprocessing and decoder as the live camera application.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--imgsz', type=int, help='Assert the compiled detector size when validating a candidate.')
    parser.add_argument('--threshold', type=float, default=.2)
    parser.add_argument('--preparation', choices=('full', 'adaptive'), default='full')
    args = parser.parse_args()
    import cv2
    import numpy as np
    from app import letterbox, decode_detections
    from inference import HailoModel
    from spatial_detection import search_regions, translate_detections, merge_detections
    if not .05 <= args.threshold <= .95:
        parser.error('Threshold must be .05-.95.')

    paths = sorted(p for p in args.images.iterdir() if p.suffix.lower() in ('.jpg', '.png'))
    if not paths:
        parser.error('No validation images found.')
    records = []
    with args.model.open('rb') as stream:
        model_sha256 = hashlib.file_digest(stream,'sha256').hexdigest()
    with HailoModel(str(args.model)) as model:
        print('Model streams:', model.describe(), flush=True)
        shape = model.get_input_shape()
        if args.imgsz is not None and tuple(shape) != (args.imgsz, args.imgsz, 3):
            raise ValueError(f'Expected {args.imgsz} square input, got {shape}')
        if len(shape) != 3 or shape[2] != 3 or any(v < 32 or v % 32 for v in shape[:2]):
            raise ValueError(f'Expected static RGB input with height/width divisible by 32, got {shape}')
        if args.preparation == 'adaptive' and tuple(shape[:2]) != (640, 640):
            raise ValueError('Native tiles require a fixed 640x640 HEF.')
        for path in paths:
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError(f'Cannot decode {path}')
            started = time.monotonic()
            height, width = frame.shape[:2]
            regions = search_regions(width,height,640) if args.preparation == 'adaptive' else [(0,0,width,height)]
            detections = []
            inference_ms = 0
            for region in regions:
                x,y,w,h = region
                crop = frame[y:y+h,x:x+w]
                rgb, transform = letterbox(crop, shape, args.preparation != 'adaptive')
                began = time.monotonic()
                result = model.run(rgb)
                inference_ms += (time.monotonic()-began)*1000
                local = decode_detections(result, ['plane','target'], args.threshold, shape, crop.shape, transform)
                detections.extend(translate_detections(local, region))
            if args.preparation == 'adaptive':
                detections = merge_detections(detections)
            elapsed = (time.monotonic() - started) * 1000
            records.append(dict(image=path.name, detections=detections, inference_ms=round(inference_ms, 2),
                                elapsed_ms=round(elapsed,2), frame_size=[width,height],
                                model_sha256=model_sha256,
                                model_input_shape=list(shape),tiles=len(regions),preparation=args.preparation))
    args.output.write_text(json.dumps(records, indent=2))
    print(json.dumps(dict(images=len(records), detections=sum(len(r['detections']) for r in records),
                          mean_inference_ms=round(float(np.mean([r['inference_ms'] for r in records])), 2))),
          flush=True)


if __name__ == '__main__':
    main()
