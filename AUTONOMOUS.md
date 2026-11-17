# Autonomous controller

The development page is http://192.168.137.190:8001/autonomous. It belongs to `remote_control.py`, with `templates/autonomous.html` and `templates/autonomous.js`. The algorithm is in `autonomous.py`; camera inference belongs to the separate `app.py` service on port 8000.

The intended production trigger is a **GPIO command**, not ROS. The pin is undecided, so GPIO remains disabled with `pin: null`. Browsers are development tools; the controller supports `--no-web` operation using the same state machine.

## Launch modes

- **Starting view / visible:** require a confirmed plane in view before countdown. Losing the plane before launch cancels the sequence.
- **Behind camera:** allow the initial plane to be out of view. After countdown, spool to launch throttle and show the launch cue, hold launch throttle for the configured interval, then attempt acquisition within its timeout.

Dry run computes the sequence without enabling hardware outputs. Hardware operation requires valid calibration, remote command limits and explicit controller start. The state progression includes arming, waiting-view when applicable, countdown, spooling, launch, acquisition, tracking, lost and stopped. Maximum session duration also limits the run.

## Control algorithm and feedback

The controller chooses a confident `plane` detection, associates its center across frames and requires consecutive confirmations. It filters normalized horizontal position, estimates horizontal velocity and computes:

```text
error = setpoint_x - filtered_plane_x
error = sign(error) * max(0, abs(error) - deadband)
roll = roll_direction * (kp * error - kd * horizontal_velocity)
```

Roll is clamped to `max_roll`; regular throttle/roll changes have slew limits. Throttle follows launch/cruise/fallback settings. This is bounded **image-space PD steering**, not an attitude, altitude or 3D flight controller. A fixed camera's image position does not supply depth or prove a physical target strike.

Tune feedback Hz, gains, filtering, confirmation, confidence, association gate, throttle and limits on the autonomous page. Settings persist in `autonomous-settings.json`. Calibration and remote limits remain separate.

The configured feedback range is 2-60 Hz; the current saved value is 50 Hz. More distinct fresh detections can improve responsiveness and velocity estimation. Polling a repeated frame faster does not create new information; each camera frame is observed once. Camera capture FPS, detection FPS, JPEG preview FPS and control-loop Hz are different metrics.

## Losing detection

When tracking loses confirmation, it enters `lost`, centers roll and immediately requests configured fallback throttle. It can resume after a newly confirmed target within the permitted loss interval. It stops if the last detection exceeds `loss_timeout` or the lost phase reaches its timeout; restart is then required. The current saved loss timeout is 0.7 seconds and fallback throttle is 0 percent.

Invalid/stale camera data, stopped frame advancement or changed capture geometry stop the autonomous session. The current saved frame timeout is 0.5 seconds. Failure to acquire after launch also stops it. The controller does not search with an uncontrolled turn. Stopping requests calibrated idle throttle and centered roll; this cannot guarantee recovery of an aircraft whose attitude is unknown.

Browser-started sessions require page heartbeats. GPIO-started sessions do not require browser heartbeats, but still enforce camera freshness, session duration and other control checks.

## GPIO and persisted power

`launch_input.py` reads `gpio-launch.json`; use `gpio-launch.example.json` as a configuration reference. Assign a BCM pin deliberately before enabling it. I2C pins 2 and 3 are rejected. The trigger must first see a stable inactive level and then a debounced active edge. A held-high signal at startup is not a launch command, and a trigger while controls are active is ignored.

The current configuration is disabled, active-high, debounce 0.05 seconds, behind-camera mode, dry run enabled and output restoration disabled. `remote_control.py --no-web` is an alternative service mode, not a second process to run alongside `pi-remote.service`.

**On the current inverted throttle wiring, 0 V means full throttle.** Persisted `outputs_zero` is a distinct electrical state from calibrated idle. Do not restore outputs, alter calibration or enable hardware merely to test a website or deploy source. Source cleanup preserves `remote-power.json` and all runtime calibration/settings.

See [DEPLOYMENT.md](DEPLOYMENT.md) for source updates and [ARCHITECTURE.md](ARCHITECTURE.md) for control, logging and GPIO file ownership.
