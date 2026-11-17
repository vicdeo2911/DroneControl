"""Optional debounced GPIO start command. Pin stays unassigned until configured."""
import json
import logging
import math
from pathlib import Path
import time

DEFAULTS = dict(enabled=False, pin=None, active_high=True, debounce_seconds=.05,
                mode='behind', dry_run=True, restore_outputs=False)
LOG = logging.getLogger(__name__)


def load_config(path):
    changes = json.loads(Path(path).read_text()) if path and Path(path).exists() else {}
    if not isinstance(changes, dict) or changes.keys() - DEFAULTS.keys():
        raise ValueError('Unknown GPIO launch setting.')
    config = {**DEFAULTS, **changes}
    for key in ('enabled', 'active_high', 'dry_run', 'restore_outputs'):
        if type(config[key]) is not bool:
            raise ValueError(f'GPIO {key} must be true or false.')
    pin = config['pin']
    if pin is not None and (type(pin) is not int or not 0 <= pin <= 27 or pin in (2, 3)):
        raise ValueError('Choose a BCM GPIO number from 0 to 27, excluding I2C pins 2 and 3.')
    if config['enabled'] and pin is None:
        raise ValueError('Assign a GPIO pin before enabling launch input.')
    delay = config['debounce_seconds']
    if type(delay) not in (int, float) or not math.isfinite(delay) or not .01 <= delay <= 1:
        raise ValueError('GPIO debounce_seconds must be from 0.01 to 1.')
    if config['mode'] not in ('behind', 'visible'):
        raise ValueError('GPIO mode must be behind or visible.')
    return config


def open_input(config):
    from gpiozero import DigitalInputDevice
    return DigitalInputDevice(config['pin'], pull_up=not config['active_high'])


class GpioLaunch:
    def __init__(self, remote, path=None, *, clock=time.monotonic, device_factory=open_input):
        self.remote, self.clock = remote, clock
        self.config = load_config(path)
        self.device = None
        self.error = None
        self.event = 'GPIO launch disabled; no pin assigned.'
        self.ready = False
        self.candidate = self.stable = None
        self.changed_at = self.clock()
        if self.config['enabled']:
            try:
                self.device = device_factory(self.config)
                self.event = 'Waiting for a stable inactive level before accepting a start edge.'
            except Exception as exc:
                self.error = 'GPIO input unavailable: ' + str(exc)

    def status(self):
        with self.remote.lock:
            return dict(config=dict(self.config), available=self.device is not None,
                        ready_for_edge=self.ready, event=self.event, error=self.error)

    def poll(self):
        with self.remote.lock:
            if self.device is None or self.error:
                return
            try:
                active = bool(self.device.is_active)
            except Exception as exc:
                self.error = 'GPIO input read failed: ' + str(exc)
                self.ready = False
                if self.remote.autonomy.active and self.remote.autonomy.source == 'gpio':
                    self.remote.autonomy.stop(self.error)
                return
            now = self.clock()
            if active != self.candidate:
                self.candidate, self.changed_at = active, now
            if now - self.changed_at < self.config['debounce_seconds'] or active == self.stable:
                return
            self.stable = active
            if not active:
                self.ready = True
                self.event = 'Ready for a start edge.'
                return
            if not self.ready:
                self.event = 'Active level ignored; first return the signal to inactive.'
                return
            self.ready = False
            if self.remote.autonomy.active or self.remote.token:
                self.event = 'Start edge ignored; controls already active.'
                return
            try:
                self.remote.autonomy.start_from_camera(self.config['mode'],
                    dry_run=self.config['dry_run'], restore_outputs=self.config['restore_outputs'], source='gpio')
                self.event = 'GPIO autonomous launch started.'
            except Exception as exc:
                # A rejected command is consumed, never queued for automatic retry.
                self.event = 'Start rejected: ' + str(exc)
            LOG.info('%s', self.event)

    def run(self, done):
        while not done.wait(.01):
            self.poll()

    def close(self):
        if self.device is not None:
            self.device.close()
