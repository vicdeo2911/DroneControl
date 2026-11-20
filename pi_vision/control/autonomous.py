"""Bounded image-space PD steering and a supervised hand-launch state machine."""
import json
from collections import deque
import math
import secrets
import time
import urllib.request

from pi_vision.control.coupled_control import (
    TrackingCommand, TrackingSettings, VerticalSettings, VerticalTrackingController, tracking_step,
)
from pi_vision.shared.state_store import save_json
from pi_vision.shared.camera_operation import operation_guard


# value, minimum, maximum; percentages are absolute calibrated control percentages.
PARAMETERS = {
    'tracking_method': (0, 0, 2),
    'turn_adjustment': (10, -100, 100),
    'vertical_roll_reduction': (0, 0, 1),
    'feedback_hz': (10, 2, 60),
    'launch_delay': (5, 1, 30), 'launch_seconds': (2, .1, 10),
    'acquire_timeout': (5, .2, 15), 'max_flight_seconds': (30, 1, 300),
    'launch_throttle': (30, 0, 100), 'cruise_throttle': (25, 0, 100),
    'lost_throttle': (0, 0, 100), 'max_roll': (15, 0, 100),
    'kp': (35, 0, 200), 'kd': (8, 0, 100), 'roll_direction': (1, -1, 1),
    'setpoint_x': (.5, .05, .95), 'deadband': (.03, 0, .2),
    'filter_alpha': (.4, .05, 1), 'confidence': (.6, .05, 1),
    'association_gate': (.25, .01, 1), 'confirm_frames': (3, 1, 30),
    'loss_timeout': (.7, .1, 3), 'frame_timeout': (.5, .1, 1),
    'throttle_slew': (20, 1, 100), 'roll_slew': (40, 1, 200),
}


class Autonomous:
    def __init__(self, remote, path=None, clock=time.monotonic):
        self.remote, self.path, self.clock = remote, path, clock
        self.config = {k: v[0] for k, v in PARAMETERS.items()}
        if path and path.exists():
            self.config = self.validate(json.loads(path.read_text()))
        self.active = False
        self.state, self.reason = 'stopped', 'Ready for a dry run.'
        self.owner = self.token = None
        self.command = dict(throttle=0, roll=0)
        self.dry_run = True
        self.source = 'web'
        self.loop_samples = deque(maxlen=240)
        self.observation_samples = deque(maxlen=240)
        self.frame_age = None

    def validate(self, changes):
        if not isinstance(changes, dict) or changes.keys() - PARAMETERS.keys():
            raise ValueError('Unknown autonomous parameter.')
        result = {**self.config, **changes}
        for key, (_, low, high) in PARAMETERS.items():
            value = result[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{key} must be from {low} to {high}.')
        for key in ('tracking_method', 'vertical_roll_reduction'):
            if type(result[key]) is not int:
                raise ValueError(f'{key} must be an integer.')
        if result['roll_direction'] not in (-1, 1) or type(result['confirm_frames']) is not int:
            raise ValueError('Roll direction must be -1 or 1; confirmation frames must be an integer.')
        return result

    def save(self, changes):
        with self.remote.lock:
            if self.active or self.remote.token:
                raise ValueError('Stop controls before changing autonomous parameters.')
            config = self.validate(changes)
            if self.path:
                save_json(self.path, config, indent=2)
            self.config = config
            return self.status()

    def status(self):
        with self.remote.lock:
            return dict(active=self.active, state=self.state, reason=self.reason,
                        dry_run=self.dry_run, source=self.source, command=dict(self.command), config=dict(self.config),
                        parameters={k: dict(min=v[1], max=v[2]) for k, v in PARAMETERS.items()},
                        feedback=dict(loop_hz=self.measured_rate(self.loop_samples),
                                      observation_hz=self.measured_rate(self.observation_samples),
                                      frame_age_ms=round(self.frame_age * 1000, 1) if self.frame_age is not None else None))

    def measured_rate(self, samples):
        recent = [stamp for stamp in samples if self.clock() - stamp <= 2]
        return round((len(recent) - 1) / (recent[-1] - recent[0]), 1) if len(recent) > 1 and recent[-1] > recent[0] else 0

    def start_from_camera(self, mode, dry_run=True, restore_outputs=False, source='web'):
        """Shared preflight for browser development controls and GPIO launch."""
        with operation_guard():
            return self._start_from_camera(mode, dry_run, restore_outputs, source)

    def _start_from_camera(self, mode, dry_run, restore_outputs, source):
        try:
            camera = camera_status()
        except Exception as exc:
            raise ValueError('Camera unavailable. Start the camera service before autonomous control.') from exc
        if (not camera.get('ready') or camera.get('error')
                or 'plane' not in camera.get('labels', [])
                or type(camera.get('frame_age_seconds')) not in (int, float)
                or not 0 <= camera['frame_age_seconds'] <= self.config['frame_timeout']
                or not camera.get('frame_size')):
            raise ValueError('Autonomous control needs a fresh camera and a model with the plane label. Deploy the trained model and updated camera app first.')
        return self.start(mode, dry_run, restore_outputs, source)

    def start(self, mode, dry_run=True, restore_outputs=False, source='web'):
        with self.remote.lock:
            if self.active or self.remote.token:
                raise ValueError('Stop the current controller first.')
            if mode not in ('behind', 'visible') or type(dry_run) is not bool:
                raise ValueError('Choose behind or visible and a boolean dry_run.')
            if source not in ('web', 'gpio'):
                raise ValueError('Invalid autonomous trigger source.')
            self.source = source
            c = self.config
            if not dry_run:
                if max(c['launch_throttle'], c['cruise_throttle'], c['lost_throttle']) > self.remote.limits['throttle'] or c['max_roll'] > self.remote.limits['roll']:
                    raise ValueError('Autonomous commands exceed the remote limits. Adjust parameters first.')
                result = self.remote.enable(source='autonomous', restore_outputs=restore_outputs)
                self.token = result['token']
                try:
                    self.remote.initialize(self.token)
                except Exception:
                    self.remote.stop('Autonomous arming failed.')
                    self.token = None
                    raise
            else:
                self.token = None
            self.dry_run, self.mode, self.active = dry_run, mode, True
            self.owner = secrets.token_urlsafe(24) if source == 'web' else None
            self.started = self.last_heartbeat = self.last_tick = self.phase_started = self.clock()
            self.sequence, self.last_frame = -1, None
            self.loop_samples.clear()
            self.observation_samples.clear()
            self.frame_age = None
            self.last_new_frame = self.started
            self.last_detection = self.filtered_x = self.previous_center = None
            self.vertical = None
            self.vertical_sample_time = None
            if c['tracking_method']:
                self.tracking_settings = TrackingSettings(
                    cruise_throttle=c['cruise_throttle'], throttle_limit=self.remote.limits['throttle'],
                    turn_adjustment=c['turn_adjustment'], setpoint_x=c['setpoint_x'],
                    deadband=c['deadband'], kp=c['kp'], kd=c['kd'],
                    roll_direction=c['roll_direction'], max_roll=c['max_roll'],
                    roll_limit=self.remote.limits['roll'], throttle_slew=c['throttle_slew'],
                    roll_slew=c['roll_slew'],
                )
                if c['tracking_method'] == 2:
                    self.vertical = VerticalTrackingController(
                        self.tracking_settings,
                        VerticalSettings(roll_reduction_enabled=bool(c['vertical_roll_reduction'])),
                    )
            self.frame_geometry = None
            self.confirmed = 0
            self.command = dict(throttle=0, roll=0)
            self.state, self.reason = 'arming', 'Waiting for arming sweep.'
            return {**self.status(), 'owner': self.owner}

    def heartbeat(self, owner):
        with self.remote.lock:
            if self.source != 'web' or not self.active or not isinstance(owner, str) or not secrets.compare_digest(owner, self.owner):
                raise ValueError('Autonomous session stopped or owned by another page.')
            self.last_heartbeat = self.clock()
            return self.status()

    def cancel(self, reason):
        # Called by Remote.stop/off as well, so stopping any control page cancels autonomy.
        self.active = False
        self.state, self.reason = 'stopped', reason
        self.command = dict(throttle=0, roll=0)
        self.owner = None

    def stop(self, reason):
        self.cancel(reason)
        if self.token and self.remote.token == self.token:
            self.remote.stop(reason)
        self.token = None

    def transition(self, state, reason, now):
        self.state, self.reason, self.phase_started = state, reason, now

    def observe(self, info, now):
        if info['frames'] == self.last_frame:
            return False
        self.last_frame, self.last_new_frame = info['frames'], now
        self.observation_samples.append(now)
        width, height = info['frame_size']
        candidates = []
        for d in info['detections']:
            if d.get('label') != 'plane' or d.get('confidence', 0) < self.config['confidence']:
                continue
            x1, y1, x2, y2 = d['box']
            if not all(type(v) in (int, float) and math.isfinite(v) for v in (x1, y1, x2, y2)) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                continue
            candidates.append(((x1 + x2) / (2 * width), (y1 + y2) / (2 * height)))
        # Ambiguity is treated as missing; never silently switch between planes.
        point = candidates[0] if len(candidates) == 1 else None
        if point and self.previous_center and math.dist(point, self.previous_center) > self.config['association_gate']:
            point = None
        if point is None:
            self.confirmed = 0
            return False
        self.previous_center = point
        self.velocity = 0
        if self.filtered_x is None:
            self.filtered_x = point[0]
        else:
            filtered = self.filtered_x + self.config['filter_alpha'] * (point[0] - self.filtered_x)
            self.velocity = (filtered - self.filtered_x) / max(now - self.last_detection, .001)
            self.filtered_x = filtered
        self.last_detection = now
        self.confirmed += 1
        return self.confirmed >= self.config['confirm_frames']

    def tick(self, info):
        with self.remote.lock:
            if not self.active:
                return
            now, c = self.clock(), self.config
            self.loop_samples.append(now)
            dt = max(0, min(now - self.last_tick, .2))
            self.last_tick = now
            if self.source == 'web' and now - self.last_heartbeat > self.remote.TIMEOUT:
                return self.stop('Autonomous page connection lost.')
            if not self.dry_run and self.remote.token != self.token:
                return self.stop('Remote control session ended.')
            if now - self.started > c['max_flight_seconds']:
                return self.stop('Maximum autonomous session duration reached.')
            if not isinstance(info, dict) or not info.get('ready') or info.get('error'):
                return self.stop('Camera unavailable. Idle and center requested.')
            age = info.get('frame_age_seconds')
            size = info.get('frame_size')
            if (type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age <= c['frame_timeout']
                    or not isinstance(size, list) or len(size) != 2
                    or any(type(v) is not int or v <= 0 for v in size)
                    or type(info.get('frames')) is not int or not isinstance(info.get('detections'), list)):
                return self.stop('Camera frame stale or invalid.')
            geometry = (tuple(size), tuple(info.get('sensor_mode', {}).get('size', [])),
                        info.get('settings', {}).get('zoom', 1))
            if self.frame_geometry is not None and geometry != self.frame_geometry:
                return self.stop('Camera capture geometry changed. Restart autonomous control.')
            self.frame_geometry = geometry
            fresh = self.observe(info, now)
            self.frame_age = age
            if now - self.last_new_frame > c['frame_timeout']:
                return self.stop('Camera frames stopped advancing.')
            if self.state == 'arming':
                if not self.dry_run and self.remote.initializing:
                    self.sequence += 1
                    self.remote.update(self.token, self.sequence, 0, 0)
                    return
                self.transition('waiting_view' if self.mode == 'visible' else 'countdown',
                                'Waiting for a confirmed plane.' if self.mode == 'visible' else 'Countdown before launch throttle ramp.', now)
            if self.state == 'waiting_view':
                if fresh:
                    self.transition('countdown', 'Plane acquired. Countdown before launch throttle ramp.', now)
                elif now - self.phase_started > c['acquire_timeout']:
                    return self.stop('No confirmed plane in starting view.')
            if self.state == 'countdown':
                if self.mode == 'visible' and (self.last_detection is None or now - self.last_detection > c['loss_timeout']):
                    return self.stop('Plane lost before launch.')
                self.reason = f'Throttle ramp in {max(0, c["launch_delay"] - (now - self.phase_started)):.1f}s; throttle remains idle.'
                if now - self.phase_started >= c['launch_delay']:
                    self.transition('spooling', 'Ramping launch throttle. Hold for the Launch now cue.', now)
            tracking_command = None
            throttle, roll = 0, 0
            if self.state == 'spooling':
                if self.mode == 'visible' and (self.last_detection is None or now - self.last_detection > c['loss_timeout']):
                    return self.stop('Plane lost before launch.')
                throttle = c['launch_throttle']
                if abs(self.command['throttle'] - throttle) < .001:
                    self.transition('launch', 'Launch now. Timed launch throttle; roll centered.', now)
            if self.state == 'launch':
                throttle = c['launch_throttle']
                if now - self.phase_started >= c['launch_seconds']:
                    self.transition('acquiring', 'Waiting for plane after launch.', now)
            if self.state in ('acquiring', 'tracking', 'lost'):
                seen = self.last_detection is not None and now - self.last_detection <= c['loss_timeout']
                if self.state == 'lost' and now - self.phase_started >= c['loss_timeout']:
                    return self.stop('Plane detection timeout. Idle and center requested; restart required.')
                if fresh and self.state != 'tracking':
                    self.transition('tracking', 'Tracking plane with image-space PD steering.', now)
                if self.state == 'acquiring':
                    throttle = c['launch_throttle']
                    if now - self.phase_started > c['acquire_timeout']:
                        return self.stop('Plane never acquired after launch.')
                elif self.state == 'tracking':
                    if self.confirmed == 0 or not seen:
                        self.transition('lost', 'Detection lost. Roll centered; fallback throttle.', now)
                    elif c['tracking_method']:
                        tracking_command = self.coupled_tracking_command(now, dt, fresh)
                    else:
                        throttle = c['cruise_throttle']
                        error = c['setpoint_x'] - self.filtered_x
                        error = math.copysign(max(0, abs(error) - c['deadband']), error)
                        roll = c['roll_direction'] * (c['kp'] * error - c['kd'] * self.velocity)
                        roll = max(-c['max_roll'], min(c['max_roll'], roll))
                if self.state == 'lost':
                    if self.vertical is not None:
                        self.vertical.reset()
                        self.vertical_sample_time = None
                    throttle, roll = c['lost_throttle'], 0
                    if not seen:
                        return self.stop('Plane detection timeout. Idle and center requested; restart required.')
            if tracking_command is not None:
                self.command.update(throttle=tracking_command.throttle, roll=tracking_command.roll)
            else:
                for key, desired in (('throttle', throttle), ('roll', roll)):
                    # Loss mitigation acts immediately; regular commands have bounded slew.
                    old = self.command[key]
                    step = c[key + '_slew'] * dt
                    self.command[key] = desired if self.state == 'lost' else old + max(-step, min(step, desired - old))
            if not self.dry_run:
                self.sequence += 1
                self.remote.reason = f'Autonomous {self.state}: {self.reason}'
                self.remote.update(self.token, self.sequence, **self.command)

    def coupled_tracking_command(self, now, dt, fresh):
        previous = TrackingCommand(**self.command)
        if self.vertical is None:
            return tracking_step(self.filtered_x, self.velocity, dt,
                                 previous, self.tracking_settings).command

        if (self.vertical_sample_time is not None
                and now - self.vertical_sample_time > self.vertical.vertical.max_sample_gap):
            self.vertical.reset()
            self.vertical_sample_time = None
        if fresh:
            y = self.previous_center[1]
            if self.vertical_sample_time is None:
                self.vertical.capture_reference(y)
            else:
                self.vertical.observe_vertical(y, now - self.vertical_sample_time)
            self.vertical_sample_time = now
        return self.vertical.command(self.filtered_x, self.velocity, dt, previous).command


def camera_status():
    # Fixed loopback source; clients cannot redirect the controller to another host.
    with urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=.25) as response:
        return json.loads(response.read(262144))
