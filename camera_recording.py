"""Bounded background recording of clean camera frames, independent of browsers."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import queue
import re
import shutil
import threading
import time
import uuid

import cv2


def validate_duration(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 300:
        raise ValueError('Recording length must be 1–300 seconds.')


class CameraRecorder:
    FILE_LIMIT = int(1.75 * 1024**3)  # Leave room for queued frames below extraction's 2 GiB limit.
    BUFFER_BYTES = 768 * 1024**2
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.thread = None
        self.current = None
        self.stopping = threading.Event()
        self.pending = queue.Queue(maxsize=30)
        # Preserve unfinished files after power loss; identify them honestly.
        for path in self.root.glob('*.json'):
            try:
                meta = json.loads(path.read_text())
                if meta.get('state') == 'recording':
                    meta.update(state='interrupted', error='Camera service interrupted; video may be incomplete.')
                    path.write_text(json.dumps(meta), encoding='utf-8')
            except (OSError, ValueError):
                continue

    def _active(self):
        return self.thread is not None and self.thread.is_alive()

    def status(self):
        with self.lock:
            result = dict(self.current) if self.current else {'state': 'idle', 'frames': 0}
            if self._active():
                result['elapsed_seconds'] = round(time.monotonic() - self.started, 2)
            result['active'] = self._active()
            return result

    def start(self, fps, size, seconds=60):
        validate_duration(seconds)
        if not math.isfinite(fps) or not 1 <= fps <= 60:
            raise ValueError('Camera frame rate must be 1–60 fps.')
        with self.lock:
            if self._active():
                raise ValueError('A clip is already recording.')
            if shutil.disk_usage(self.root).free < 512 * 1024 * 1024:
                raise ValueError('Keep at least 512 MB free before recording.')
            ident = uuid.uuid4().hex
            writer = cv2.VideoWriter(str(self.root / (ident + '.avi')), cv2.CAP_OPENCV_MJPEG,
                                    cv2.VideoWriter_fourcc(*'MJPG'), float(fps), tuple(size))
            writer.set(cv2.VIDEOWRITER_PROP_NSTRIPES, 4)
            writer.set(cv2.VIDEOWRITER_PROP_QUALITY, 90)
            if not writer.isOpened():
                writer.release()
                raise ValueError('Cannot open the MJPEG video writer.')
            self.current = dict(id=ident, state='recording', started_utc=datetime.now(timezone.utc).isoformat(),
                                fps=float(fps), width=size[0], height=size[1], limit_seconds=seconds,
                                frames=0, dropped=0, elapsed_seconds=0, actual_fps=0, bytes=0, error=None)
            self.current['size_limit_bytes'] = self.FILE_LIMIT
            self.started = time.monotonic()
            self.stopping = threading.Event()
            capacity = max(1, min(max(30, int(fps * 3)), self.BUFFER_BYTES // (size[0] * size[1] * 3)))
            self.pending = queue.Queue(maxsize=capacity)
            self.current['buffer_frames'] = capacity
            try:
                self._persist([])
            except Exception:
                writer.release()
                raise
            self.thread = threading.Thread(target=self._write, args=(writer, size), daemon=True)
            self.thread.start()
            return self.status()

    def submit(self, frame, sequence, *, copy=True):
        with self.lock:
            if not self._active() or self.stopping.is_set():
                return
            stamp = time.monotonic() - self.started
            if stamp >= self.current['limit_seconds']:
                self.stopping.set()
                return
            try:
                # The camera owner draws boxes after this call; retain a clean copy.
                self.pending.put_nowait((frame.copy() if copy else frame, sequence, stamp))
            except queue.Full:
                self.current['dropped'] += 1

    def _persist(self, times):
        meta = dict(self.current, frame_times=times)
        path = self.root / (meta['id'] + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(meta), encoding='utf-8')
        temporary.replace(path)

    def _write(self, writer, size):
        times = []
        encoded_bytes = 0
        try:
            while True:
                if time.monotonic() - self.started >= self.current['limit_seconds']:
                    self.stopping.set()
                if self.stopping.is_set() and self.pending.empty():
                    break
                try:
                    frame, sequence, stamp = self.pending.get(timeout=.1)
                except queue.Empty:
                    continue
                if (frame.shape[1], frame.shape[0]) != tuple(size):
                    raise ValueError('Camera size changed during recording.')
                writer.write(frame)
                encoded_bytes += max(0, int(writer.get(cv2.VIDEOWRITER_PROP_FRAMEBYTES)))
                times.append([sequence, round(stamp, 6)])
                with self.lock:
                    self.current['frames'] = len(times)
                    self.current['bytes'] = encoded_bytes
                    if len(times) > 1:
                        self.current['actual_fps'] = round((len(times) - 1) / max(.001, times[-1][1] - times[0][1]), 2)
                if len(times) % 30 == 0 and shutil.disk_usage(self.root).free < 128 * 1024 * 1024:
                    raise ValueError('Recording stopped: less than 128 MB free on Pi.')
                if len(times) % 30 == 0 and max(encoded_bytes, (self.root / (self.current['id'] + '.avi')).stat().st_size) >= self.current['size_limit_bytes']:
                    with self.lock:
                        self.current['stop_reason'] = 'File size limit reached; start another clip.'
                    self.stopping.set()
        except Exception as exc:
            with self.lock:
                self.current.update(state='failed', error=str(exc))
        finally:
            writer.release()
            with self.lock:
                if self.current['state'] == 'recording':
                    self.current['state'] = 'completed' if times else 'failed'
                    if not times:
                        self.current['error'] = 'No camera frames arrived.'
                self.current['elapsed_seconds'] = round(time.monotonic() - self.started, 2)
                self.current['bytes'] = (self.root / (self.current['id'] + '.avi')).stat().st_size
                try:
                    self._persist(times)
                except OSError as exc:
                    self.current.update(state='failed', error='Could not save recording metadata: ' + str(exc))

    def stop(self):
        with self.lock:
            self.stopping.set()
            worker = self.thread
        if worker:
            worker.join(timeout=10)
        return self.status()

    def list(self):
        result = []
        for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                meta = json.loads(path.read_text())
                meta.pop('frame_times', None)
                result.append(meta)
            except (OSError, ValueError):
                continue
        current = self.status()
        if current.get('id'):
            result = [current] + [r for r in result if r['id'] != current['id']]
        return result

    def file(self, ident, suffix):
        if not re.fullmatch('[0-9a-f]{32}', ident) or suffix not in ('avi', 'json'):
            raise ValueError('Unknown recording.')
        with self.lock:
            if self._active() and self.current['id'] == ident:
                raise ValueError('Stop the clip before downloading or extracting it.')
            path = self.root / (ident + '.' + suffix)
            if not path.is_file():
                raise ValueError('Recording not found.')
            return path
