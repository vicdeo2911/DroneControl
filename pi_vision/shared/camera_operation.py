"""Cross-process exclusion for camera/NPU maintenance on the Pi."""
from contextlib import contextmanager
import os
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parents[2] / '.camera-operation.lock'


@contextmanager
def operation_guard():
    if os.name != 'posix':
        yield
        return
    import fcntl
    # A read-only fd also works when a previous root inspection created the file.
    with os.fdopen(os.open(LOCK_PATH, os.O_RDONLY | os.O_CREAT, 0o644), 'r') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Compiled-model inspection is using the camera. Wait for it to finish.')
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
