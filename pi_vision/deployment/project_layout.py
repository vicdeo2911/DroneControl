"""Authoritative Pi source payloads. See docs/WHERE_TO_EDIT.md for code ownership."""
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PI_ROOT = '/home/user/pi-vision'

# Remote compatibility destinations differ from their local source locations.
SOURCE_FILES = {
    'app.py': 'scripts/legacy/app.py',
    'remote_control.py': 'scripts/legacy/remote_control.py',
    'run.sh': 'scripts/run-camera.sh',
}

SHARED_FILES = ('pi_vision/__init__.py', 'pi_vision/shared/__init__.py','pi_vision/shared/state_store.py', 'pi_vision/shared/camera_operation.py', 'pi_vision/shared/static/camera-preview.js')
CAMERA_RUNTIME = ('pi_vision/camera/__init__.py', 'app.py',
    'pi_vision/camera/app.py', 'pi_vision/camera/inference.py', 'pi_vision/camera/monitoring.py', 'pi_vision/camera/camera_state.py', 'pi_vision/camera/camera_modes.py',
    'pi_vision/camera/camera_recording.py', 'pi_vision/shared/spatial_detection.py', 'scripts/run-camera.sh', 'run.sh', 'config/labels.txt', 'config/plane-labels.txt',
    'pi_vision/camera/templates/index.html', 'pi_vision/shared/static/recording.js',
)
HEF_TOOLS = ('pi_vision/maintenance/__init__.py','pi_vision/maintenance/inspect_hailo.py', 'pi_vision/maintenance/activate_hailo.py', 'pi_vision/maintenance/verify_hailo.py')
CONTROLLER_RUNTIME = ('pi_vision/control/__init__.py', 'remote_control.py',
    'pi_vision/control/remote_control.py', 'pi_vision/control/autonomous.py', 'pi_vision/control/control_calibration.py', 'pi_vision/control/gp8413_bus.py',
    'pi_vision/control/flight_recorder.py', 'pi_vision/control/launch_input.py', 'config/gpio-launch.example.json',
    'pi_vision/control/templates/remote.html', 'pi_vision/control/templates/remote.js', 'pi_vision/control/templates/remote.css',
    'pi_vision/control/templates/autonomous.html', 'pi_vision/control/templates/autonomous.js', 'pi_vision/control/templates/calibration.html',
    'pi_vision/control/templates/joystick.html', 'pi_vision/control/templates/joystick.js', 'pi_vision/control/templates/flights.html', 'pi_vision/control/templates/flights.js',
)


def unique(files):
    return tuple(dict.fromkeys(files))


CAMERA_FILES = unique(CAMERA_RUNTIME + HEF_TOOLS + SHARED_FILES)
CONTROLLER_FILES = unique(CONTROLLER_RUNTIME + SHARED_FILES)
EXECUTABLE_FILES = ('scripts/run-camera.sh', 'run.sh')
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


def source_path(root, name):
    """Resolve a Pi destination to its checked-in source file."""
    source = SOURCE_FILES.get(name, name)
    validate_source_paths((name, source))
    return Path(root) / source


def validate_layout(root):
    files = TARGETS['all'].files
    validate_source_paths(files)
    missing = [name for name in files if not source_path(root, name).is_file()]
    if missing:
        raise ValueError('Missing deployment sources: '+', '.join(missing))
