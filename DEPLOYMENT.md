# Deployment

There are three distinct operations: install dependencies, update application source, and activate a trained model. They use different commands and should not be combined implicitly.

## Source updates to an existing Pi installation

Run from this workspace on the laptop:

```powershell
.venv-training/Scripts/python.exe deploy.py plan
.venv-training/Scripts/python.exe deploy.py status
.venv-training/Scripts/python.exe deploy.py audit
# When ready to update source and restart the selected services:
.venv-training/Scripts/python.exe deploy.py apply
```

`plan` is offline. `status` reads Pi APIs. `audit` compares local and remote source SHA256 values using SFTP; it does not upload or restart anything. All output is JSON except apply progress. Use `--host ADDRESS --user USER` to override the defaults.

`--target all` is the default; `camera` or `controller` selects one service. Prefer `all` when changing shared modules. The payload is defined only in [project_layout.py](project_layout.py). It includes each backend's dependencies and served assets.

`apply` requires reachable camera/control APIs and refuses enabled/initializing controls, active autonomy or active camera recording. It stages source, syntax-checks Python on the Pi, backs up previous files, restarts the selected services, checks readiness and attempts rollback on failure. The backup path is printed. Do not use it during a flight or recording. Preflight is a point-in-time check; keep the system idle while deploying.

It preserves runtime JSON settings, calibration, recordings, logs, models and installed systemd overrides. It does not arm the controller or select a HEF. It does not install dependencies or service units. Existing `deploy_autonomous.py`, `pi_ssh.py --deploy-camera` and `deploy-camera.ps1` now use this same workflow; they remain for compatibility.

New staging/backup folders live under `maintenance/deployments/<target>-<id>/`, rather than adding hidden directories to the project root. Previous deployments were retained under `maintenance/archive/`. See [MODEL_STORAGE.md](MODEL_STORAGE.md) for model normalization and cleanup commands.

## SSH credentials

Install `requirements-ssh.txt` in the Python environment used for SSH tooling. On this Windows laptop:

```powershell
.venv-training/Scripts/python.exe pi_ssh.py --save-password
```

Credentials are stored in Windows Credential Manager, not in source files. The Pi's trusted SSH host key must already be present. Web-triggered tools do not prompt interactively for missing credentials.

## First-time installation

On a fresh 64-bit Pi installation, copy the project to `/home/user/pi-vision`, run `bash setup.sh` for camera/Hailo dependencies and reboot if the driver installation requires it. The controller additionally needs its system-Python I2C dependency (`python3-smbus2`) and, when GPIO is used, `python3-gpiozero`. Enable/configure the physical I2C interface and verify calibration before enabling outputs.

`pi-vision.service` and `pi-remote.service` are base unit templates. Install/configure them deliberately for the Pi user and paths. The camera unit's base arguments are defaults, not a record of the live model. Do not replace an existing installed override to recover a supposed default.

On the laptop, `./setup-training.ps1` prepares `.venv-training`; `./start-training.ps1` starts the studio on loopback port 8010. Hailo compilation additionally needs the configured Docker compiler container. Starting the studio does not start or deploy Pi services.

## Model changes

Use the studio's HEF selector, **Test one frame on Pi**, then the separate **Activate on Pi** action after successful full-frame validation. This has its own preflight, exact-artifact proof, maintenance lock, readiness checks and rollback. See [TRAINING.md](TRAINING.md).

`deploy_hailo.py` remains an advanced older workflow for prepared artifacts and saved validation baselines. It is not an alternative source sync command.

## Useful diagnostics

On the Pi, read `systemctl cat pi-vision pi-remote` for effective launch commands and `journalctl -u pi-vision -u pi-remote -n 80 --no-pager` for logs. Verify the active model through `:8000/api/status`, including its SHA, rather than inferring it from a filename or old documentation.

On the laptop, on-disk changes to studio Python need a studio process restart. Browser refresh alone reloads frontend assets; it does not reload the Python backend.
