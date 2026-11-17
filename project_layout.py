"""Authoritative Pi source payloads. See ARCHITECTURE.md for code ownership."""
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PI_ROOT = '/home/user/pi-vision'

SHARED_FILES = ('state_store.py', 'camera_operation.py', 'templates/camera-preview.js')
CAMERA_RUNTIME = (
    'app.py', 'inference.py', 'monitoring.py', 'camera_state.py', 'camera_modes.py',
    'camera_recording.py', 'spatial_detection.py', 'run.sh', 'labels.txt', 'plane-labels.txt',
    'templates/index.html', 'static/recording.js',
)
HEF_TOOLS = ('inspect_hailo.py', 'activate_hailo.py', 'verify_hailo.py')
CONTROLLER_RUNTIME = (
    'remote_control.py', 'autonomous.py', 'control_calibration.py', 'gp8413_bus.py',
    'flight_recorder.py', 'launch_input.py', 'gpio-launch.example.json',
    'templates/remote.html', 'templates/remote.js', 'templates/remote.css',
    'templates/autonomous.html', 'templates/autonomous.js', 'templates/calibration.html',
    'templates/joystick.html', 'templates/joystick.js', 'templates/flights.html', 'templates/flights.js',
)


def unique(files):
    return tuple(dict.fromkeys(files))


CAMERA_FILES = unique(CAMERA_RUNTIME + HEF_TOOLS + SHARED_FILES)
CONTROLLER_FILES = unique(CONTROLLER_RUNTIME + SHARED_FILES)
EXECUTABLE_FILES = ('run.sh',)
RUNTIME_FILES = (
    'camera-settings.json', 'camera-settings.tmp', 'remote-calibration.json', 'remote-calibration.tmp',
    'remote-settings.json', 'remote-settings.tmp', 'remote-power.json', 'remote-power.tmp',
    'autonomous-settings.json', 'autonomous-settings.tmp', 'gpio-launch.json',
    '.control.lock', '.camera-operation.lock',
)
RUNTIME_DIRECTORIES = ('data', 'runs', 'models', 'logs', 'maintenance', '.venv', '.venv-training',
                       'test-artifacts', '.hef-inspection', '.git', '__pycache__')


@dataclass(frozen=True)
class DeploymentTarget:
    files: tuple
    services: tuple
    health_urls: tuple


TARGETS = {
    'camera': DeploymentTarget(CAMERA_FILES, ('pi-vision',), ('http://127.0.0.1:8000/healthz',)),
    'controller': DeploymentTarget(CONTROLLER_FILES, ('pi-remote',), ('http://127.0.0.1:8001/api/autonomous',)),
    'all': DeploymentTarget(unique(CAMERA_FILES + CONTROLLER_FILES), ('pi-vision', 'pi-remote'),
        ('http://127.0.0.1:8000/healthz', 'http://127.0.0.1:8001/api/autonomous')),
}


def validate_source_paths(files):
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in name or ':' in name:
            raise ValueError('Deployment sources must stay inside the project.')
        if path.name in RUNTIME_FILES or path.parts[0] in RUNTIME_DIRECTORIES:
            raise ValueError('Runtime state/data cannot be included in a source deployment: ' + name)


def validate_layout(root):
    files = TARGETS['all'].files
    validate_source_paths(files)
    missing = [name for name in files if not (Path(root) / name).is_file()]
    if missing:
        raise ValueError('Missing deployment sources: '+', '.join(missing))
