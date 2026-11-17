"""Local two-class annotations and immutable, session-separated YOLO exports."""
import hashlib
from contextlib import contextmanager
import io
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import uuid

from PIL import Image, ImageOps
from pi_vision.shared.spatial_detection import search_regions

CLASSES = ['plane', 'target']


def validate_boxes(boxes):
    if not isinstance(boxes, list) or len(boxes) > 100:
        raise ValueError('Expected up to 100 boxes.')
    result = []
    for box in boxes:
        if not isinstance(box, dict) or box.get('class_id') not in (0, 1) or isinstance(box.get('class_id'), bool):
            raise ValueError('Choose plane (0) or target (1).')
        values = [box.get(k) for k in ('x', 'y', 'w', 'h')]
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
            raise ValueError('Box coordinates must be finite numbers.')
        x, y, w, h = values
        if not (0 < w <= 1 and 0 < h <= 1 and w / 2 <= x <= 1 - w / 2 + 1e-9
                and h / 2 <= y <= 1 - h / 2 + 1e-9):
            raise ValueError('Box must fit inside the image, using normalized YOLO coordinates.')
        result.append(dict(class_id=int(box['class_id']), x=x, y=y, w=w, h=h))
    return result


class Dataset:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.images = self.root / 'images'
        self.images.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY, digest TEXT UNIQUE, width INTEGER, height INTEGER,
                session TEXT, split TEXT, reviewed INTEGER DEFAULT 0, boxes TEXT DEFAULT '[]')''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(images)')}
            if 'created_utc' not in columns:
                db.execute('ALTER TABLE images ADD COLUMN created_utc TEXT')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'annotations.sqlite3')
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, content, session, split):
        if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session):
            raise ValueError('Session must be 1–64 letters, digits, underscores or hyphens.')
        if split not in ('train', 'val', 'test'):
            raise ValueError('Choose train, val or test.')
        with Image.open(io.BytesIO(content)) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError('Image exceeds 40 megapixels.')
            image = ImageOps.exif_transpose(original).convert('RGB')
            width, height = image.size
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=95)
        content = output.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        with self.lock, self.connect() as db:
            existing = db.execute('SELECT id FROM images WHERE digest=?', (digest,)).fetchone()
            if existing:
                return existing['id'], False
            other = db.execute('SELECT split FROM images WHERE session=? LIMIT 1', (session,)).fetchone()
            if other and other['split'] != split:
                raise ValueError('A capture session must stay in one split. Use a different recording for validation/test.')
            ident = uuid.uuid4().hex
            path = self.images / (ident + '.jpg')
            path.write_bytes(content)
            try:
                db.execute("INSERT INTO images(id,digest,width,height,session,split,created_utc) VALUES(?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                           (ident, digest, width, height, session, split))
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return ident, True

    def list(self):
        with self.lock, self.connect() as db:
            return [dict(row, boxes=json.loads(row['boxes']), reviewed=bool(row['reviewed']))
                    for row in db.execute('SELECT * FROM images ORDER BY rowid')]

    def save(self, ident, boxes):
        boxes = validate_boxes(boxes)
        with self.lock, self.connect() as db:
            if db.execute('UPDATE images SET reviewed=1, boxes=? WHERE id=?', (json.dumps(boxes), ident)).rowcount != 1:
                raise ValueError('Image not found.')

    def remove(self, ident):
        with self.lock, self.connect() as db:
            if db.execute('SELECT 1 FROM images WHERE id=?', (ident,)).fetchone() is None:
                raise ValueError('Image not found.')
            (self.images / (ident + '.jpg')).unlink(missing_ok=True)
            db.execute('DELETE FROM images WHERE id=?', (ident,))

    def remove_many(self, ids):
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 10000
                or any(not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{32}', ident) for ident in ids)):
            raise ValueError('Select 1–10000 valid image IDs.')
        ids = list(dict.fromkeys(ids))
        with self.lock:
            existing = {row['id'] for row in self.list()}
            if any(ident not in existing for ident in ids):
                raise ValueError('Image not found. Refresh the library and try again.')
            for ident in ids:
                self.remove(ident)
        return len(ids)

    def export(self, profile='full', crop_size=640):
        if profile not in ('full', 'adaptive'):
            raise ValueError('Choose full or adaptive dataset preparation.')
        if type(crop_size) is not int or not 320 <= crop_size <= 1920 or crop_size % 32:
            raise ValueError('Crop size must be 320-1920 and divisible by 32.')
        with self.lock:
            rows = [row for row in self.list() if row['reviewed']]
            for split in ('train', 'val'):
                subset = [r for r in rows if r['split'] == split]
                if not subset:
                    raise ValueError('Label images in separate train and validation sessions first.')
                for cls in range(len(CLASSES)):
                    if not any(b['class_id'] == cls for r in subset for b in r['boxes']):
                        raise ValueError(f'Add at least one labeled {CLASSES[cls]} in {split}.')
            folder = self.root / 'exports' / uuid.uuid4().hex
            for split in ('train', 'val', 'test'):
                (folder / 'images' / split).mkdir(parents=True)
                (folder / 'labels' / split).mkdir(parents=True)
            for row in rows:
                split, ident = row['split'], row['id']
                shutil.copyfile(self.images / (ident + '.jpg'), folder / 'images' / split / (ident + '.jpg'))
                lines = [f"{b['class_id']} {b['x']:.8f} {b['y']:.8f} {b['w']:.8f} {b['h']:.8f}" for b in row['boxes']]
                (folder / 'labels' / split / (ident + '.txt')).write_text('\n'.join(lines), encoding='utf-8')
                if profile == 'adaptive':
                    with Image.open(self.images / (ident + '.jpg')) as source:
                        for number, (region, boxes) in enumerate(native_crops(row, crop_size)):
                            x, y, w, h = region
                            canvas = Image.new('RGB', (crop_size, crop_size), (114, 114, 114))
                            canvas.paste(source.crop((x, y, x+w, y+h)), (0, 0))
                            stem = f'{ident}-crop-{number}'
                            canvas.save(folder / 'images' / split / (stem + '.jpg'), quality=95)
                            lines = [f"{b['class_id']} {b['x']:.8f} {b['y']:.8f} {b['w']:.8f} {b['h']:.8f}" for b in boxes]
                            (folder / 'labels' / split / (stem + '.txt')).write_text('\n'.join(lines), encoding='utf-8')
            config = {'path': folder.as_posix(), 'train': 'images/train', 'val': 'images/val',
                      'names': CLASSES}
            if any(r['split'] == 'test' for r in rows):
                config['test'] = 'images/test'
            # JSON is valid YAML, including Windows paths and class names.
            (folder / 'data.yaml').write_text(json.dumps(config, indent=2), encoding='utf-8')
            (folder / 'manifest.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
            (folder / 'preparation.json').write_text(json.dumps(dict(profile=profile, crop_size=crop_size,
                full_frames=True, crop_coordinates='native pixels; top-left padding for undersized images',
                split_policy='All derivatives retain the original capture-session split.'), indent=2), encoding='utf-8')
            return folder


def native_crops(row, size):
    """Cover the view and add off-centre object crops without dropping labels.

    Reject crops containing tiny cut-off annotation fragments rather than
    turning known objects into unlabelled background. No resizing occurs.
    """
    width, height = row['width'], row['height']
    regions = search_regions(width, height, size)
    for b in row['boxes']:
        for offset in ((.5, .5), (.3, .65)):
            w, h = min(size, width), min(size, height)
            x = max(0, min(width-w, round(b['x']*width - w*offset[0])))
            y = max(0, min(height-h, round(b['y']*height - h*offset[1])))
            regions.append((x, y, w, h))
    for region in dict.fromkeys(regions):
        x, y, w, h = region
        boxes, usable = [], True
        for b in row['boxes']:
            left, top = (b['x']-b['w']/2)*width, (b['y']-b['h']/2)*height
            right, bottom = left+b['w']*width, top+b['h']*height
            x1, y1, x2, y2 = max(x, left), max(y, top), min(x+w, right), min(y+h, bottom)
            if x2 <= x1 or y2 <= y1:
                continue
            if min(x2-x1, y2-y1) < 2 or (x2-x1)*(y2-y1) < .25*(right-left)*(bottom-top):
                usable = False
                break
            boxes.append(dict(class_id=b['class_id'], x=((x1+x2)/2-x)/size,
                              y=((y1+y2)/2-y)/size, w=(x2-x1)/size, h=(y2-y1)/size))
        if usable:
            yield region, boxes
