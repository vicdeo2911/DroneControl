"""Read-only live camera benchmark; never starts or changes aircraft controls."""
import argparse
import json
from pathlib import Path
import statistics
import time
from urllib.request import urlopen


def percentile(values, fraction):
    values = sorted(values)
    return values[round((len(values)-1)*fraction)] if values else None


def measure(url, seconds=20):
    samples, unique, previous = [], [], None
    began = time.monotonic()
    while time.monotonic()-began < seconds:
        started = time.monotonic()
        with urlopen(url.rstrip('/')+'/api/status',timeout=3) as response:
            row = json.load(response)
        if not row.get('ready'):
            raise RuntimeError(row.get('error') or 'Camera not ready.')
        samples.append(row)
        if row['frames'] != previous:
            unique.append(dict(observed_at=time.monotonic()-began, **row))
            previous = row['frames']
        time.sleep(max(0,.02-(time.monotonic()-started)))
    models = {row.get('model') for row in samples}
    if len(models) != 1:
        raise RuntimeError('Model changed during measurement; repeat the benchmark.')
    def median(key):
        values = [r[key] for r in samples if isinstance(r.get(key),(int,float))]
        return round(statistics.median(values),2) if values else None
    modes = {}
    for row in unique:
        mode = row.get('detection_mode','full')
        modes[mode] = modes.get(mode,0)+1
    report = dict(model=samples[-1].get('model'), input_shape=samples[-1].get('model_input_shape'),
        capture_size=samples[-1].get('frame_size'), seconds=round(time.monotonic()-began,2),
        camera_fps_median=median('capture_fps'), detection_fps_median=median('fps'),
        inference_ms_median=median('inference_ms'), unique_observations=len(unique),
        modes=modes, frame_age_ms_p50=round(percentile([r['frame_age_seconds']*1000 for r in unique],.5),2),
        frame_age_ms_p95=round(percentile([r['frame_age_seconds']*1000 for r in unique],.95),2),
        notes='Live status sampling. Reported FPS is the service EMA; polling can miss observations. '
              'Search FPS counts tile results, not complete full-view scans. Accuracy requires labelled flight footage.')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://192.168.137.190:8000')
    parser.add_argument('--seconds',type=int,default=20)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 5 <= args.seconds <= 300:
        parser.error('Use 5-300 seconds.')
    report=measure(args.url,args.seconds)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
