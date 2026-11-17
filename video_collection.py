"""Download a completed Pi clip and sample it into the local labeling library."""
import json
import math
import re
import threading
import urllib.error
import urllib.request

from import_video import extract_video


def camera_json(camera, path, data=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(camera.rstrip('/') + path, data=body,
                                 headers={} if body is None else {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read()).get('error', str(exc))
        except ValueError:
            error = str(exc)
        raise ValueError('Pi camera: ' + error) from exc
    except OSError as exc:
        raise ValueError('Pi camera is unavailable: ' + str(exc)) from exc


class VideoExtraction:
    def __init__(self, dataset, camera):
        self.dataset, self.camera = dataset, camera
        self.lock = threading.RLock()
        self.thread = None
        self.current = dict(state='idle', active=False, added=0, sampled=0, error=None)

    def status(self):
        with self.lock:
            return dict(self.current)

    def start(self, ident, split, interval=1, max_frames=1000):
        if not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('Select a completed recording.')
        if split not in ('train', 'val', 'test'):
            raise ValueError('Choose train, val or test.')
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 1 / 60 <= interval <= 60:
            raise ValueError('Frame interval must be 1/60–60 seconds (up to 60 fps).')
        if isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 1 <= max_frames <= 10000:
            raise ValueError('Choose a maximum of 1–10000 extracted frames.')
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise ValueError('An extraction is already running.')
            # Use recording identity as the group, so resampling cannot leak into validation.
            session = 'clip-' + ident
            if any(row['session'] == session and row['split'] != split for row in self.dataset.list()):
                raise ValueError('This recording already belongs to another split.')
            self.current = dict(state='downloading', active=True, id=ident, session=session, split=split,
                                added=0, sampled=0, downloaded_bytes=0, error=None)
            self.thread = threading.Thread(target=self._run, args=(ident, session, split, interval, max_frames), daemon=True)
            self.thread.start()
            return self.status()

    def _update(self, **changes):
        with self.lock:
            self.current.update(changes)

    def _run(self, ident, session, split, interval, max_frames):
        temporary = None
        try:
            metadata = camera_json(self.camera, '/api/recordings/' + ident + '.json')
            if metadata.get('state') != 'completed' or not metadata.get('frames'):
                raise ValueError('Choose a completed clip with camera frames.')
            folder = self.dataset.root / 'videos'
            folder.mkdir(exist_ok=True)
            video = folder / (ident + '.avi')
            temporary = video.with_suffix('.part')
            expected = metadata.get('bytes', 0)
            if not isinstance(expected, int) or not 1 <= expected <= 2 * 1024**3:
                raise ValueError('Recording size is invalid or exceeds 2 GB.')
            if not video.exists() or video.stat().st_size != expected:
                with urllib.request.urlopen(self.camera.rstrip('/') + '/api/recordings/' + ident + '.avi', timeout=30) as source, temporary.open('wb') as output:
                    downloaded = 0
                    while True:
                        chunk = source.read(256 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > expected:
                            raise ValueError('Downloaded recording is larger than its metadata.')
                        output.write(chunk)
                        self._update(downloaded_bytes=downloaded)
                if downloaded != expected:
                    raise ValueError('Recording download was incomplete; retry extraction.')
                temporary.replace(video)
            (folder / (ident + '.json')).write_text(json.dumps(metadata), encoding='utf-8')
            self._update(state='extracting')
            result = extract_video(video, self.dataset, session, split, interval, max_frames,
                                   progress=lambda values: self._update(**values), frame_times=metadata.get('frame_times'))
            self._update(state='completed', active=False, **result)
        except Exception as exc:
            self._update(state='failed', active=False, error=str(exc))
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
