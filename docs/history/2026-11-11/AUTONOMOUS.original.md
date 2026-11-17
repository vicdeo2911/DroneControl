# Autonomous launch and horizontal tracking

## Current correction: full-frame 640 for existing weights (2026-11-10)

The deployed detector now uses **full-frame 640x640 YOLOv8s**, a **0.55 detector
confidence cutoff**, and **0.55 autonomous confidence** with the existing three
consecutive-frame confirmation. Capture and recording remain full-view 1080p50;
feedback remains 50 Hz. Full-frame detection measured roughly 34-36 FPS (10-second benchmark median 33.6). Native crop mode is experimental and is not the active flight
configuration. No new model was trained during this correction.

A real-plane regression image exposed a limitation hidden by the previous aggregate
validation: full640 float inference found the plane at .722 confidence, but native
crops missed it and produced a background detection. Full1920 also missed it.
Actual Hailo inference on that same saved image found the correct plane at .586;
its other full-frame prediction scored .264. The new .55 cutoff accepts that
known plane and rejects the background prediction. This is a reproducible regression
check, not proof that all poses, distances or false detections are solved.

Do not deploy existing full-frame-trained weights in native crop mode merely
because their HEF input is 640. Train and validate the intended crop/scale
workflow, including near/large planes and hard background negatives, first.
The training studio now defaults to full-frame preparation and inspection;
native preparation remains an explicitly labelled retraining experiment.
New distant 1080p footage is still needed to validate the high-detail approach.

Experimental crop acquisition now requires three spatially associated hits at
confidence >= .6, rejects large jumps, suppresses unconfirmed plane observations,
and forces a search after one second of crop lock. This limits distraction by a
single false location but cannot replace crop-specific training. Missing observations
still reach the controller as missing; no predicted plane boxes are invented.
Live classroom checks still showed some background false detections after the rollback.
Representative existing training images all show the atrium, mostly with small planes.
The current classroom, very close wing views and partly clipped planes need additional
reviewed examples and background negatives. This correction is not a flight-readiness
claim. The earlier native-crop recommendation and measurements below are historical.


## Current detector and feedback configuration (2026-11-10)

The Pi captures the full view at 1920x1080@50. The active detector is the original
640-input YOLOv8s HEF with adaptive native-pixel crops. Acquisition/loss scans eight
overlapping tiles; tracking recentres a 640 crop on a unique plane. It resumes
search after two misses or a 250 ms gap. Coordinates returned to this controller
remain full-camera coordinates. No predicted boxes are substituted on a miss.
Multiple planes outside the crop cannot be ruled out; this is a single-aircraft
setup. Existing confidence/association and consecutive confirmation still apply.

Measured throughput is about 50 crop results/s; this is not 50 full-view searches/s.
Current saved feedback target is 50 Hz. A dry run measured 49.7 control Hz and
47.7 fresh observations/s, with the output state unchanged. The default for older
settings files remains 10 Hz; adjust Feedback rate in this page or the existing
settings API. Controller gains and actuator calibration have not been changed.
Physical flight validation at this feedback rate is pending.

Preview encoding now runs independently and is capped at 15 FPS in the deployed
development service; the camera metrics display search/tracking and crop geometry.
The amber rectangle marks the processed region. A miss still centres roll and
uses fallback throttle according to the existing state machine; the configured
loss timeout still stops the session. Background search does not bypass that timeout.
GPIO remains disabled with no pin selected. The websites remain development tools.
See TRAINING.md for crop preparation and the preserved full-frame alternative.
Historical rate tables below describe the earlier implementations.


The controller is deployed and its pages have been verified on the Pi. It has
passed fake-DAC and browser tests, but has not been flight-tested. Physical
tuning and flight validation remain pending. Defaults are starting values, not measured flight
parameters. Only throttle and roll are available; there is no attitude,
altitude, GPS, landing, or return-to-home controller.

## Install and access

To redeploy an update, stop aircraft control and camera recording, then run:

```powershell
python deploy_autonomous.py
```

This updates the remote/controller, navigation and camera status metadata. It
backs up code and rolls back if services fail their readiness checks. Calibration,
limits, persisted zero-output mode, logs, recordings and models are preserved.
It does not arm the aircraft or activate a trained model.

Open http://192.168.137.190:8001/autonomous or
http://localhost:8002/autonomous through the existing joystick tunnel.
There is an Autonomous link in the remote navigation.

The website is a development interface. Production launch can be triggered by
a GPIO command, with all feedback, state transitions and detection-loss handling
running on the Pi. No ROS installation, browser, laptop or external network
connection is required for that mode. The camera service must still run; it
supplies detections over local loopback HTTP independently of any open page.

Start requires a fresh camera service with the updated metadata and a model whose
labels include `plane`. The trained plane/target Hailo model is active. The fallback default COCO model
has no `plane` label.
Use the trained two-class detector and its labels, following TRAINING.md's
validation/activation procedure. Image overlap does not establish depth or a
physical interception; this controller steers toward a fixed horizontal image
position, not toward the `target` detection.

## GPIO-triggered operation

The launch pin is **not assigned**. GPIO launch is disabled until configured.
`gpio-launch.example.json` contains the defaults:

```json
{
  "enabled": false,
  "pin": null,
  "active_high": true,
  "debounce_seconds": 0.05,
  "mode": "behind",
  "dry_run": true,
  "restore_outputs": false
}
```

When the pin is decided, copy the example to `gpio-launch.json` on the Pi, set
`pin` to the chosen **BCM GPIO number**, set `enabled` to true, and restart the
control service. GPIO 2 and 3 are rejected because this project uses them for
the DAC's I2C bus. No pin is opened while disabled. Pin numbering and input
polarity follow [GPIO Zero](https://gpiozero.readthedocs.io/en/latest/_modules/gpiozero/input_devices.html).
The input expects appropriate 3.3 V logic and a shared ground with the sender.

With the default polarity, a stable low level makes the input ready; a subsequent
stable rising edge starts one autonomous session. The debounce interval defaults
to 50 ms and the input is sampled every 10 ms; use a pulse of at least 100 ms for
initial testing. `active_high: false` supports an active-low command instead.

- A signal already active at service startup never launches. First return it
  inactive, then send a new active edge.
- Holding the signal active never retriggers. A rejected command, or an edge
  received while another controller is active, is discarded rather than queued.
- A new launch requires another inactive-to-active transition after the previous
  session ends. Service restart never resumes a previous flight.
- This is a **start command**, not a continuously held flight-enable signal.
  Returning it inactive after a pulse does not stop the flight.
- GPIO starts use the same camera/model preflight, calibration, limits, arming
  sweep, countdown, launch-throttle ramp and timed launch sequence as web starts.
- GPIO sessions need no browser heartbeat. Camera/data failures, detection loss,
  DAC errors, maximum session duration, explicit Stop or service shutdown still
  stop the session. Browser-started development sessions retain their heartbeat
  watchdog and stop-on-page-leave behavior.

Keep `dry_run: true` while testing the command input. Set it to false only when
commissioning live operation. `restore_outputs: false` preserves the existing
requirement to explicitly authorize restoring from saved zero-output mode. A
live GPIO start in zero mode is rejected unless `restore_outputs` has been set
to true in this configuration. It does not restore outputs on boot; it applies
only when a new valid GPIO start command is accepted. These values remain
disabled/unassigned in the deployed setup.

The existing service can retain the development web pages alongside GPIO input.
For a final deployment, run the same controller with `--no-web` to omit the web
server. Use a systemd override for `pi-remote` rather than starting a second DAC
controller:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 -u /home/user/pi-vision/remote_control.py --no-web
```

Both modes use an exclusive process lock, so they cannot own the DAC together.
Controller and GPIO configuration load from the same saved files. Flight state
transitions and the launch cue are logged to the service journal; live command
logs include `autonomous_trigger: gpio` in their settings metadata. In development
mode, `/api/status` also reports `gpio_launch`, and autonomous status reports
`source: gpio` or `source: web`.

## Develop and tune with the website

1. Keep **Dry run** checked. Save parameters while stopped. Inspect the resulting
   commands with recorded/bench motion and verify the roll direction. Positive
   logical roll must cause the image motion assumed by the selected direction.
   Camera placement and flight direction can change that relationship.
2. Choose **Behind camera** or **Plane in starting view**. In-view mode requires
   consecutive detections before countdown. Behind-camera mode allows a timed
   blind launch and a bounded acquisition period.
3. For live operation, uncheck Dry run and explicitly confirm the existing
   idle/full/idle arming sweep. Restoring from zero mode requires a separate
   confirmation. Hold the aircraft securely for the sweep.
4. Wait through the idle countdown and launch-throttle ramp. Throw only at
   **Launch now**, after the requested launch throttle has been reached. There
   is no release sensor: the subsequent launch phase is timed.
5. Once acquired, the controller applies cruise throttle and horizontal PD
   steering. Keep the page visible, focused and connected. Stop, Space/Escape,
   leaving the page, or a missing heartbeat ends control. On mobile, Stop stays
   fixed at the bottom of the screen. Any remote's Stop also cancels autonomy.

Parameters persist in `autonomous-settings.json`. Settings cannot change during
active control. Launch/cruise/lost throttle and maximum roll use **absolute
calibrated percentages** and must fit the existing remote limits. They are not
percentages of those limits. Normal throttle and roll changes have slew limits.
The session limit includes arming, countdown and launch.

## Live preview and feedback rate

The autonomous page includes the annotated camera preview and three different
rate measurements:

- **Camera / detection target FPS** requests the camera rate. The sensor and
  actual detection FPS are shown separately; processing can skip sensor frames.
  Apply this while stopped. This setting is shared with the camera application
  and is unavailable while a recording is active. Camera FPS and capture resolution are saved in camera-settings.json and
  survive service restarts.
- **Feedback rate (Hz)** in Controller parameters requests 2–60 controller
  updates per second. Save parameters to persist it. Existing configurations
  retain the default 10 Hz. The controller subtracts request/processing time from
  its scheduling interval. Actual controller Hz and fresh observations consumed
  per second appear alongside the commands during a session.
- **Browser preview FPS** measures frames decoded by this browser. It follows
  the camera target without the old fixed 15 fps preview cap. Only one snapshot
  request is outstanding; slow connections skip frames instead of accumulating
  delayed video. Preview traffic is separate from feedback processing.

The preview is proxied through the control service's fixed loopback camera URL.
It works through the existing laptop SSH tunnel as well as directly on the Pi,
without requiring browser access to an additional camera address.

Higher detection FPS helps control when it supplies fresh observations sooner.
A controller running at 60 Hz with a 30 fps detector still has at most about 30
new observations per second. Re-reading a frame never advances detection
confirmation or re-runs the position filter. Faster updates can smooth command
ramps, but cannot manufacture missing vision measurements. Gains and smoothing
still need physical validation at the chosen rate.

Measured on the current model at 960 × 540, with a desktop browser on the LAN:

| Requested camera FPS | Actual detections/s | Browser preview FPS | NPU utilization |
| --- | --- | --- | --- |
| 15 | 15.0 | 13.9 | 17.5% |
| 30 | 30.0 | 29.4 | 35.0% |
| 45 | 22.5 | 21.5 | 26.1% |
| 60 | 30.0 | 30.0 | 35.1% |

These are short measurements, not guaranteed rates under every scene, lighting,
recording or network load. Start testing with **30 camera FPS and 30 feedback Hz**.
The serial capture, preprocessing, inference and JPEG pipeline can skip frames
at higher sensor rates; a larger requested number is not always faster. The NPU
percentage is runtime utilization, not electrical power or a direct measure of
the model's share of advertised TOPS. Inference took about 14 ms in these runs.

In dry runs at 30 camera FPS, feedback targets of 10, 30 and 60 Hz were measured
against the real Pi. See `test-artifacts/controller-rate-benchmark.json` and
`test-artifacts/camera-rate-benchmark.json` for results. No aircraft was armed.

Frame freshness now uses the sensor's exposure-start timestamp in
[libcamera metadata](https://github.com/raspberrypi/libcamera/blob/main/src/libcamera/control_ids_core.yaml),
including exposure, capture and processing time. Picamera2's completed-frame
cache is disabled for this control workload, following its
[queue configuration](https://github.com/raspberrypi/picamera2/blob/main/CHANGELOG.md).
Preview frame age is approximate and does not measure the screen's physical
display delay.

## Control law

For each fresh frame, accept exactly one `plane` detection above the confidence
threshold. Reject ambiguous detections and center jumps outside the normalized
2D association gate. Require consecutive accepted frames for acquisition and
reacquisition. This is a simple association gate, not an identity tracker.

Let `x` be the plane center divided by image width. Exponential smoothing gives
`xf = xf + alpha * (x - xf)`. Estimate horizontal velocity from successive
filtered positions and elapsed time. Then:

```text
error = sign(setpoint_x - xf) * max(abs(setpoint_x - xf) - deadband, 0)
roll = clamp(direction * (Kp * error - Kd * velocity), -max_roll, +max_roll)
```

This image-space PD controller reduces horizontal error and damps lateral
motion. There is no integral term. Throttle follows configured launch/cruise
values; image height does not provide a reliable altitude estimate. The controller
does not measure bank angle, speed, distance or release. Gains, roll polarity,
throttle and timing require validation on the actual aircraft and camera setup.

## Detection loss and failures

- During tracking, the first missing, low-confidence, ambiguous or rejected
  detection immediately requests centered roll and Lost throttle (default 0%).
  Ordinary slew limits do not delay this fallback.
- Within the loss window (default 0.7 s), consecutive detections can restore
  tracking. Isolated intermittent hits cannot extend this window indefinitely.
- On timeout, request calibrated throttle idle and roll center, invalidate the
  session, and require a new start. No blind circling or predicted steering.
- The timed launch/acquisition phases permit the planned initial lack of view.
  If the plane never enters view within Acquisition timeout, stop at idle/center.
- Camera HTTP failure, invalid/stale frames, frozen frame counters, DAC failure,
  the session time limit, or the 1.2 s browser heartbeat timeout for a web-started
  session also stops control. GPIO-started sessions do not require that heartbeat.
  Camera freshness includes capture/inference time, not just HTTP response time.

Centered roll is not measured wings-level flight. Idle can cause descent or
stall. A fixed ground camera and these two control channels cannot provide a
guaranteed recovery maneuver. Persisted zero-output mode is preserved: with the
existing inverted wiring, 0 V is **not** the idle voltage.

Live sessions use the existing flight recorder with source `autonomous`, a
parameter snapshot, state/reason, and requested command samples. Dry runs do not
arm, write flight commands or create a flight recording. A manual Stop always
retains its existing neutral-output behavior.

## Verification

```powershell
.\.venv-training\Scripts\python.exe -m unittest discover -s tests
.\.venv-training\Scripts\python.exe tests/check_autonomous_browser.py
```

Tests use a fake DAC and synthetic observations and never operate Pi hardware.


## Current high-resolution configuration

The active model now uses 1920x1920 input, with 1920x1080 capture at a target
50 fps. Recording is independent of inference: the sustained live test measured
49.6 recorded fps with zero recording-queue drops, while the complete detector
ran about 7-8 fps. The previous 640-model rate table above is historical and does
not describe the current 1920-model throughput. Feedback Hz cannot increase the
number of fresh detections beyond that detector rate.

Hailo runs in its own process so native inference calls cannot stall camera
capture through Python's GIL. Camera geometry changes during an active autonomous
session stop the session; choose recording resolution before starting flight.
There is still no physical flight validation of this controller/model setup.
