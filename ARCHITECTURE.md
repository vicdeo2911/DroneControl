# Code map

Root module paths are intentionally stable: service commands, scripts and tests import them directly. This guide groups them by responsibility without changing those imports.

## Pi camera application - port 8000

| File | Responsibility |
|---|---|
| `app.py` | Flask routes, camera lifecycle and pipeline coordination |
| `inference.py` | Hailo worker / model lifecycle and inference |
| `monitoring.py` | CPU/GPU/NPU monitoring |
| `camera_state.py` | Shared settings, latest frames and preview metadata |
| `camera_modes.py` | Capture validation and sensor mode selection |
| `camera_recording.py` | Clean camera video recording, clip metadata/list/delete |
| `spatial_detection.py` | Detection geometry, crops, tile merging and association; also used by studio |
| `run.sh`, `setup.sh`, `pi-vision.service` | Launch, first-time native dependencies and base service unit |
| `labels.txt`, `plane-labels.txt` | Default class-name files; active models may have their own labels |

Capture, inference and JPEG preview consume latest frames rather than queuing old observations. A slow preview does not directly set the controller feedback rate.

## Pi controls application - port 8001

| File | Responsibility |
|---|---|
| `remote_control.py` | HTTP routes, control ownership/watchdog, worker scheduling and hardware integration |
| `autonomous.py` | Launch state machine, target selection and bounded image-space PD control |
| `control_calibration.py` | Validated calibration and percentage-to-voltage conversion |
| `gp8413_bus.py` | GP8413 I2C transactions |
| `launch_input.py` | Optional debounced GPIO launch trigger |
| `gpio-launch.example.json` | Configuration example; not the live GPIO settings |
| `flight_recorder.py` | Asynchronous SQLite command/session logs; not camera video |
| `pi-remote.service` | Base control service unit |

`--no-web` runs the controller without serving the development pages. Do not start another controller beside the service: `.control.lock` prevents two processes owning the DAC.

## Laptop studio - port 8010

| File | Responsibility |
|---|---|
| `training_studio.py` | Flask routes and orchestration for library, labels, training and inspection |
| `training_data.py` | Annotation database, session splits and immutable dataset exports |
| `video_collection.py` | Download a completed Pi clip and import sampled frames |
| `import_video.py` | Local video sampling CLI |
| `train_yolo.py` | Train / validate / test / export / compile CLI; separate training process |
| `training_output.py` | ONNX export and optional HEF compilation orchestration |
| `compile_hailo.py` | Hailo Docker compiler entrypoint |
| `hailo_inspection.py` | HEF discovery and SSH bridge for Pi inspection/activation |
| `setup-training.ps1`, `start-training.ps1` | Laptop dependencies and studio launch |
| `requirements-training.txt`, `requirements-ssh.txt` | Laptop ML/web and SSH dependencies |

## Shared state, maintenance and deployment

| File | Responsibility |
|---|---|
| `state_store.py` | Atomic JSON persistence for Pi settings |
| `camera_operation.py` | Shared camera maintenance lock, used by camera/controller |
| `inspect_hailo.py` | Pi maintenance manager: preflight, pause camera, inspect, restore |
| `verify_hailo.py` | Pi NPU test worker for a saved image |
| `activate_hailo.py` | Pi model installation, exact tested SHA checks, service override and rollback |
| `project_layout.py` | Single authoritative source payloads and protected runtime paths |
| `deploy.py` | Canonical source plan/status/audit/apply CLI |
| `pi_deploy.py` | Source staging, backup, syntax check, restart, readiness and rollback |
| `pi_ssh.py` | Trusted-host SSH, Windows Credential Manager, compatibility camera deploy command |
| `deploy_autonomous.py`, `deploy-camera.ps1` | Compatibility wrappers for the shared source deployment |
| `deploy_hailo.py` | Older prepared-artifact validation/activation workflow; not source deployment |
| `joystick_tunnel.py` | Optional localhost SSH forwarding for browser gamepad access |
| `benchmark_detection.py` | Live performance sampler; no model training |
| `tidy_storage.py` | Explicit local/Pi artifact normalization, inventory and archival; no service restart |
| `tests/test_*.py` | Unit/integration regression checks, mocked hardware |
| `tests/check_*.py` | Browser / smoke checks; read each script before running against real services |

The older prepared-artifact model workflow is retained because it is still callable. New deployments should use studio testing/activation. No unreferenced runtime module was removed merely because it was absent from an incomplete deployment list.

## Data and ownership

| Location | Owner / meaning |
|---|---|
| Pi `/home/user/pi-vision/data/recordings/` | Clean AVI clips and recording metadata |
| Pi `logs/flights.sqlite3` plus WAL/SHM | Controller command/session telemetry |
| Pi `models/` | Installed HEFs and labels |
| Pi root `camera-settings.json` | Camera/preview settings |
| Pi root `remote-calibration.json`, `remote-settings.json`, `remote-power.json` | Calibration, manual settings and persisted power state |
| Pi root `autonomous-settings.json`, `gpio-launch.json` | Controller tuning and optional GPIO configuration |
| Pi `/etc/systemd/system/pi-vision.service.d/` | Active camera/model service override |
| Laptop `data/plane-target/` | Annotation SQLite, images, imported video and dataset exports |
| Laptop `runs/plane-target/<run>/` | Training logs, weights and optional compiled HEFs |
| `.hef-inspection/`, `test-artifacts/` | Maintenance proofs/diagnostics and test evidence |
| `docs/history/` | Archived documentation, not active configuration |
| `maintenance/` | Archived duplicate artifacts/deployments and cleanup/source-update journals |

See [MODEL_STORAGE.md](MODEL_STORAGE.md) for the canonical HEF paths and compatibility aliases. Old deployment folders have been moved out of the Pi root; future source updates stage and back up under `maintenance/deployments/`.

Source deployment excludes runtime data and settings. Model activation updates model artifacts and its service override separately. Disk source equality does not prove a long-running Python process has reloaded it.
