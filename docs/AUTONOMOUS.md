# Autonomous controls

Open `http://<pi-address>:8001/autonomous`. The controller runs separately from the camera service.

## Launch and tune

- **Starting view / visible:** requires a confirmed plane before countdown; losing it before launch cancels the sequence.
- **Behind camera:** allows the plane to start out of view, then attempts acquisition after the launch interval.

Live autonomous starts use the GPIO12 hardware PWM launcher. Save distinct Lock and Release positions on Calibration first. On Autonomous, press **Reload launcher** to command the saved Lock position. Reload is available while aircraft controls are stopped and requests calibrated idle throttle before moving the latch. It indicates a commanded position, so confirm the latch mechanically.

Start the flight after reloading. The existing throttle arming sweep runs while the launcher stays locked, then the selected camera-start condition and countdown apply. Throttle ramps to **Launch throttle**, then holds that command for **Launcher release delay** (default 1 second). The controller then commands the saved Release pulse once and begins the launch phase. Release uses a PWM update without blocking the flight loop. If release fails, the session stops and requests idle and centre. Stopping never automatically locks the launcher. Press Reload for the next launch. A controller restart or any launcher calibration action also requires Reload before another live start. GPIO-triggered live starts use the same launcher checks. Dry runs leave the launcher untouched.

Drag a rectangle on the preview and click **Save target** to save its center as the horizontal and vertical image setpoints. Start the sequence separately with **Start autonomous flight**.

The **Stationary detection timeout** automatically filters static plane detections anywhere in view. The controller requires consistent movement of a box's center and, after release, expires that evidence after 0.6 seconds by default. A plane flying almost directly toward the camera can move too little in the image to qualify. For **Plane in starting view**, move the plane enough for the camera to confirm it before the countdown. Once confirmed, it can remain still through the countdown while the camera keeps seeing it. While controls are stopped, use **Disable noise filter** or **Enable noise filter** under the live preview. The button saves the change immediately. Re-enabling restores the last timeout remembered by that browser or uses the 0.6-second default.

During browser-started flights, telemetry reports the requested throttle and roll plus any estimated horizontal position during a brief detection gap. The estimate projects the last confirmed observation at constant velocity for up to 0.25 seconds, capped at a shift of 6% of image width. The plane detection total counts qualifying boxes after release, with the stationary filter applied. The logged commands and state transitions describe the controller's actual behavior.

Tune gains, filtering, confidence, confirmations, throttle and limits on the page. Settings persist in `autonomous-settings.json`; calibration and manual control limits are separate.

Steering uses horizontal image position and velocity to compute bounded PD roll commands. In **Throttle tracking**, vertical Kp and Kd adjust cruise throttle from the filtered vertical image position and speed. The vertical direction of +1 adds throttle for a plane below the target. The vertical correction is bounded independently of the turn boost. Turn boost scales with the square of the rate-limited roll command and reaches the configured boost at maximum roll. The existing throttle ramp and remote limit apply to the combined command. Vertical Kp, Kd, and turn boost default to zero. Vertical position is in the camera image and depends on distance from the camera. Camera FPS, detection FPS, preview FPS and control-loop Hz measure different things. The controller only updates its observations from fresh frames.

## Stop behavior

**Post-release throttle hold** keeps the launch throttle for an adjustable interval starting at launcher release. The default is 0.5 seconds. Set it to 0 to disable the hold. Roll steering starts as usual when tracking begins, and Stop and automatic session stops take effect immediately.

Lost detection centers roll and requests fallback throttle once the post-release hold ends. A confirmed target can resume tracking within the allowed loss interval. Loss/acquisition timeouts, stale camera data, stopped frame advancement, changed capture geometry or maximum session duration stop the session.

Stopping requests calibrated idle throttle and centered roll. It cannot guarantee recovery of an aircraft whose attitude is unknown. Browser-started sessions require page heartbeats; GPIO-started sessions enforce the same camera checks without browser heartbeats.

## GPIO and headless mode

Copy `config/gpio-launch.example.json` to root `gpio-launch.json`, select a BCM pin and review the options before enabling it. Pins 2 and 3 are reserved for I2C. The trigger requires a stable inactive level followed by a debounced active edge; a held signal at startup does not launch.

For operation without a web server, configure the controller service to use:

```bash
python3 -m pi_vision.control.remote_control --no-web
```

Run one controller process. Do not start a second one beside the service.

**Electrical zero is different from calibrated idle.** On inverted throttle wiring, 0 V means full throttle. Verify polarity, calibration and stop behavior with propulsion disconnected before enabling outputs. Deployment and UI checks should leave calibration and `remote-power.json` alone.
