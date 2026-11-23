# Deployment

## First installation

On the Pi, place the project at `/home/user/pi-vision` and run `bash scripts/setup.sh`. Reboot if the Hailo driver requires it. Aircraft controls also need `python3-smbus2`, enabled I2C, a GP8413 DAC and calibration. GPIO input needs `python3-gpiozero`.

The **Launcher servo** section of the plane remote's **Calibration** page has Clockwise and Anticlockwise buttons for the servo on BCM GPIO12 (physical header pin 32). On the Pi 5 it uses RP1 PWM0 channel 0 through the Linux hardware PWM interface. Unload the launcher and disconnect propulsion before calibration.

Each press generates continuous 50 Hz hardware pulses through three stages using the working ESP32 test timing. It sends 1500 microseconds for 1 second, 1200 microseconds clockwise or 1800 microseconds anticlockwise for 1.5 seconds, and 1500 microseconds for 1.5 seconds. It then releases the signal. Each command takes approximately 4 seconds. These direction names follow the user's ESP32 test.

To save launcher endpoints, enter a position pulse from 500 to 3000 microseconds, press **Apply position**, inspect it, then use **Save as Lock** or **Save as Release**. **Go to Lock** and **Go to Release** recall those settings. These position commands keep hardware PWM running at the selected pulse width to hold the endpoint, with no centre return. The request completes after 1.5 seconds while PWM continues. Use **Stop servo signal** to disable PWM. Stopping the signal may make the servo move. Start near the current position and adjust in small steps. Settings persist in `launcher-settings.json` beside `remote-calibration.json`. Saving requires a successfully applied pulse in the running controller. The original Clockwise and Anticlockwise buttons retain their startup and centre-return test sequence.

The Autonomous page uses these saved endpoints. **Reload launcher** commands Lock while controls are stopped. Each live autonomous start requires Reload, runs the arming sequence and throttle ramp, then holds launch throttle for **Launcher release delay** before commanding Release once. Reload again before the next flight.

For hardware servo PWM, add `dtoverlay=pwm,pin=12,func=4` under `[all]` in `/boot/firmware/config.txt`. Reboot to load the overlay, or apply it immediately with `sudo dtoverlay pwm pin=12 func=4`. Install `services/pi-launcher-pwm.service` in `/etc/systemd/system/` and enable it with `sudo systemctl enable --now pi-launcher-pwm`. The service runs `scripts/prepare-launcher-pwm.sh user`, sets GPIO12 to RP1 alternate function 0, exports PWM0 channel 0, and grants the controller user access to that channel. Configure a `pi-remote.service` drop-in with `Requires=pi-launcher-pwm.service` and `After=pi-launcher-pwm.service` under `[Unit]`. Adjust the service path and username for another installation. Chip discovery uses the RP1 PWM0 device address, so it tolerates changes in pwmchip numbering. Setup leaves the servo PWM disabled until a website command arrives.

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
