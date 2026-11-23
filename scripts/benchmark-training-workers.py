"""Short, isolated CUDA training benchmark on an existing dataset export.

Run once for each worker count while studio training is stopped. No HEF compile
or deployment occurs; outputs stay in test-artifacts rather than the model list.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--workers', type=int, required=True)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--fraction', type=float, default=.25)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.data.is_file() or not 0 <= args.workers <= 16 or not 0 < args.fraction <= 1:
        parser.error('Use an existing data.yaml, workers 0-16 and fraction (0, 1].')
    with urlopen('http://127.0.0.1:8010/api/training', timeout=4) as response:
        if json.load(response).get('active') is not False:
            raise RuntimeError('Stop studio training before benchmarking.')
    from ultralytics import YOLO
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('This benchmark requires CUDA.')
    started, batches = [], []
    def epoch_start(trainer):
        torch.cuda.synchronize()
        started.append(time.perf_counter())
    def batch_end(trainer):
        batches.append(time.perf_counter())
    def epoch_end(trainer):
        torch.cuda.synchronize()
        duration = time.perf_counter()-started[0]
        intervals = [b-a for a,b in zip(batches[10:], batches[11:])]
        report = dict(workers=args.workers, batch=args.batch, imgsz=640,
                      fraction=args.fraction, batches=len(batches), train_seconds=round(duration, 3),
                      batches_per_second=round(len(batches)/duration, 3),
                      steady_batch_seconds=round(sum(intervals)/len(intervals), 4) if intervals else None,
                      note='One epoch on the same snapshot subset; excludes model setup and validation '
                           'but includes first-epoch loader startup. '
                           'Steady interval excludes the first 10 batches. Not an accuracy benchmark.')
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output/f'workers-{args.workers}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('WORKER_BENCHMARK '+json.dumps(report), flush=True)
    model = YOLO(str(ROOT/'models/pretrained/yolov8s.pt'))
    model.add_callback('on_train_epoch_start', epoch_start)
    model.add_callback('on_train_batch_end', batch_end)
    model.add_callback('on_train_epoch_end', epoch_end)
    model.train(data=str(args.data.resolve()), workers=args.workers, batch=args.batch,
                imgsz=640, device=0, epochs=1, fraction=args.fraction,
                seed=42, deterministic=True, cache=False, close_mosaic=10,
                project=str(args.output.resolve()), name=f'workers-{args.workers}',
                exist_ok=False, save=False, plots=False)


if __name__ == '__main__':
    main()
