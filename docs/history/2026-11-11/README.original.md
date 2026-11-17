# Pi Vision

This folder contains the Raspberry Pi camera/Hailo setup and the latest plane remote.

Current configuration: **1080p50 capture/recording, full-frame 640-input YOLOv8s
at roughly 34-36 FPS, and 50 Hz controller feedback**. The detector/controller
confidence cutoffs are 0.55. Native crop mode is experimental pending matching
retraining and validation after a real-plane regression. See
[TRAINING.md](TRAINING.md) for the current correction and earlier experiments.

## Local plane + target training

Run `.\start-training.ps1` on this laptop, then open http://localhost:8010.
The studio supports clean camera capture/photo import, two-class bounding-box
annotation (`plane`, `target`), session-separated YOLO export, local CUDA training,
and detection/relative-position preview. See [TRAINING.md](TRAINING.md) for the
complete collection → labeling → training → evaluation workflow and Pi camera update.
Supervised autonomous hand launch and horizontal tracking are available in the new controller; see [AUTONOMOUS.md](AUTONOMOUS.md) for deployment, parameters and detection-loss behavior. Physical flight validation is pending.

Video-first collection is ready: **Record video** on the Pi camera page or laptop
studio, then choose the completed clip and **Extract frames** in the studio.
Clean clips stay on the Pi under `data/recordings/`; downloaded copies stay under
`data/plane-target/videos/` on the laptop. Preserve both directories. The studio
preview uses continuous MJPEG (measured ~15 fps), replacing its old ~2 fps polling.

## Access

See [WEB.md](WEB.md) for a map of every page and how the services relate.

- SSH: `ssh user@192.168.137.190`
- Camera: http://192.168.137.190:8000/
- Remote: http://192.168.137.190:8001/
- Autonomous controller: http://192.168.137.190:8001/autonomous
- Calibration: http://192.168.137.190:8001/calibration
- Joystick (laptop connection): http://localhost:8002/joystick
- Flight logs: http://192.168.137.190:8001/flights (or http://localhost:8002/flights)
- Pi folder: `/home/user/pi-vision`

## Camera and accelerator

Installed hardware: Raspberry Pi 5, Hailo-8 (`HAILO8`), IMX708 Camera Module 3
on CAM0. HailoRT/firmware 4.23.0. The camera was index 0 when connected.
Active model: `/home/user/pi-vision/models/plane-target-20261109-022355-9ed07a-1920-h8.hef`,
with `models/plane-labels.txt` (`plane`, `target`). The systemd model override
selects it. The fallback apt model is `/usr/share/hailo-models/yolov8s_h8.hef`.

On a fresh 64-bit Raspberry Pi OS installation:

```bash
cd /home/user/pi-vision
bash setup.sh
sudo apt-get install -y python3-smbus2
chmod +x setup.sh run.sh
sudo reboot
```

After reboot, verify the hardware:

```bash
hailortcli fw-control identify
rpicam-hello --list-cameras
```

Use the native apt packages and the `.venv` created with system-site-packages.
Keep the Hailo driver, runtime, firmware and Python bindings compatible. Do not
replace native camera/NPU modules with pip packages. Existing setup uses
`camera_auto_detect=1`; no camera/PCIe boot changes were required.

The working pipeline is `app.py` + `inference.py` + `monitoring.py`, with
`labels.txt` and `templates/index.html`. It captures camera frames, runs the
compiled Hailo YOLO model, and serves annotated images. `inference.py` manages
HailoRT resources directly because the tested Picamera2 Hailo helper crashed
on shutdown. Current capture is 1920 x 1080 at a target 50 fps; recording resolution/FPS and camera zoom are configurable.

Install/start the camera service:

```bash
sudo install -m 644 pi-vision.service /etc/systemd/system/pi-vision.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-vision
```

For a manual run, stop `pi-vision` first, then run `./run.sh`.
Two programs must not own the camera/NPU concurrently.

Latest check (2026-11-09): trained plane/target Hailo inference is active at
960 x 540, about 15 fps. Camera streaming and all control pages were verified.
The refactored controller and autonomous UI are deployed; calibrated settings,
zero-output mode, active model, recordings and flight history were preserved.

## Latest remote

`remote_control.py` serves both remote pages, independently of camera/inference.
`gp8413_bus.py` uses raw I2C transactions through system `python3-smbus2`.
`control_calibration.py` supplies the calibrated mappings.

- GP8413: I2C bus 1, address `0x58`; VOUT1 throttle, VOUT0 roll.
- Set 5 V range using register `0x01 = 0x00`; pause 10 ms before output codes.
- Code: `int(volts / 5 * 32767) << 1`, low byte first.
- Software restricts requested voltages to 0-3.3 V. Displayed values are commands,
  not measurements. Constant `0x11` register reads are not voltage feedback.
- An earlier measured 6.66 V for a 3.3 V request was resolved after raw-I2C bench
  checks/deployment; its root cause remains unproven. User verified both 1 V
  bench outputs and then 3.3 V idle / 1.65 V center from the service.

Calibration page saves throttle idle/full and roll left/center/right. All five
are required. Arm aircraft runs saved idle/full/idle, then keeps controls enabled.
W/S ramp throttle; releasing W gradually reduces throttle toward idle. A/D
steer while held and release to center. Sliders
and touch buttons also work. Save independent throttle/roll limits from 1-100%
while stopped after calibration. Defaults are 50% each.

Under Remote sensitivity, save separate throttle rise, fall and release decay
speeds while stopped. Rise/fall range from 1 to 100 percentage points per second,
defaulting to 30. Release decay defaults to 10 pp/s: throttle fades from 50% to
idle in 5 seconds without W/S input. Keep holding or pressing W to add throttle;
S lowers throttle at the fall speed and takes priority if both keys are held.
Decay applies to touch buttons and after setting the throttle slider too. Set
decay to 0 to hold throttle on release. Settings persist in `remote-settings.json`
alongside the limits; older files receive the default decay. Stop still restores
idle immediately.

Stop, Space/Escape, window blur, page hide/close and a 1.2-second connection
watchdog restore saved idle/center in normal mode.

Off remote requires a warning/holding-drone confirmation, requests both outputs
at 0 V, then prompts physical remote power-off. **0 V is full throttle with the
saved inverted wiring.** This is not electrical isolation or physical switch
control. User observed apparent signal back-powering (LED/buzzer remained active
when VIN was cut). Zero mode persists through Stop, watchdogs and restarts;
explicit confirmation is required to restore calibrated output voltages.

Preserve these ignored runtime files during deployment:

- `remote-calibration.json`: throttle idle 3.3 V, full 0 V; roll 0/1.65/3.3 V.
- `remote-settings.json`: latest saved limits throttle 100%, roll 50%.
- `remote-power.json`: latest observed `outputs_zero: true`.

Do not arm or restore idle simply to inspect the project. Physical switch state
is unknown; the last API check reported both requested outputs at 0 V, no error.

Install/start the remote service:

```bash
sudo install -m 644 pi-remote.service /etc/systemd/system/pi-remote.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-remote
```

Read-only checks:

```bash
systemctl status pi-remote pi-vision --no-pager
curl --fail http://127.0.0.1:8001/api/status
journalctl -u pi-remote -n 30 --no-pager
```

## Remote API and checks

### Laptop joystick

`/joystick` shows live gamepad axes and saves a throttle/roll mapping in the
laptop browser. Pitch and yaw are ignored. Use Identify by moving or choose
axis numbers, capture throttle idle/full and roll left/center/right, then save.
Capturing endpoints handles reversed axes and off-center sticks. A configurable
roll dead zone filters small center movements. Previewing never arms or sends
control commands. Mapping is specific to the selected controller and browser
origin. Changes are locked while armed or another tab is in control.

Put throttle at idle and roll at center before arming. Joystick positions set
throttle/roll directly as proportions of the saved limits; keyboard decay does
not apply on this page. Disconnect, invalid input, Space/Escape, page blur/hide,
and the existing server watchdog stop control. Returning to the page or
reconnecting never rearms automatically.

Browsers require a secure context for Gamepad access, so the plain Pi IP page
links to a localhost connection. On the laptop, keep this running:

```powershell
python joystick_tunnel.py
```

The Windows tunnel reuses the saved Pi password from Windows Credential Manager.
Open http://localhost:8002/joystick
in Edge/Chrome. Move a stick or press a controller button to expose the device
to the browser. The tunnel binds only to laptop loopback and requires an SSH
host key already trusted in `~/.ssh/known_hosts`. It uses installed `paramiko`
and `keyring` (install with `python -m pip install -r requirements-ssh.txt`).
The password is stored in the Windows account vault, outside this project.
Closing the tunnel
disconnects the page and the Pi watchdog restores its saved neutral outputs.
An equivalent SSH tunnel is `ssh -N -L 127.0.0.1:8002:127.0.0.1:8001 user@192.168.137.190`.

For agent/terminal SSH commands on this laptop, use
`python pi_ssh.py --command "rpicam-hello --list-cameras"`. The password is already
saved in Windows Credential Manager under service `pi-vision:ssh:192.168.137.190`,
username `user`. Do not put it in source files or logs. `--sudo` uses the saved
password via SSH stdin. `--save-password` prompts once to replace the stored
credential. Native `ssh.exe` does not automatically read this custom vault entry.

### Flight data

Every normal armed session is automatically logged on the Pi. Joystick setup
previews and voltage calibration do not create flight logs. Both control pages
show recording status and link to `/flights`, where the most recent 100 sessions
can be downloaded as CSV, including a recording in progress. Logs persist in
`logs/flights.sqlite3` across browser closes and service restarts. Preserve the
`logs/` directory during deployment and backup (SQLite may also have `-wal` and
`-shm` files while the service is running).

CSV rows include UTC time, elapsed seconds, input source, event, logical throttle
and roll percentages, requested voltages, sequence, initialization/zero-output
flags, and stop reason. Arming sweep rows are labeled `initialization`; commands
received during the sweep are labeled `control_ignored`. Setup JSON includes
the flight summary plus calibration, limits and sensitivity snapshots. Tokens
and passwords are never logged. These are command records, not measured flight
speed, altitude, attitude, GPS, or voltage feedback.

Stop, page leave/blur, joystick disconnect, power-off, DAC errors and watchdog
timeouts finish the recording. An unclosed log after a hard interruption is
marked `Service interrupted` on the next startup. Disk writes use a bounded
background queue so a storage failure cannot block the aircraft control path;
recorder errors are shown on the page. A hard power loss can lose pending rows.
The flight source is set by `/api/enable` using `source: "keyboard"` (default)
or `source: "joystick"`. `GET /api/flights` lists sessions and recorder state;
`GET /api/flights/<id>.csv` downloads samples, and `.json` downloads metadata.

### Control API

Same-origin JSON POSTs use `version: 2`. Acquire a session with `/api/enable`,
then send token and increasing integer sequence on `/api/control` every 200 ms.
Normal commands use logical percentages; calibration mode uses explicit volts.
`/api/save-point` saves calibration; `/api/initialize` runs the arming sweep.
`/api/limits` sets stopped-session limits. `/api/stop` also accepts `{}`.
`/api/sensitivity` saves stopped-session throttle speeds using `rise`, `fall`,
and optional `decay` (0-100). Omitting decay preserves its current value.
`/api/off` requires literal `confirmed: true`; enabling from zero mode requires
literal `restore_outputs: true`. Stop preserves zero when zero mode is active.

The remaining tests use a fake DAC and do not operate the hardware:

```bash
python3 -m unittest discover -s tests
```

The current local suite has 120 passing tests, with browser checks for all workflows. Browser checks
also passed WASD, arming persistence, calibration, saved limits, off confirmation,
zero-mode persistence and responsive layouts. Old screenshots/reports and
standalone test tools were removed during cleanup.


The autonomous page includes a live camera preview with measured browser FPS,
frame age, actual detection FPS and controller feedback Hz. Camera FPS and
controller feedback rate can be adjusted separately while stopped. Testing on
the current model achieved 30 detections/s and about 29.4 preview fps at a
30 fps camera target, with NPU utilization around 35%. See AUTONOMOUS.md for
measurements, rate selection and latency details.


For production, autonomous launch can receive a debounced GPIO start command.
No pin is assigned yet and the input is disabled by default. GPIO-started flights
run locally without a browser heartbeat. The development web server is optional
(`remote_control.py --no-web`); see AUTONOMOUS.md for `gpio-launch.json` and the
startup/edge behavior. Browser-started development sessions retain their existing
connection watchdog.


Detector input resolution is selectable for training, preview and Hailo
compilation (320-1920 in steps of 32; typical comparisons: 640, 960, 1280).
Existing weights can be recompiled with `train_yolo.py compile --imgsz 960`
without retraining. The active HEF is now 1920x1920, compiled and checked on Hailo-8. See TRAINING.md for commands and current limitations.


Current collection profile: 1080p50. Camera and studio pages offer recording
resolution/rate selectors. A 60-second live test recorded 2975 frames at 49.6 fps
with zero recording-queue drops. Hailo detection is isolated in a separate process
and runs about 7-8 fps at the active 1920-square input. See TRAINING.md for the
measured tradeoffs, high-resolution retraining workflow, and file-size limits.
