# Pi Vision

Raspberry Pi camera tracking and experimental aircraft control, with a Windows studio for labeling footage and training YOLO models.

## Start

On Windows, from the project folder:

```powershell
.\scripts\setup-training.ps1
.\scripts\start-training.ps1
```

Open http://127.0.0.1:8010/. Use `scripts/setup-training.ps1 -CpuOnly` for a CPU installation.

On a Raspberry Pi 5 running 64-bit Raspberry Pi OS, run `bash scripts/setup.sh`, then follow [Deployment](docs/DEPLOYMENT.md) to configure hardware, models and services.

| Application | Address |
|---|---|
| Camera | `http://<pi-address>:8000/` |
| Controls, calibration, joystick and flight logs | `http://<pi-address>:8001/` |
| Laptop training studio | http://127.0.0.1:8010/ |

## Guides

- [Where to edit](docs/WHERE_TO_EDIT.md) — code and page locations.
- [Deployment](docs/DEPLOYMENT.md) — Pi setup, source updates and diagnostics.
- [Training](docs/TRAINING.md) — record, label, train and activate models.
- [Autonomous controls](docs/AUTONOMOUS.md) — launch modes, tuning and GPIO.
- [Contributing](docs/CONTRIBUTING.md) — checks and change guidelines.

Models, datasets, recordings, credentials and device settings are local files excluded from Git.

**Before operating hardware:** verify calibration, polarity and stop behavior with propulsion disconnected. On inverted throttle wiring, 0 V can mean full throttle. Keep controls idle during deployment.
