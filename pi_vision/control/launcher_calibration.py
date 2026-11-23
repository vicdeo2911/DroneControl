"""Momentary PWM for unloaded launcher servo calibration."""
from pathlib import Path
import json
import time

from pi_vision.shared.state_store import save_json

PIN = 12
DIRECTION_PULSES = {'clockwise': 1200, 'anticlockwise': 1800}
PWM_HZ = 50
PERIOD_US = 1_000_000 // PWM_HZ
NEUTRAL_US = 1500
START_CYCLES = PWM_HZ
STOP_CYCLES = 75
MOVE_CYCLES = 75


def pwm_channel():
    # Discover by RP1 PWM0 device address because pwmchip numbers can change.
    for chip in Path('/sys/class/pwm').glob('pwmchip*'):
        if (chip / 'device').resolve().name == '1f00098000.pwm':
            channel = chip / 'pwm0'
            if channel.exists():
                return channel
    raise ValueError('GPIO12 hardware PWM is unavailable. Run the launcher PWM setup service.')


def send_pwm(pulse, cycles, *, return_to_neutral=True):
    channel = pwm_channel()
    completed = False
    try:
        (channel / 'enable').write_text('0')
        (channel / 'duty_cycle').write_text('0')
        (channel / 'period').write_text(str(PERIOD_US * 1000))
        (channel / 'polarity').write_text('normal')
        initial = NEUTRAL_US if return_to_neutral else pulse
        (channel / 'duty_cycle').write_text(str(initial * 1000))
        (channel / 'enable').write_text('1')
        # Hardware generates every edge. Sleeps only set the duration of stages.
        if return_to_neutral:
            time.sleep(START_CYCLES / PWM_HZ)
            (channel / 'duty_cycle').write_text(str(pulse * 1000))
        time.sleep(cycles / PWM_HZ)
        if return_to_neutral:
            (channel / 'duty_cycle').write_text(str(NEUTRAL_US * 1000))
            time.sleep(STOP_CYCLES / PWM_HZ)
        completed = True
    finally:
        # Keep sending position pulses after successful movement.
        if return_to_neutral or not completed:
            try:
                (channel / 'duty_cycle').write_text('0')
            finally:
                (channel / 'enable').write_text('0')


def stop_signal(remote):
    with remote.lock:
        if remote.autonomy.active:
            raise ValueError('Stop autonomous controls before stopping the launcher signal.')
        channel = pwm_channel()
        try:
            (channel / 'duty_cycle').write_text('0')
        finally:
            (channel / 'enable').write_text('0')
        remote.launcher_last_pulse = None
        remote.launcher_loaded = False
        return {'stopped': True}


def validate_pulse(pulse):
    if type(pulse) is not int or not 500 <= pulse <= 3000:
        raise ValueError('Choose a whole-number pulse from 500 to 3000 microseconds.')
    return pulse


def positions(remote):
    with remote.lock:
        path = remote.calibration_path.with_name('launcher-settings.json') if remote.calibration_path else None
        saved = json.loads(path.read_text()) if path and path.exists() else {}
        if not isinstance(saved, dict) or saved.keys() - {'lock_us', 'release_us'}:
            raise ValueError('Invalid launcher settings file.')
        for pulse in saved.values():
            validate_pulse(pulse)
        return dict(pin=PIN, lock_us=saved.get('lock_us'), release_us=saved.get('release_us'),
                    loaded=getattr(remote, 'launcher_loaded', False),
                    last_pulse_us=getattr(remote, 'launcher_last_pulse', None))


def reload_launcher(remote):
    with remote.lock:
        if remote.token or remote.initializing or remote.autonomy.active:
            raise ValueError('Stop aircraft controls before reloading the launcher.')
        if getattr(remote.launch_input, 'config', {}).get('pin') == PIN:
            raise ValueError('GPIO12 is assigned to the launch input.')
        saved = positions(remote)
        if saved['lock_us'] is None or saved['release_us'] is None or saved['lock_us'] == saved['release_us']:
            raise ValueError('Save distinct Lock and Release positions in Calibration first.')
        remote.launcher_loaded = False
        remote._safe()
        send_pwm(saved['lock_us'], MOVE_CYCLES, return_to_neutral=False)
        remote.launcher_last_pulse = saved['lock_us']
        remote.launcher_loaded = True
        return positions(remote)


def prepare_launch(remote):
    saved = positions(remote)
    if not saved['loaded'] or saved['lock_us'] is None or saved['release_us'] is None:
        raise ValueError('Reload the launcher before starting autonomous flight.')
    if saved['lock_us'] == saved['release_us'] or saved['last_pulse_us'] != saved['lock_us']:
        raise ValueError('Launcher Lock and Release calibration is invalid.')
    if getattr(remote.launch_input, 'config', {}).get('pin') == PIN:
        raise ValueError('GPIO12 is assigned to the launch input.')
    channel = pwm_channel()
    if ((channel / 'enable').read_text().strip() != '1'
            or (channel / 'period').read_text().strip() != str(PERIOD_US * 1000)
            or (channel / 'duty_cycle').read_text().strip() != str(saved['lock_us'] * 1000)):
        raise ValueError('Launcher lock signal is unavailable. Reload the launcher.')
    # A stopped or failed session needs an explicit reload before another start.
    remote.launcher_loaded = False
    return saved['release_us']


def release_launcher(remote, pulse):
    channel = pwm_channel()
    if (channel / 'enable').read_text().strip() != '1':
        raise ValueError('Launcher hardware PWM stopped before release.')
    (channel / 'duty_cycle').write_text(str(validate_pulse(pulse) * 1000))
    remote.launcher_last_pulse = pulse


def save_position(remote, data):
    if data.keys() - {'version', 'position', 'pulse_us'}:
        raise ValueError('Unknown launcher position setting.')
    position = data.get('position')
    if position not in ('lock', 'release'):
        raise ValueError('Choose lock or release.')
    pulse = validate_pulse(data.get('pulse_us'))
    with remote.lock:
        if remote.autonomy.active:
            raise ValueError('Stop autonomous controls before saving launcher positions.')
        if pulse != getattr(remote, 'launcher_last_pulse', None):
            raise ValueError('Apply this pulse and check the position before saving it.')
        if remote.calibration_path is None:
            raise ValueError('Launcher settings storage is unavailable.')
        saved = positions(remote)
        candidate = {key: saved[key] for key in ('lock_us', 'release_us') if saved[key] is not None}
        candidate[position + '_us'] = pulse
        save_json(remote.calibration_path.with_name('launcher-settings.json'), candidate, indent=2)
        remote.launcher_loaded = False
        return positions(remote)


def jog(remote, data):
    if data.get('unloaded') is not True:
        raise ValueError('Confirm the launcher is unloaded before moving the servo.')
    if data.keys() - {'version', 'direction', 'pulse_us', 'position', 'unloaded'}:
        raise ValueError('Refresh the calibration page to use the direction buttons.')
    if sum(key in data for key in ('direction', 'pulse_us', 'position')) != 1:
        raise ValueError('Choose a direction, pulse width, or saved position.')

    with remote.lock:
        if remote.token or remote.initializing or remote.autonomy.active:
            raise ValueError('Stop aircraft controls before calibrating the launcher.')
        if getattr(remote.launch_input, 'config', {}).get('pin') == PIN:
            raise ValueError(f'GPIO{PIN} is assigned to the launch input. Resolve the pin conflict first.')
        remote.launcher_loaded = False
        direction = data.get('direction')
        if 'direction' in data:
            if not isinstance(direction, str) or direction not in DIRECTION_PULSES:
                raise ValueError('Choose clockwise or anticlockwise.')
            pulse = DIRECTION_PULSES[direction]
        elif 'position' in data:
            if data['position'] not in ('lock', 'release'):
                raise ValueError('Choose lock or release.')
            pulse = positions(remote)[data['position'] + '_us']
            if pulse is None:
                raise ValueError('Save this launcher position first.')
        else:
            pulse = validate_pulse(data['pulse_us'])
        try:
            send_pwm(pulse, MOVE_CYCLES, return_to_neutral='direction' in data)
        except Exception as exc:
            raise ValueError(f'Could not jog GPIO{PIN}: {exc}') from exc
        remote.launcher_last_pulse = None if 'direction' in data else pulse
        return {'direction': direction, 'pulse_us': pulse, 'duration_seconds': MOVE_CYCLES / PWM_HZ}
