# Websites and page ownership

There are **three applications** and one optional tunnel. All Pi control pages share one process and one set of hardware state.

| URL | Purpose | Backend | HTML | JavaScript / CSS |
|---|---|---|---|---|
| Pi `:8000/` | Camera preview, detection settings, recording | `app.py` | `templates/index.html` | Inline UI, `templates/camera-preview.js`, `static/recording.js` |
| Pi `:8001/` | Manual throttle and roll | `remote_control.py` | `templates/remote.html` | `templates/remote.js`, `templates/remote.css` |
| Pi `:8001/autonomous` | Launch modes, controller tuning, live camera preview | Same controls backend | `templates/autonomous.html` | `templates/autonomous.js`, shared camera preview |
| Pi `:8001/calibration` | DAC/channel calibration | Same controls backend | `templates/calibration.html` | Inline UI |
| Pi `:8001/joystick` | Browser gamepad control | Same controls backend | `templates/joystick.html` | `templates/joystick.js` |
| Pi `:8001/flights` | Flight command logs / export | Same controls backend | `templates/flights.html` | `templates/flights.js` |
| Laptop `:8010/` | Capture/import, labels, training, PT/HEF inspection | `training_studio.py` | `templates/training.html` | `static/training.js`, `static/recording.js` |
| Laptop `:8002/joystick` | Same Pi joystick page through localhost | `joystick_tunnel.py` forwards Pi `:8001` | Same Pi files | Same Pi files |

The controls backend serves its asset allowlist itself; it is not Flask. Camera and studio use Flask. Keep assets with the owning entrypoint in [project_layout.py](project_layout.py) when changing the Pi payload.

## Running processes

| Host | Process/service | Python environment | Launch |
|---|---|---|---|
| Pi | `pi-vision.service` | `.venv` with system packages | `run.sh` -> `app.py` |
| Pi | `pi-remote.service` | `/usr/bin/python3` | `remote_control.py` |
| Laptop | Training studio | `.venv-training` | `start-training.ps1` |
| Laptop | Optional SSH tunnel | Environment with SSH requirements | `joystick_tunnel.py` |

`pi-vision.service` in this workspace is a base unit. The installed service can have an overriding `ExecStart` containing the active HEF and camera settings. Inspect `systemctl cat pi-vision`; do not overwrite that override during a source update.

## Camera streams and model testing

- Pi `:8000/raw.jpg` and `/raw.mjpg`: clean camera images for labeling.
- Pi `:8000/snapshot.jpg` and `/stream.mjpg`: detection overlays for viewing.
- Pi `:8000/api/status`: detector/camera state and actual performance; browser preview FPS is a separate limit.
- Studio PT inspection runs on the laptop. One-frame HEF inspection runs on the Pi and temporarily pauses its camera service under a maintenance lock.
- Studio live HEF testing continuously displays the Pi's active annotated stream. The selected HEF must match the loaded model SHA. Starting live viewing does not activate a different HEF.
- HEF activation is a separate explicit action after a successful full-frame test. See [TRAINING.md](TRAINING.md).

For production, GPIO can start the autonomous controller without any browser. The pin is not selected and GPIO remains disabled. See [AUTONOMOUS.md](AUTONOMOUS.md).
