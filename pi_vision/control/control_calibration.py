"""Shared voltage mapping for manual and autonomous flight controllers.

Controllers must load the Pi's remote-calibration.json and call outputs().
Throttle/roll commands are percentages of calibrated travel, not raw voltage.
"""
import json
import math
from pathlib import Path

from pi_vision.shared.state_store import save_json

POINTS = ('throttle_idle', 'throttle_full', 'roll_center', 'roll_left', 'roll_right')


def voltage(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 3.3:
        raise ValueError('Voltage must be a finite number from 0 to 3.3 V.')
    return value


class Calibration:
    def __init__(self, points=None):
        self.points = dict(points or {})
        if self.points.keys() - set(POINTS):
            raise ValueError('Unknown calibration point.')
        for value in self.points.values():
            voltage(value)
        p = self.points
        if all(k in p for k in ('throttle_idle', 'throttle_full')):
            if abs(p['throttle_idle'] - p['throttle_full']) < .01:
                raise ValueError('Throttle idle and full must differ by at least 0.01 V.')
        if all(k in p for k in ('roll_left', 'roll_center', 'roll_right')):
            if not min(p['roll_left'], p['roll_right']) < p['roll_center'] < max(p['roll_left'], p['roll_right']):
                raise ValueError('Roll center must lie between left and right. Inverted wiring is supported.')

    @property
    def complete(self):
        return all(k in self.points for k in POINTS)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        if 'roll_center_percent' in data:  # Preserve the previous roll-only setup.
            return cls({'roll_center': voltage(data['roll_center_percent'] * .033)})
        if data.get('version') != 2:
            raise ValueError('Unsupported calibration version.')
        return cls(data['volts'])

    def with_point(self, name, value):
        if name not in POINTS:
            raise ValueError('Unknown calibration point.')
        # Save idle before full, so finishing a throttle test can restore idle.
        if name != 'throttle_idle' and 'throttle_idle' not in self.points:
            raise ValueError('Save throttle idle first.')
        return Calibration({**self.points, name: voltage(value)})

    def save(self, path):
        save_json(path, {'version': 2, 'volts': self.points}, indent=2, newline=True)

    def throttle_percent(self, volts):
        idle = self.points.get('throttle_idle')
        full = self.points.get('throttle_full')
        if volts is None or idle is None or full is None:
            return None
        percent = 100 * (volts - idle) / (full - idle)
        return max(0, min(100, percent)) if -1e-9 <= percent <= 100 + 1e-9 else None

    def outputs(self, throttle, roll, *, initialization=False, throttle_limit=50, roll_limit=50):
        if not self.complete:
            raise ValueError('Save all five calibration points before normal control.')
        for value in (throttle, roll):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Commands must be finite numbers.')
        for limit in (throttle_limit, roll_limit):
            if type(limit) not in (int, float) or not math.isfinite(limit) or not 1 <= limit <= 100:
                raise ValueError('Limits must be from 1 to 100%.')
        if not 0 <= throttle <= (100 if initialization else throttle_limit) or not -roll_limit <= roll <= roll_limit:
            raise ValueError('Command exceeds the configured throttle or roll limit.')
        p = self.points
        t = p['throttle_idle'] + throttle / 100 * (p['throttle_full'] - p['throttle_idle'])
        endpoint = p['roll_right'] if roll >= 0 else p['roll_left']
        r = p['roll_center'] + abs(roll) / 100 * (endpoint - p['roll_center'])
        return voltage(t), voltage(r)
