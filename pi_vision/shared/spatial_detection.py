"""Native-pixel search and tracking geometry, independent of camera/NPU APIs."""
import math


def tile_starts(length, window, overlap=.25):
    if length <= window:
        return [0]
    count = math.ceil((length - window) / (window * (1 - overlap)))
    return [round(i * (length - window) / count) for i in range(count + 1)]


def search_regions(width, height, window):
    regions = [(x, y, min(width, window), min(height, window))
               for y in tile_starts(height, window) for x in tile_starts(width, window)]
    # Start in the middle of the launch view; every tile is still visited.
    return sorted(regions, key=lambda r: (r[0] + r[2]/2 - width/2)**2 +
                                         (r[1] + r[3]/2 - height/2)**2)


def translate_detections(detections, region):
    x, y, _, _ = region
    return [{**d, 'box': [d['box'][0] + x, d['box'][1] + y,
                         d['box'][2] + x, d['box'][3] + y]} for d in detections]


def merge_detections(detections, threshold=.5):
    """Class-wise NMS for exhaustive offline tiled inspection."""
    kept = []
    for item in sorted(detections, key=lambda d:d['confidence'], reverse=True):
        a = item['box']
        duplicate = False
        for other in kept:
            if item['label'] != other['label']:
                continue
            b = other['box']
            area = max(0,min(a[2],b[2])-max(a[0],b[0])) * max(0,min(a[3],b[3])-max(a[1],b[1]))
            union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area
            if union > 0 and area/union >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
    return kept


class SpatialDetector:
    """Scan on fresh frames, then follow a unique plane with an unscaled crop.

    Require three associated observations before emitting a confirmed plane.
    A miss immediately produces no plane observation. Resume search after a
    candidate miss, two tracking misses, an excessive gap, or a one-second lock.
    No predicted boxes are fed to flight control. A crop cannot rule out a
    second plane elsewhere in view. Use only with crop-trained/validated weights.
    """
    def __init__(self, window=640, label='plane', max_gap=.25, confirm_frames=3,
                 confidence=.6, max_lock_seconds=1):
        self.window, self.label, self.max_gap = window, label, max_gap
        self.confirm_frames, self.confidence = confirm_frames, confidence
        self.max_lock_seconds = max_lock_seconds
        self.reset()

    def reset(self):
        self.geometry = None
        self.center = None
        self.last_seen = None
        self.misses = 0
        self.index = 0
        self.confirmed = 0
        self.lock_started = None

    def region(self, width, height, now, revision=0):
        geometry = (width, height, revision)
        if geometry != self.geometry:
            self.reset()
            self.geometry = geometry
        if self.last_seen is not None and now - self.last_seen > self.max_gap:
            self.center = None
            self.confirmed = 0
            self.lock_started = None
        if self.lock_started is not None and now - self.lock_started >= self.max_lock_seconds:
            # Bound time spent on even a persistent background false detection.
            # Resume the next search tile instead of letting one ROI monopolise inference.
            self.center = None
            self.confirmed = 0
            self.lock_started = None
        if self.center is not None:
            w, h = min(width, self.window), min(height, self.window)
            x = max(0, min(width - w, round(self.center[0] - w/2)))
            y = max(0, min(height - h, round(self.center[1] - h/2)))
            return (x, y, w, h), 'tracking' if self.confirmed >= self.confirm_frames else 'confirming'
        regions = search_regions(width, height, self.window)
        region = regions[self.index % len(regions)]
        self.index += 1
        return region, 'searching'

    def observe(self, detections, now):
        planes = [d for d in detections if d['label'] == self.label and d['confidence'] >= self.confidence]
        if len(planes) == 1:
            x1, y1, x2, y2 = planes[0]['box']
            point = ((x1 + x2)/2, (y1 + y2)/2)
            if self.center is not None and math.dist(point, self.center) > self.window/4:
                self.center, self.confirmed, self.lock_started = None, 0, None
                return False
            # A box truncated at the crop edge can be recentred next frame.
            self.center = point
            self.last_seen, self.misses = now, 0
            self.confirmed += 1
            if self.confirmed >= self.confirm_frames and self.lock_started is None:
                self.lock_started = now
            return self.confirmed >= self.confirm_frames
        else:
            self.misses += 1
            self.confirmed = 0
            if self.misses >= 2 or len(planes) > 1 or self.lock_started is None:
                self.center = None
                self.lock_started = None
            return False
