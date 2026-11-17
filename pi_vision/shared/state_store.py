"""Durable replacement of small JSON settings files.

Callers validate data and hold their own settings lock before writing.
"""
import json
import os
from pathlib import Path


def save_json(path, data, *, indent=None, newline=False):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(data, stream, indent=indent)
        if newline:
            stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
