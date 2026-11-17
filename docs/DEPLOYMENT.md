# Deployment

## First installation

On the Pi, place the project at `/home/user/pi-vision` and run `bash scripts/setup.sh`. Reboot if the Hailo driver requires it. Aircraft controls also need `python3-smbus2`, enabled I2C, a GP8413 DAC and calibration. GPIO input needs `python3-gpiozero`.

Configure the user, paths and camera/model arguments in `services/pi-vision.service` and `services/pi-remote.service` before installing and enabling them with systemd. The camera uses `scripts/run-camera.sh`; the controller uses `python3 -m pi_vision.control.remote_control`.

On Windows, `scripts/setup-training.ps1` installs the studio and SSH dependencies. Save the Pi credential once:

```powershell
.venv-training/Scripts/python.exe -m pi_vision.deployment.pi_ssh --save-password
```

SSH requires a trusted host key. Credentials are stored in Windows Credential Manager.

## Update source

Run from the laptop project root:

```powershell
.venv-training/Scripts/python.exe -m pi_vision.deployment.deploy plan
.venv-training/Scripts/python.exe -m pi_vision.deployment.deploy status
.venv-training/Scripts/python.exe -m pi_vision.deployment.deploy audit
.venv-training/Scripts/python.exe -m pi_vision.deployment.deploy apply
```

- `plan`: list files offline.
- `status`: read Pi service status.
- `audit`: compare local and Pi source without changing it.
- `apply`: stage files, back up, restart services and check readiness; attempt rollback on failure.

Use `--host ADDRESS --user USER` for your Pi. `--target all` is the default; `camera` and `controller` update one service. Use `all` for shared changes. The file list lives in `pi_vision/deployment/project_layout.py`.

Older Pi services may still reference root `app.py`, `remote_control.py` or `run.sh`. Deployment installs compatible launchers there from `scripts/legacy/` and `scripts/run-camera.sh`; the local project root stays clear. The offline plan shows these mappings under `local_sources`.

Keep controls and recording idle during updates. Source deployment preserves settings, data, models and service overrides. It does not install dependencies or service units. Model activation is separate; see [Training](TRAINING.md).

## Storage and diagnostics

| Location | Contents |
|---|---|
| `data/recordings/` on Pi | Camera clips |
| `logs/flights.sqlite3` on Pi | Control session logs |
| `models/studio-<sha256>.hef` and `.labels.txt` on Pi | Installed models and labels |
| `.hef-inspection/` on Pi | Model test proofs and diagnostics |
| `maintenance/deployments/` on Pi | Source staging and rollback backups |
| `data/plane-target/` on laptop | Images, annotations and dataset exports |
| `runs/plane-target/` on laptop | Training outputs |

Read camera `/api/status` for the loaded model identity. On the Pi, inspect services and logs with:

```bash
systemctl cat pi-vision pi-remote
journalctl -u pi-vision -u pi-remote -n 80 --no-pager
```

Keep existing model compatibility links and deployment backups if older service overrides or rollback records reference them.
