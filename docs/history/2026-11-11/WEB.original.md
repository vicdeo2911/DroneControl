# Continuous Pi live preview

Studio inspection now offers **Start live Pi preview** for the selected active
HEF, plus a separate **Test one frame on Pi** action. Live viewing streams the Pi's
annotated output continuously and polls its actual detector metrics. It does not
pause inference, activate anything or command the aircraft. For another HEF use
the existing test/activation workflow first. Model hashes are verified so a
mismatched model is refused. Stop live viewing to use inspection controls again.

The Pi source changes are deployed. The main laptop studio was idle when checked,
but automatic approval review rejected restarting it with `blocked by policy`.
The restart command never ran; the original server remains available. Manually
stop the existing studio, run `start-training.ps1` again, then refresh localhost8010.
The existing Pi camera page at http://192.168.137.190:8000/ also remains live.

# Tested-model activation added

The studio now offers **Activate on Pi** separately from HEF testing. Run a
**Full frame** test first, review the result, then confirm activation of the exact
file. Activation preserves camera/control parameters, restarts camera inference,
and restores the previous detector if startup fails. It does not arm the aircraft.
Let current training and compilation finish before restarting the studio backend
and refreshing the page. See TRAINING.md for the full workflow.

# Compiled-model inspection available

The laptop studio inspection panel now has **Model format: Standard YOLO (.pt) /
Compiled Hailo (.hef)**. HEF input is fixed. Image and one-camera-frame tests run
on the Pi and briefly pause/restart camera inference, preserving the active model.
Stop controls/autonomy and recording first. Selecting a HEF does not activate it.
See TRAINING.md for details. Let a running training/compilation finish before
restarting the studio backend to load this feature; then refresh the page.

# Current correction (2026-11-10)

Camera/controller now use full-frame640 YOLOv8s, confidence .55, capture1080p50,
and controller feedback50Hz. The earlier crop mode is experimental after a
real-plane regression; see TRAINING.md. Studio defaults to full-frame preparation
and inspection. Native crops remain available for retraining experiments.

IPv4 access recovered during the false-detection investigation; user confirmed
http://192.168.137.190:8001/ works and direct Python camera HTTP/SSH also work.
The earlier IPv6 forwarding notes below are a historical workaround, not required
while IPv4 connectivity works. Restart an existing laptop studio to load its new
backend and refresh the page to load the current preparation defaults.

# Web pages and services

There are three applications: the Pi camera, the Pi control service, and the
laptop training studio. The laptop joystick tunnel exposes the same Pi control
service on localhost so browsers can use a gamepad.

| Page | Address | Purpose |
| --- | --- | --- |
| Camera | http://192.168.137.190:8000/ | Live Hailo detections, performance/temperature, FPS/zoom settings and clean video recording. |
| Manual remote | http://192.168.137.190:8001/ | Keyboard/touch/sliders for throttle and roll; arming, limits, sensitivity and Stop. |
| Autonomous controller | http://192.168.137.190:8001/autonomous | Live camera preview, camera FPS and feedback Hz, behind-camera or visible launch, dry run, horizontal PD steering, detection-loss handling and saved parameters. |
| Calibration | http://192.168.137.190:8001/calibration | Save throttle idle/full and roll left/center/right voltages. |
| Flight logs | http://192.168.137.190:8001/flights | Download command history as CSV and setup/session metadata as JSON. |
| Joystick | http://localhost:8002/joystick | Detect/map a laptop gamepad, capture endpoints, preview axes and fly with it. Requires the SSH tunnel below. |
| Training studio | http://localhost:8010/ | Capture/import images, record/extract video, label plane/target boxes, export data, train standard YOLO or additionally compile Hailo, and preview detections. |

The Pi camera and control services run automatically with systemd. The autonomous,
manual, calibration, joystick and log pages share one control service; only one
controller can own the aircraft at a time. Leaving a web-started active control page stops
its session. GPIO-started autonomous sessions run locally and have no browser dependency. The studio only observes detections and manages training data; its
preview never commands the aircraft.

Start the laptop studio in a PowerShell terminal:

```powershell
.\start-training.ps1
```

Start the joystick tunnel in a separate terminal:

```powershell
python joystick_tunnel.py
```

Keep those terminals open. The Pi has a direct `/joystick` page as well, but
browsers restrict gamepad access on an ordinary HTTP LAN address. Localhost
provides the required browser context. All control pages are accessible through
the tunnel too, for example http://localhost:8002/autonomous and
http://localhost:8002/flights; they use the same sessions, settings and logs as
the direct Pi URLs.

The camera's `/raw.jpg` and `/raw.mjpg` endpoints provide clean images/video to
the studio. `/snapshot.jpg` and `/stream.mjpg` include detection overlays. These
are media endpoints rather than additional applications.

See [AUTONOMOUS.md](AUTONOMOUS.md) for the control algorithm and loss behavior,
and [TRAINING.md](TRAINING.md) for the data/training/compiler workflow.
# Current detector and network access (2026-11-10)

The camera page records full-view 1080p50. The autonomous page now shows whether
native 640-pixel inference is searching or tracking, and the processed region.
Its amber rectangle is a detector crop, not a camera zoom. Detection is about
50 results/s; the deployed development preview is capped at 15 FPS. The controller
feedback target is saved at 50 Hz. Acquisition scans eight tiles, so detection
FPS does not mean complete full-view searches per second.

The laptop training page defaults to native 640 crops plus original full frames.
YOLOv8s is recommended; YOLOv8m is available for comparison. Native tile inspection
and full-frame inspection are separate choices. Restart an existing training
studio process to load the updated backend, then refresh its page.

During this update, Windows reported the Ethernet address 192.168.137.1 as
`Duplicate`, and direct Pi IPv4 HTTP/SSH timed out. The Pi was reached over its
saved IPv6 SSH hostname, `afc-rpi.local`. Direct browser access to the hostname
also timed out; the web services currently bind IPv4. No network settings were
changed. Until IPv4 connectivity is restored, run this in a terminal and leave
it open (it uses the existing trusted SSH host key):

```powershell
ssh -6 -N -L 8011:127.0.0.1:8001 -L 8012:127.0.0.1:8000 user@afc-rpi.local
```

Then use http://localhost:8011/autonomous for the controller and
http://localhost:8012/ for the camera. In another terminal start the updated
training studio against the forwarded camera:

```powershell
.\start-training.ps1 -Camera http://localhost:8012
```

The training page remains http://localhost:8010. The forwarded controller also
offers `/joystick`, `/calibration` and `/flights`. This is an access workaround;
GPIO/headless operation on the Pi does not require these pages or this tunnel.

