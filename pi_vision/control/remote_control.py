#!/usr/bin/env python3
"""Calibrated GP8413 remote and supervised autonomous control service.

Manual controls work independently of the camera. Autonomy reads camera status
over loopback HTTP without importing camera or inference libraries.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import math
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from pi_vision.control.control_calibration import Calibration, voltage
from pi_vision.control.flight_recorder import FlightRecorder
from pi_vision.control.autonomous import Autonomous, camera_status
from pi_vision.shared.state_store import save_json
from pi_vision.shared.camera_operation import operation_guard

LOG = logging.getLogger(__name__)


class Remote:
    TIMEOUT = 1.2

    def __init__(self, bus, calibration_path=None, recorder=None):
        self.bus = bus
        self.recorder = recorder
        self.launch_input = None
        self.lock = threading.RLock()
        self.token = None
        self.last_seen = 0
        self.sequence = -1
        self.initializing = False
        self.mode = 'normal'
        self.calibration_path = Path(calibration_path) if calibration_path else None
        self.calibration = Calibration.load(self.calibration_path) if self.calibration_path else Calibration()
        self.settings_path = self.calibration_path.with_name('remote-settings.json') if self.calibration_path else None
        self.power_path = self.calibration_path.with_name('remote-power.json') if self.calibration_path else None
        self.outputs_zero = False
        if self.power_path and self.power_path.exists():
            saved_power = json.loads(self.power_path.read_text())
            if type(saved_power.get('outputs_zero')) is not bool:
                raise ValueError('Invalid saved output power state.')
            self.outputs_zero = saved_power['outputs_zero']
        self.limits = {'throttle': 50, 'roll': 50}
        self.sensitivity = {'rise': 30, 'fall': 30, 'decay': 10}
        if self.settings_path and self.settings_path.exists():
            saved = json.loads(self.settings_path.read_text())
            self._validate_limits(saved.get('throttle'), saved.get('roll'))
            self.limits = {'throttle': saved['throttle'], 'roll': saved['roll']}
            sensitivity = {**self.sensitivity, **saved.get('sensitivity', {})}
            self._validate_sensitivity(sensitivity.get('rise'), sensitivity.get('fall'), sensitivity.get('decay'))
            self.sensitivity = dict(sensitivity)
        self.throttle = self.roll = None  # No guessed voltage before calibration.
        self.error = None
        self.reason = ('Stopped. Saved idle and center requested.' if self.calibration.complete
                       else 'Stopped. Complete setup before control.')
        bus.write_byte_data(0x58, 0x01, 0)  # Select 5 V mode, then apply known neutrals.
        self._safe()
        self.autonomy = Autonomous(self, self.calibration_path.with_name('autonomous-settings.json') if self.calibration_path else None)

    def _output(self, channel, volts):
        voltage(volts)
        code = int(volts / 5 * 32767) << 1
        self.bus.write_i2c_block_data(0x58, 2 + channel * 2, [code & 255, code >> 8])
        if channel == 1:
            self.throttle = volts
        else:
            self.roll = volts

    def _safe(self):
        if self.outputs_zero:
            try:
                # One transaction sets BOTH channels; 0 V is not calibrated idle.
                self.bus.write_i2c_block_data(0x58, 2, [0, 0, 0, 0])
                self.throttle = self.roll = 0
                self.error = None
            except OSError as exc:
                self.error = str(exc)
                raise
            self.reason = 'Both outputs requested at 0 V. Switch the physical remote off now.'
            return
        failures = []
        for channel, name in ((1, 'throttle_idle'), (0, 'roll_center')):
            target = self.calibration.points.get(name)
            # Unknown neutral: retain output instead of inventing a voltage.
            if target is None:
                continue
            try:
                self._output(channel, target)
            except OSError as exc:
                failures.append(str(exc))
        self.error = '; '.join(failures) or None
        if failures:
            raise OSError(self.error)

    def status(self):
        with self.lock:
            return dict(api_version=2, camera_operation_guard=True, enabled=self.token is not None,
                        initializing=self.initializing, outputs_zero=self.outputs_zero,
                        throttle_volts=self.throttle,
                        roll_volts=self.roll, error=self.error, reason=self.reason,
                        limit=self.limits['throttle'], limits=dict(self.limits), mode=self.mode,
                        sensitivity=dict(self.sensitivity),
                        calibration=dict(self.calibration.points),
                        calibration_complete=self.calibration.complete,
                        gpio_launch=self.launch_input.status() if self.launch_input else None,
                        flight_log=self.recorder.status() if self.recorder else
                            dict(available=False, recording=False, latest_id=None, error=None))

    def _record(self, event, throttle=None, roll=None, sequence=None, finish=False):
        if self.recorder and self.mode == 'normal':
            self.recorder.record(event, dict(throttle_percent=throttle, roll_percent=roll,
                throttle_volts=self.throttle, roll_volts=self.roll, sequence=sequence,
                reason=self.reason, initializing=self.initializing, outputs_zero=self.outputs_zero), finish=finish)

    @staticmethod
    def _validate_limits(throttle, roll):
        for value in (throttle, roll):
            if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 100:
                raise ValueError('Limits must be from 1 to 100%.')

    @staticmethod
    def _validate_sensitivity(rise, fall, decay):
        for value in (rise, fall):
            if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 100:
                raise ValueError('Throttle speeds must be from 1 to 100 percentage points per second.')
        if type(decay) not in (int, float) or not math.isfinite(decay) or not 0 <= decay <= 100:
            raise ValueError('Throttle decay must be from 0 to 100 percentage points per second.')

    def _save_settings(self, limits, sensitivity):
        if self.settings_path:
            save_json(self.settings_path, {**limits, 'sensitivity': sensitivity}, indent=2)

    def set_sensitivity(self, rise, fall, decay=None):
        with self.lock:
            if self.token is not None:
                raise ValueError('Stop controls before changing throttle speeds.')
            if decay is None:
                decay = self.sensitivity['decay']
            self._validate_sensitivity(rise, fall, decay)
            sensitivity = dict(rise=rise, fall=fall, decay=decay)
            self._save_settings(self.limits, sensitivity)
            self.sensitivity = sensitivity
            return self.status()

    def set_limits(self, throttle, roll):
        with self.lock:
            if self.token is not None:
                raise ValueError('Stop controls before changing limits.')
            if not self.calibration.complete:
                raise ValueError('Complete calibration first.')
            self._validate_limits(throttle, roll)
            limits = dict(throttle=throttle, roll=roll)
            self._save_settings(limits, self.sensitivity)
            self.limits = limits
            return self.status()

    def stop(self, reason='Stopped. Saved throttle idle and roll center restored where known.'):
        with self.lock:
            if hasattr(self, 'autonomy'):
                self.autonomy.cancel(reason)
            self.token = None
            self.initializing = False
            self.reason = reason
            try:
                self._safe()
            finally:
                self._record('stop', None if self.outputs_zero else 0,
                             None if self.outputs_zero else 0, finish=True)
            return self.status()

    def _save_power_state(self, outputs_zero):
        if self.power_path:
            save_json(self.power_path, {'outputs_zero': outputs_zero})

    def power_off(self, confirmed=False):
        with self.lock:
            if confirmed is not True:
                raise ValueError('Confirm that you are holding the drone. 0 V can command full throttle.')
            self.autonomy.cancel('Remote outputs switched to zero mode.')
            # Persist before writing, so service restart cannot restore idle voltage.
            self._save_power_state(True)
            self.outputs_zero = True
            self.token = None
            self.initializing = False
            try:
                self._safe()
            finally:
                self._record('power_off', finish=True)
            return self.status()

    def enable(self, mode='normal', restore_outputs=False, source='keyboard'):
        with self.lock, operation_guard():
            if source not in ('keyboard', 'joystick', 'autonomous'):
                raise ValueError('Invalid input source')
            if self.autonomy.active:
                raise ValueError('Stop the autonomous controller first.')
            if mode not in ('normal', 'calibration'):
                raise ValueError('Invalid control mode')
            if self.token is not None:
                raise ValueError('Remote already in use. Press Stop first.')
            if mode == 'normal' and not self.calibration.complete:
                raise ValueError('Save all five calibration points first.')
            if self.outputs_zero:
                if restore_outputs is not True:
                    raise ValueError('Confirm restoring calibrated output voltages before enabling controls.')
                self._save_power_state(False)
                self.outputs_zero = False
            self._safe()
            self.mode = mode
            self.token = secrets.token_urlsafe(24)
            self.last_seen = time.monotonic()
            self.sequence = -1
            self.reason = 'Setup active' if mode == 'calibration' else 'Controls active'
            if mode == 'normal' and self.recorder:
                self.recorder.begin(source, dict(calibration=dict(self.calibration.points),
                    limits=dict(self.limits), sensitivity=dict(self.sensitivity),
                    **({'pi_vision.control.autonomous': dict(self.autonomy.config), 'autonomous_trigger': self.autonomy.source}
                       if source == 'autonomous' else {})))
                self._record('enable', 0, 0)
            return {**self.status(), 'token': self.token}

    def _check(self, token):
        if not self.token or not isinstance(token, str) or not secrets.compare_digest(token, self.token):
            raise ValueError('Remote stopped. Enable controls again.')
        if time.monotonic() - self.last_seen > self.TIMEOUT:
            self.stop('Connection lost. Saved idle and center restored where known.')
            raise ValueError('Connection expired. Enable controls again.')

    def _sequence(self, sequence):
        if type(sequence) is not int or sequence <= self.sequence:
            raise ValueError('Invalid or outdated command sequence')
        self.sequence = sequence
        self.last_seen = time.monotonic()

    def update(self, token, sequence, throttle, roll):
        with self.lock:
            self._check(token)
            if self.mode == 'calibration':
                # Setup commands are explicit volts; missing channels are untouched.
                if throttle is not None:
                    voltage(throttle)
                if roll is not None:
                    voltage(roll)
                if roll is not None and 'throttle_idle' not in self.calibration.points:
                    raise ValueError('Save throttle idle before adjusting roll.')
                t, r = throttle, roll
            else:
                t, r = self.calibration.outputs(throttle, roll,
                    throttle_limit=self.limits['throttle'], roll_limit=self.limits['roll'])
            self._sequence(sequence)
            if not self.initializing:
                try:
                    if t is not None:
                        self._output(1, t)
                    if r is not None:
                        self._output(0, r)
                except OSError:
                    self.stop('DAC write failed')
                    raise
            self._record('control_ignored' if self.initializing else 'control', throttle, roll, sequence)
            return self.status()

    def save_point(self, token, sequence, name, volts):
        with self.lock:
            self._check(token)
            if self.mode != 'calibration' or self.initializing:
                raise ValueError('Enter setup first.')
            candidate = self.calibration.with_point(name, volts)
            self._sequence(sequence)
            channel = 1 if name.startswith('throttle_') else 0
            try:
                self._output(channel, volts)
                if self.calibration_path:
                    candidate.save(self.calibration_path)
                self.calibration = candidate
                self._safe()  # Full throttle/roll endpoint tests return to known neutrals.
            except OSError:
                self.stop('Could not save calibration or apply output')
                raise
            self.reason = name.replace('_', ' ').capitalize() + ' saved. Known neutrals restored.'
            return self.status()

    def initialize(self, token):
        with self.lock:
            self._check(token)
            if self.mode != 'normal' or not self.calibration.complete:
                raise ValueError('Complete setup and enable normal control before initialization.')
            if self.initializing:
                raise ValueError('Initialization already running')
            self._safe()
            self.initializing = True
            self.reason = 'Initializing with calibrated throttle endpoints'
        threading.Thread(target=self._sweep, args=(token,), daemon=True).start()
        return self.status()

    def _sweep(self, token):
        try:
            for percent in (0, 25, 50, 75, 100, 75, 50, 25, 0):
                with self.lock:
                    if token != self.token:
                        return
                    self._check(token)
                    t, r = self.calibration.outputs(percent, 0, initialization=True)
                    self._output(1, t)
                    self._output(0, r)
                    self._record('initialization', percent, 0)
                time.sleep(.05)
            with self.lock:
                if token == self.token:
                    self._safe()
                    self.initializing = False
                    self.reason = 'Armed. Ready for control.'
                    self._record('armed', 0, 0)
        except (OSError, ValueError):
            with self.lock:
                if token == self.token:
                    try:
                        self.stop('Initialization failed')
                    except OSError:
                        pass

    def watchdog(self):
        with self.lock:
            if self.error or (self.token and time.monotonic() - self.last_seen > self.TIMEOUT):
                self.stop('Connection lost or DAC error. Saved idle and center restored where known.')


def handler_for(remote):
    class Handler(BaseHTTPRequestHandler):
        # Keep snapshot requests on one connection instead of reconnecting per frame.
        protocol_version = 'HTTP/1.1'
        disable_nagle_algorithm = True

        def send_content(self, body, content_type, code=200, headers=None):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def reply(self, value, code=200):
            self.send_content(json.dumps(value).encode(), 'application/json', code)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == '/api/autonomous/camera':
                try:
                    return self.reply(camera_status())
                except (OSError, ValueError):
                    return self.reply({'error': 'Camera unavailable.'}, 503)
            if parsed.path == '/camera/preview.jpg':
                query = urllib.parse.parse_qs(parsed.query)
                after = query.get('after', ['-1'])[0]
                if not re.fullmatch(r'-?\d{1,16}', after):
                    return self.reply({'error': 'Invalid frame sequence.'}, 400)
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8000/snapshot.jpg?after=' + after, timeout=3) as response:
                        body = response.read(4_000_001)
                        if len(body) > 4_000_000 or not response.headers.get('Content-Type', '').startswith('image/jpeg'):
                            raise ValueError('Invalid preview image.')
                        headers = {key: response.headers[key] for key in ('X-Frame-Sequence', 'X-Frame-Age') if key in response.headers}
                    return self.send_content(body, 'image/jpeg', headers=headers)
                except (OSError, ValueError):
                    return self.reply({'error': 'Camera preview unavailable.'}, 503)
            if self.path == '/api/autonomous':
                return self.reply(remote.autonomy.status())
            if self.path == '/api/status':
                return self.reply(remote.status())
            if self.path == '/api/flights':
                return self.reply(dict(flights=remote.recorder.list_flights() if remote.recorder else [],
                                       recorder=remote.status()['flight_log']))
            download = re.fullmatch(r'/api/flights/([0-9]{8}T[0-9]{6}-[a-f0-9]{8})\.(csv|json)', self.path)
            if download:
                try:
                    flight_id, format = download.groups()
                    body = remote.recorder.download(flight_id, format) if remote.recorder else None
                    if body is None:
                        return self.reply({'error': 'Flight not found'}, 404)
                    return self.send_content(body, 'text/csv; charset=utf-8' if format == 'csv' else 'application/json',
                        headers={'Content-Disposition': f'attachment; filename="flight-{flight_id}.{format}"'})
                except (OSError, sqlite3.Error):
                    return self.reply({'error': 'Could not read flight log.'}, 503)
            assets = {
                '/autonomous': ('autonomous.html', 'text/html; charset=utf-8'),
                '/autonomous.js': ('autonomous.js', 'text/javascript; charset=utf-8'),
                '/camera-preview.js': ('camera-preview.js', 'text/javascript; charset=utf-8'),
                '/': ('remote.html', 'text/html; charset=utf-8'),
                '/calibration': ('calibration.html', 'text/html; charset=utf-8'),
                '/joystick': ('joystick.html', 'text/html; charset=utf-8'),
                '/joystick.js': ('joystick.js', 'text/javascript; charset=utf-8'),
                '/flights': ('flights.html', 'text/html; charset=utf-8'),
                '/flights.js': ('flights.js', 'text/javascript; charset=utf-8'),
                '/remote.css': ('remote.css', 'text/css; charset=utf-8'),
                '/remote.js': ('remote.js', 'text/javascript; charset=utf-8'),
            }
            if self.path not in assets:
                return self.reply({'error': 'Not found'}, 404)
            filename, content_type = assets[self.path]
            folder = Path(__file__).resolve().parent
            asset = (folder.parent / 'shared/static/camera-preview.js'
                     if filename == 'camera-preview.js' else folder / 'templates' / filename)
            body = asset.read_bytes()
            self.send_content(body, content_type, headers={'X-Frame-Options': 'DENY'})

        def do_POST(self):
            # Only same-origin JSON requests; no permissive CORS or form commands.
            host = self.headers.get('Host', '')
            hostname = host.split(':')[0].lower()
            try:
                ipaddress.ip_address(hostname)
                valid_host = True
            except ValueError:
                valid_host = hostname in ('afc-rpi.local', 'afc-rpi', 'localhost')
            origin = self.headers.get('Origin')
            if (not valid_host or (origin and origin != 'http://' + host)
                    or self.headers.get('Sec-Fetch-Site') == 'cross-site'
                    or self.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
                return self.reply({'error': 'Use the remote page on this Pi.'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048:
                    raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object')
                if self.path != '/api/stop' and data.get('version') != 2:
                    raise ValueError('Refresh the remote page for the new calibrated controls.')
                if self.path == '/api/stop':
                    reasons = dict(manual='Stopped by pilot. Saved idle and center restored where known.',
                        page_left='Control page hidden, closed or unfocused.',
                        joystick_disconnected='Joystick disconnected or mapping unavailable.',
                        joystick_not_ready='Joystick not at idle and center after arming.',
                        client_error='Browser connection or control error.')
                    reason = data.get('reason', 'manual')
                    if not isinstance(reason, str) or reason not in reasons:
                        raise ValueError('Invalid stop reason')
                    result = remote.stop(reasons[reason])
                elif self.path == '/api/autonomous/settings':
                    result = remote.autonomy.save(data.get('config'))
                elif self.path == '/api/autonomous/camera':
                    with remote.lock:
                        if remote.token or remote.autonomy.active:
                            raise ValueError('Stop controls before changing camera FPS.')
                        fps = data.get('fps')
                        if type(fps) is not int or not 1 <= fps <= 60:
                            raise ValueError('Camera FPS must be an integer from 1 to 60.')
                        req = urllib.request.Request('http://127.0.0.1:8000/api/settings',
                            data=json.dumps({'fps': fps}).encode(), headers={'Content-Type': 'application/json'})
                        try:
                            with urllib.request.urlopen(req, timeout=2) as response:
                                result = json.load(response)
                        except urllib.error.HTTPError as exc:
                            raise ValueError(json.load(exc).get('error', 'Camera rejected FPS.')) from exc
                        except OSError as exc:
                            raise ValueError('Camera unavailable.') from exc
                elif self.path == '/api/autonomous/start':
                    # HTTP clients always use browser supervision; source is never client-selectable.
                    result = remote.autonomy.start_from_camera(data.get('mode'), data.get('dry_run', True), data.get('restore_outputs', False))
                elif self.path == '/api/autonomous/heartbeat':
                    result = remote.autonomy.heartbeat(data.get('owner'))
                elif self.path == '/api/enable':
                    if data.get('source') == 'pi_vision.control.autonomous':
                        raise ValueError('Use the autonomous start endpoint.')
                    result = remote.enable(data.get('mode', 'normal'), data.get('restore_outputs', False), data.get('source', 'keyboard'))
                elif self.path == '/api/off':
                    result = remote.power_off(data.get('confirmed', False))
                elif self.path == '/api/limits':
                    result = remote.set_limits(data.get('throttle'), data.get('roll'))
                elif self.path == '/api/sensitivity':
                    result = remote.set_sensitivity(data.get('rise'), data.get('fall'), data.get('decay'))
                elif self.path == '/api/save-point':
                    result = remote.save_point(data.get('token'), data.get('sequence'), data.get('point'), data.get('volts'))
                elif self.path == '/api/initialize':
                    result = remote.initialize(data.get('token'))
                elif self.path == '/api/control':
                    result = remote.update(data.get('token'), data.get('sequence'),
                                           data.get('throttle'), data.get('roll'))
                else:
                    return self.reply({'error': 'Not found'}, 404)
                self.reply(result)
            except (ValueError, TypeError) as exc:
                self.reply({'error': str(exc)}, 400)
            except OSError:
                self.reply({'error': 'DAC error — switch off the remote if motion continues.'}, 503)

        def log_message(self, *args):
            pass
    return Handler


def control_worker(remote, done):
    while not done.is_set():
        began = time.monotonic()
        try:
            remote.watchdog()
            if remote.autonomy.active:
                previous_state = remote.autonomy.state
                try:
                    info = camera_status()
                except Exception:
                    info = None
                try:
                    remote.autonomy.tick(info)
                except Exception:
                    remote.autonomy.stop('Autonomous controller error. Idle and center requested.')
                if remote.autonomy.state != previous_state:
                    LOG.info('Autonomous %s (%s): %s', remote.autonomy.state,
                             remote.autonomy.source, remote.autonomy.reason)
        except OSError:
            pass  # Retry calibrated neutral outputs on the next watchdog tick.
        period = 1 / remote.autonomy.config['feedback_hz'] if remote.autonomy.active else .1
        # Include HTTP/inference observation time in the period, avoiding fixed-sleep drift.
        done.wait(max(.001, period - (time.monotonic() - began)))



def main():
    from pi_vision.control.gp8413_bus import open_bus
    from pi_vision.control.launch_input import GpioLaunch
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--no-web', action='store_true', help='Run GPIO/flight control without the development web server.')
    parser.add_argument('--launch-input', type=Path, default=Path(__file__).resolve().parents[2].joinpath('gpio-launch.json'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    # Both development and headless modes share one exclusive DAC owner.
    control_lock = Path(__file__).resolve().parents[2].joinpath('.control.lock').open('a')
    try:
        fcntl.flock(control_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('The aircraft controller is already running.')
    bus = open_bus()
    recorder = FlightRecorder(Path(__file__).resolve().parents[2] / 'logs' / 'flights.sqlite3')
    remote = Remote(bus, Path(__file__).resolve().parents[2].joinpath('remote-calibration.json'), recorder=recorder)
    done = threading.Event()
    workers = []
    launch_input = None

    def shutdown(signum, frame):
        raise SystemExit()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, shutdown)
    def start_workers():
        workers.append(threading.Thread(target=control_worker, args=(remote, done), daemon=True))
        if launch_input.config['enabled'] and launch_input.device is not None:
            workers.append(threading.Thread(target=launch_input.run, args=(done,), daemon=True))
        for worker in workers:
            worker.start()

    try:
        launch_input = remote.launch_input = GpioLaunch(remote, args.launch_input)
        if args.no_web:
            start_workers()
            print('Headless aircraft controller running; development web server disabled.', flush=True)
            while not done.wait(1):
                pass
        else:
            with ThreadingHTTPServer((args.host, args.port), handler_for(remote)) as server:
                start_workers()
                print(f'Remote listening on {args.host}:{args.port}; GPIO launch configuration loaded', flush=True)
                server.serve_forever()
    finally:
        done.set()
        try:
            remote.stop('Server stopped')
        finally:
            for worker in workers:
                worker.join(timeout=3)
            if launch_input:
                launch_input.close()
            bus.close()
            recorder.close()
            control_lock.close()


if __name__ == '__main__':
    main()
