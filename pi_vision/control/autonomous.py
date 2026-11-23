"""Bounded image-space PD steering and a supervised autonomous launch state machine."""
import json
from collections import deque
from dataclasses import dataclass
import math
import secrets
import time
import urllib.request

from pi_vision.shared.state_store import save_json
from pi_vision.shared.camera_operation import operation_guard
from pi_vision.control.launcher_calibration import prepare_launch, release_launcher

PREDICTION_SECONDS = .25
PREDICTION_MAX_SHIFT = .06
MOTION_RADIUS = .013
MOTION_MAX_AGE = .25
PLANE_MOTION_DELTA = .008
PLANE_MOTION_PROGRESS = .002
POST_LAUNCH_STATES = ('launch', 'acquiring', 'tracking', 'lost')

# value, minimum, maximum; percentages are absolute calibrated control percentages.
PARAMETERS = {
    'feedback_hz': (10, 2, 60),
    'launch_delay': (5, 1, 30), 'launcher_release_delay': (1, 0, 10),
    'launch_seconds': (2, .1, 10),
    'post_release_throttle_hold': (.5, 0, 10),
    'acquire_timeout': (5, .2, 15), 'max_flight_seconds': (30, 1, 300),
    'launch_throttle': (30, 0, 100), 'cruise_throttle': (25, 0, 100),
    'lost_throttle': (0, 0, 100), 'max_roll': (15, 0, 100),
    'kp': (35, 0, 200), 'kd': (8, 0, 100), 'roll_direction': (1, -1, 1),
    'setpoint_x': (.5, .05, .95), 'deadband': (.03, 0, .2),
    'setpoint_y': (.5, .05, .95), 'vertical_deadband': (.03, 0, .2),
    'vertical_kp': (0, 0, 200), 'vertical_kd': (0, 0, 100),
    'vertical_direction': (1, -1, 1), 'vertical_filter_alpha': (.2, .05, 1),
    'max_vertical_throttle': (10, 0, 50), 'turn_throttle_boost': (0, 0, 50),
    'filter_alpha': (.4, .05, 1), 'confidence': (.6, .05, 1),
    'association_gate': (.25, .01, 1), 'confirm_frames': (3, 1, 30),
    'loss_timeout': (.7, .1, 3), 'frame_timeout': (.5, .1, 1),
    'stationary_timeout': (.6, 0, 3),
    'throttle_slew': (20, 1, 100), 'roll_slew': (40, 1, 200),
}


def valid_box(box, width, height):
    if (not isinstance(box, (tuple, list)) or len(box) != 4
            or not all(type(v) in (int, float) and math.isfinite(v) for v in box)):
        return False
    x1, y1, x2, y2 = box
    return 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height


@dataclass
class PlaneMotion:
    center: tuple
    anchor_center: tuple
    last_seen: float
    last_motion: float | None = None
    motion_point: tuple | None = None

    def observe(self, center, now):
        self.center, self.last_seen = center, now
        if math.dist(center, self.anchor_center) < PLANE_MOTION_DELTA:
            self.motion_point = None
            return
        if self.motion_point is None:
            self.motion_point = center
            return
        dx = self.motion_point[0] - self.anchor_center[0]
        dy = self.motion_point[1] - self.anchor_center[1]
        distance = math.hypot(dx, dy)
        progress = ((center[0] - self.motion_point[0]) * dx
                    + (center[1] - self.motion_point[1]) * dy) / distance
        if progress >= PLANE_MOTION_PROGRESS:
            self.anchor_center, self.last_motion = center, now
            self.motion_point = None
        elif progress < -distance:
            self.motion_point = center


class Autonomous:
    def __init__(self, remote, path=None, clock=time.monotonic):
        self.remote, self.path, self.clock = remote, path, clock
        self.config = {k: v[0] for k, v in PARAMETERS.items()}
        if path and path.exists():
            saved = json.loads(path.read_text())
            if isinstance(saved, dict):
                saved.pop('ignored_plane', None)  # Older settings stored a manually selected static box.
            self.config = self.validate(saved)
        self.active = False
        self.state, self.reason = 'stopped', 'Reload launcher, then start autonomous flight.'
        self.owner = self.token = None
        self.command = dict(throttle=0, roll=0)
        self.post_release_hold_until = None
        self.throttle_feedback = None
        self.dry_run = True
        self.test_target_x = None
        self.predict_gaps = False
        self.prediction_anchor = None
        self.predicted_x = None
        self.motion_anchor = self.last_motion = None
        self.stationary_hint = False
        self.noise_frames = deque(maxlen=32)
        self.source = 'web'
        self.loop_samples = deque(maxlen=240)
        self.observation_samples = deque(maxlen=240)
        self.frame_age = None
        self.plane_detections = 0

    def validate(self, changes):
        if not isinstance(changes, dict) or changes.keys() - PARAMETERS.keys():
            raise ValueError('Unknown autonomous parameter.')
        result = {**self.config, **changes}
        for key, (_, low, high) in PARAMETERS.items():
            value = result[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{key} must be from {low} to {high}.')
        if result['roll_direction'] not in (-1, 1) or type(result['confirm_frames']) is not int:
            raise ValueError('Roll direction must be -1 or 1; confirmation frames must be an integer.')
        if result['vertical_direction'] not in (-1, 1):
            raise ValueError('Vertical throttle direction must be -1 or 1.')
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
            throttle_volts = self.remote.throttle
            throttle_percent = (None if self.remote.outputs_zero
                                else self.remote.calibration.throttle_percent(throttle_volts))
            return dict(active=self.active, state=self.state, reason=self.reason,
                        settings_locked=self.active or self.remote.token is not None,
                        dry_run=self.dry_run, source=self.source, command=dict(self.command), config=dict(self.config),
                        throttle_output=dict(percent=throttle_percent, volts=throttle_volts,
                                             zero_mode=self.remote.outputs_zero),
                        test_target_x=self.test_target_x,
                        predicted_x=self.predicted_x,
                        stationary_hint=self.stationary_hint, plane_detections=self.plane_detections,
                        throttle_feedback=self.throttle_feedback,
                        parameters={k: dict(default=v[0], min=v[1], max=v[2]) for k, v in PARAMETERS.items()},
                        feedback=dict(loop_hz=self.measured_rate(self.loop_samples),
                                      observation_hz=self.measured_rate(self.observation_samples),
                                      frame_age_ms=round(self.frame_age * 1000, 1) if self.frame_age is not None else None))

    def measured_rate(self, samples):
        recent = [stamp for stamp in samples if self.clock() - stamp <= 2]
        return round((len(recent) - 1) / (recent[-1] - recent[0]), 1) if len(recent) > 1 and recent[-1] > recent[0] else 0

    def start_from_camera(self, mode, dry_run=True, restore_outputs=False, source='web',
                          *, test_target_x=None, predict_gaps=False):
        """Shared preflight for browser development controls and GPIO launch."""
        with operation_guard():
            return self._start_from_camera(mode, dry_run, restore_outputs, source, test_target_x, predict_gaps)

    def _start_from_camera(self, mode, dry_run, restore_outputs, source, test_target_x, predict_gaps):
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
        return self.start(mode, dry_run, restore_outputs, source,
                          test_target_x=test_target_x, predict_gaps=predict_gaps)

    def start(self, mode, dry_run=True, restore_outputs=False, source='web', *, test_target_x=None, predict_gaps=False):
        with self.remote.lock:
            if self.active or self.remote.token:
                raise ValueError('Stop the current controller first.')
            if mode not in ('behind', 'visible') or type(dry_run) is not bool:
                raise ValueError('Choose behind or visible and a boolean dry_run.')
            if source not in ('web', 'gpio'):
                raise ValueError('Invalid autonomous trigger source.')
            if type(predict_gaps) is not bool or (predict_gaps and source != 'web'):
                raise ValueError('Gap prediction is available only for browser sessions.')
            if test_target_x is not None:
                if not dry_run or source != 'web':
                    raise ValueError('The test target is available only for browser dry runs.')
                self.validate({'setpoint_x': test_target_x})
            self.source = source
            c = self.config
            self.plane_detections = 0
            self.launcher_release_us = None
            if not dry_run:
                if max(c['launch_throttle'], c['cruise_throttle'], c['lost_throttle']) > self.remote.limits['throttle'] or c['max_roll'] > self.remote.limits['roll']:
                    raise ValueError(f'Autonomous throttle and Max roll must fit remote limits: throttle {self.remote.limits["throttle"]}%, roll {self.remote.limits["roll"]}%.')
                self.launcher_release_us = prepare_launch(self.remote)
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
            self.test_target_x = test_target_x
            self.predict_gaps = predict_gaps
            self.prediction_anchor = self.predicted_x = None
            self.motion_anchor = self.last_motion = None
            self.plane_motion = []
            self.stationary_hint = False
            self.noise_frames.clear()
            self.owner = secrets.token_urlsafe(24) if source == 'web' else None
            self.started = self.last_heartbeat = self.last_tick = self.phase_started = self.clock()
            self.sequence, self.last_frame = -1, None
            self.loop_samples.clear()
            self.observation_samples.clear()
            self.frame_age = None
            self.last_new_frame = self.started
            self.last_detection = self.filtered_x = self.previous_center = None
            self.filtered_y = None
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
        self.test_target_x = None
        self.prediction_anchor = self.predicted_x = None
        self.stationary_hint = False
        self.noise_frames.clear()
        self.state, self.reason = 'stopped', reason
        self.command = dict(throttle=0, roll=0)
        self.post_release_hold_until = None
        self.throttle_feedback = None
        self.owner = None

    def stop(self, reason):
        self.cancel(reason)
        if self.token and self.remote.token == self.token:
            self.remote.stop(reason)
        self.token = None

    def transition(self, state, reason, now):
        self.state, self.reason, self.phase_started = state, reason, now

    def steering(self, x, velocity):
        c = self.config
        setpoint = self.test_target_x if self.test_target_x is not None else c['setpoint_x']
        error = setpoint - x
        error = math.copysign(max(0, abs(error) - c['deadband']), error)
        roll = c['roll_direction'] * (c['kp'] * error - c['kd'] * velocity)
        return max(-c['max_roll'], min(c['max_roll'], roll))

    def tracking_throttle(self, roll):
        c = self.config
        error = self.filtered_y - c['setpoint_y']
        error = math.copysign(max(0, abs(error) - c['vertical_deadband']), error)
        correction = c['vertical_direction'] * (c['vertical_kp'] * error + c['vertical_kd'] * self.vertical_velocity)
        limit = c['max_vertical_throttle']
        correction = max(-limit, min(limit, correction))
        boost = c['turn_throttle_boost'] * (roll / c['max_roll']) ** 2 if c['max_roll'] else 0
        self.throttle_feedback = dict(image_y=self.filtered_y, vertical_correction=correction, turn_boost=boost)
        throttle_limit = 100 if self.dry_run else self.remote.limits['throttle']
        return max(0, min(throttle_limit, c['cruise_throttle'] + correction + boost))

    def slew_command(self, key, desired, dt):
        if self.state == 'lost':
            return desired
        old = self.command[key]
        step = self.config[key + '_slew'] * dt
        return old + max(-step, min(step, desired - old))

    def predicted_roll(self, now):
        if self.prediction_anchor is None:
            return 0
        last_detection, x, velocity = self.prediction_anchor
        horizon = min(PREDICTION_SECONDS, self.config['loss_timeout'])
        elapsed = now - last_detection
        if elapsed >= horizon:
            self.prediction_anchor = None
            return 0
        shift = max(-PREDICTION_MAX_SHIFT, min(PREDICTION_MAX_SHIFT, velocity * elapsed))
        self.predicted_x = max(0, min(1, x + shift))
        return self.steering(self.predicted_x, velocity) * (1 - elapsed / horizon)

    def has_recent_motion(self, point, now):
        if self.motion_anchor is None:
            self.motion_anchor = point
        elif math.dist(point, self.motion_anchor) >= MOTION_RADIUS:
            self.motion_anchor, self.last_motion = point, now
        return self.last_motion is not None and now - self.last_motion < MOTION_MAX_AGE

    def moving_plane_indexes(self, centers, now):
        # Match closest observations first so clutter and a passing plane keep separate motion histories.
        gap_limit = max(self.config['frame_timeout'], 2 / self.config['feedback_hz'])
        tracks = [track for track in self.plane_motion if now - track.last_seen <= gap_limit]
        matches = sorted((math.dist(center, track.center), index, track_index)
                         for index, center in enumerate(centers)
                         for track_index, track in enumerate(tracks))
        assigned, used_tracks = {}, set()
        for distance, index, track_index in matches:
            if distance > self.config['association_gate']:
                break
            if index not in assigned and track_index not in used_tracks:
                assigned[index] = tracks[track_index]
                used_tracks.add(track_index)
        hold_visible_plane = self.mode == 'visible' and self.state in ('countdown', 'spooling', 'holding_throttle')
        moving = set()
        for index, center in enumerate(centers):
            track = assigned.get(index)
            if track is None:
                track = PlaneMotion(center, center, now)
                tracks.append(track)
            else:
                track.observe(center, now)
            if track.last_motion is not None and (
                    hold_visible_plane or now - track.last_motion < self.config['stationary_timeout']):
                moving.add(index)
        self.plane_motion = tracks
        return moving

    def preview_noise(self, sequence):
        with self.remote.lock:
            return next((boxes for frame, boxes in reversed(self.noise_frames)
                         if frame == sequence), ())

    def observe(self, info, now):
        if info['frames'] == self.last_frame:
            return False
        self.last_frame, self.last_new_frame = info['frames'], now
        self.stationary_hint = False
        self.observation_samples.append(now)
        width, height = info['frame_size']
        candidates = []
        boxes = []
        for d in info['detections']:
            if d.get('label') != 'plane' or d.get('confidence', 0) < self.config['confidence']:
                continue
            box = d.get('box')
            if valid_box(box, width, height):
                x1, y1, x2, y2 = box
                candidates.append(((x1 + x2) / (2 * width), (y1 + y2) / (2 * height)))
                boxes.append(box)
        in_flight = self.state in POST_LAUNCH_STATES
        if self.config['stationary_timeout'] and (in_flight or self.mode == 'visible'):
            moving = self.moving_plane_indexes(candidates, now)
            self.stationary_hint = len(moving) < len(candidates)
            if self.stationary_hint:
                noise = tuple((x1 / width, y1 / height, x2 / width, y2 / height)
                              for index, (x1, y1, x2, y2) in enumerate(boxes) if index not in moving)
                self.noise_frames.append((self.last_frame, noise))
            candidates = [center for index, center in enumerate(candidates) if index in moving]
        if candidates and in_flight:
            self.plane_detections += len(candidates)
            if not self.dry_run and self.remote.recorder:
                self.remote.recorder.update_plane_detections(self.plane_detections)
        # Ambiguity is treated as missing; never silently switch between planes.
        point = candidates[0] if len(candidates) == 1 else None
        if point and self.previous_center and math.dist(point, self.previous_center) > self.config['association_gate']:
            point = None
        if (point is not None and self.predict_gaps and in_flight and not self.config['stationary_timeout']
                and not self.has_recent_motion(point, now)):
            self.stationary_hint = True
            if self.dry_run:
                point = None
        if point is None:
            self.confirmed = 0
            return False
        self.previous_center = point
        self.velocity = 0
        elapsed = max(now - self.last_detection, .001) if self.last_detection is not None else .001
        if self.filtered_x is None:
            self.filtered_x = point[0]
        else:
            filtered = self.filtered_x + self.config['filter_alpha'] * (point[0] - self.filtered_x)
            self.velocity = (filtered - self.filtered_x) / elapsed
            self.filtered_x = filtered
        self.vertical_velocity = 0
        if self.filtered_y is None or self.confirmed == 0:
            self.filtered_y = point[1]
        else:
            filtered_y = self.filtered_y + self.config['vertical_filter_alpha'] * (point[1] - self.filtered_y)
            self.vertical_velocity = (filtered_y - self.filtered_y) / elapsed
            self.filtered_y = filtered_y
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
            self.predicted_x = None
            self.frame_age = age
            if now - self.last_new_frame > c['frame_timeout']:
                return self.stop('Camera frames stopped advancing.')
            if self.state == 'arming':
                if not self.dry_run and self.remote.initializing:
                    self.sequence += 1
                    self.remote.update(self.token, self.sequence, 0, 0)
                    return
                if self.mode == 'visible':
                    reason = 'Waiting for a confirmed moving plane.' if c['stationary_timeout'] else 'Waiting for a confirmed plane.'
                    self.transition('waiting_view', reason, now)
                else:
                    self.transition('countdown', 'Countdown before launch throttle ramp.', now)
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
                    self.transition('spooling', 'Ramping to launch throttle before launcher release.', now)
            throttle, roll = 0, 0
            if self.state in ('spooling', 'holding_throttle'):
                if self.mode == 'visible' and (self.last_detection is None or now - self.last_detection > c['loss_timeout']):
                    return self.stop('Plane lost before launch.')
                throttle = c['launch_throttle']
            if self.state == 'holding_throttle':
                remaining = c['launcher_release_delay'] - (now - self.phase_started)
                self.reason = f'Holding launch throttle. Launcher release in {max(0, remaining):.1f}s.'
                if remaining <= 0:
                    if not self.dry_run:
                        try:
                            release_launcher(self.remote, self.launcher_release_us)
                        except Exception as exc:
                            return self.stop(f'Launcher release failed: {exc}')
                    self.post_release_hold_until = self.clock() + c['post_release_throttle_hold']
                    if c['stationary_timeout']:
                        self.plane_motion.clear()
                        self.last_detection = self.filtered_x = self.previous_center = None
                        self.confirmed = 0
                    self.filtered_y = None
                    self.transition('launch', 'Launcher release commanded. Launch throttle; roll centered.', now)
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
                    else:
                        roll = self.steering(self.filtered_x, self.velocity)
                        if self.predict_gaps and fresh:
                            self.prediction_anchor = (self.last_detection, self.filtered_x, self.velocity)
                if self.state == 'lost':
                    throttle, roll = c['lost_throttle'], 0
                    if not seen:
                        return self.stop('Plane detection timeout. Idle and center requested; restart required.')
                    if self.predict_gaps:
                        predicted_roll = self.predicted_roll(now)
                        if self.dry_run:
                            roll = predicted_roll
                            self.reason = ('Dry run: predicting horizontal position during detection loss.'
                                           if self.predicted_x is not None else
                                           'Detection lost. Roll centered; fallback throttle.')
            # Use the rate-limited roll command for compensation and apply the existing throttle ramp.
            self.command['roll'] = self.slew_command('roll', roll, dt)
            holding_launch_throttle = (self.post_release_hold_until is not None
                                       and now < self.post_release_hold_until)
            if self.state == 'tracking' and not holding_launch_throttle:
                throttle = self.tracking_throttle(self.command['roll'])
            else:
                self.throttle_feedback = None
                if holding_launch_throttle:
                    throttle = c['launch_throttle']
            self.command['throttle'] = self.slew_command('throttle', throttle, dt)
            if not self.dry_run:
                self.sequence += 1
                self.remote.reason = f'Autonomous {self.state}: {self.reason}'
                self.remote.update(self.token, self.sequence, **self.command)
            if self.state == 'spooling' and self.command['throttle'] == c['launch_throttle']:
                self.transition('holding_throttle', 'Holding launch throttle before launcher release.', self.clock())


def camera_status():
    # Fixed loopback source; clients cannot redirect the controller to another host.
    with urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=.25) as response:
        return json.loads(response.read(262144))
