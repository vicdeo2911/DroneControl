"""Sample a recording into the annotation library. One video = one split."""
import argparse
from pathlib import Path

from training_data import Dataset


def extract_video(video, dataset, session, split, interval=1.0, max_frames=1000, progress=None, frame_times=None):
    import cv2
    import math
    if not math.isfinite(interval) or not 1 / 60 <= interval <= 3600 or not 1 <= max_frames <= 10000:
        raise ValueError('Use interval 1/60–3600 seconds (up to 60 fps) and max frames 1–10000.')
    # Check session assignment even if this video contains duplicate images.
    if any(row['session'] == session and row['split'] != split for row in dataset.list()):
        raise ValueError('This recording already belongs to another split.')
    cap = cv2.VideoCapture(str(Path(video).resolve()))
    try:
        if not cap.isOpened():
            raise ValueError('Cannot open video.')
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('Video does not report a usable frame rate.')
        added = sampled = frame_index = 0
        next_time = 0.0
        while sampled < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            stamp = frame_times[frame_index][1] if frame_times and frame_index < len(frame_times) else frame_index / fps
            if stamp + 1e-7 >= next_time:
                encoded, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if not encoded:
                    raise ValueError('Could not encode extracted frame.')
                _, created = dataset.add(jpeg.tobytes(), session, split)
                added += int(created)
                sampled += 1
                # Keep a fixed sampling schedule: rounding each interval up to a
                # source frame would turn 10 fps from a 15 fps clip into 7.5 fps.
                next_time += interval
                if progress:
                    progress(dict(added=added, sampled=sampled, duplicates=sampled - added))
            frame_index += 1
        if not sampled:
            raise ValueError('The video contains no readable frames.')
        return dict(added=added, sampled=sampled, duplicates=sampled - added, source_fps=fps)
    finally:
        cap.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--split', choices=['train', 'val', 'test'], default='train')
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--max-frames', type=int, default=1000)
    parser.add_argument('--data', type=Path, default=Path('data/plane-target'))
    args = parser.parse_args()
    if not args.video.is_file() or not 1 / 60 <= args.interval <= 3600 or not 1 <= args.max_frames <= 10000:
        parser.error('Check video path, interval (1/60–3600 s), and max frames (1–10000).')
    try:
        result = extract_video(args.video, Dataset(args.data), args.session, args.split, args.interval, args.max_frames)
        print(f"Imported {result['added']} images ({result['duplicates']} duplicates skipped). Open the training studio to label them.")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
