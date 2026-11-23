# Where to edit

Paths below are relative to `pi_vision/`. Each application keeps its pages and scripts alongside its Python code.

| Change | Files |
|---|---|
| Camera page | [camera/templates/index.html](../pi_vision/camera/templates/index.html) |
| Camera API and capture | [camera/app.py](../pi_vision/camera/app.py), `camera/camera_state.py` |
| Detection and video recording | `camera/inference.py`, `camera/camera_recording.py` |
| Controls pages | [control/templates/](../pi_vision/control/templates): `remote.*`, `autonomous.*`, `joystick.*`, `flights.*`, `calibration.html` |
| Control API and hardware | [control/remote_control.py](../pi_vision/control/remote_control.py), `control/control_calibration.py`, `control/gp8413_bus.py`, `control/launch_input.py` |
| Launcher servo calibration | `control/templates/calibration.html`, `control/templates/launcher.js`, `control/launcher_calibration.py` |
| Autonomous steering | [control/autonomous.py](../pi_vision/control/autonomous.py) |
| Training page | [studio/templates/training.html](../pi_vision/studio/templates/training.html), [studio/static/training.js](../pi_vision/studio/static/training.js) |
| Studio API, datasets and imports | `studio/training_studio.py`, `studio/training_data.py`, `studio/import_video.py`, `studio/video_collection.py` |
| Training and compilation | `studio/train_yolo.py`, `studio/training_output.py`, `studio/compile_hailo.py` |
| Shared browser controls and detection geometry | `shared/static/`, `shared/spatial_detection.py` |
| SSH, deployment and copied-file list | `deployment/`, especially [project_layout.py](../pi_vision/deployment/project_layout.py) |
| Model inspection, activation and diagnostics | `maintenance/` |

At the repository root:

- `config/` — default labels and GPIO example.
- `services/` — systemd service templates.
- `requirements/` — laptop dependencies.
- `tests/` — automated checks.
- `docs/` — operating guides.
- `scripts/` — setup, studio launch and camera deployment commands.

Start the camera with `bash scripts/run-camera.sh` and the controller with `python3 -m pi_vision.control.remote_control`. Edit their implementations under `pi_vision/`. `scripts/legacy/` contains deployment compatibility launchers for older Pi services.

Run Python tools from the project root with `python -m pi_vision.<area>.<module>`. Restart the affected Python process after backend edits; refresh the browser after frontend edits.

Runtime JSON settings stay at the root. `data/`, `runs/`, `models/` and `logs/` hold generated data. Starter YOLO weights are cached in `models/pretrained/`. Top-level `maintenance/` holds archives and deployment backups; `pi_vision/maintenance/` holds source code.
