# Autonomous controls

Open `http://<pi-address>:8001/autonomous`. The controller runs separately from the camera service.

## Launch and tune

- **Starting view / visible:** requires a confirmed plane before countdown; losing it before launch cancels the sequence.
- **Behind camera:** allows the plane to start out of view, then attempts acquisition after the launch interval.
- **Dry run:** computes the sequence without enabling hardware outputs.

Tune gains, filtering, confidence, confirmations, throttle and limits on the page. Settings persist in `autonomous-settings.json`; calibration and manual control limits are separate.

Steering uses horizontal image position and velocity to compute bounded PD roll commands. It does not control attitude or altitude. Camera FPS, detection FPS, preview FPS and control-loop Hz measure different things; polling a repeated frame does not create a new observation.

## Stop behavior

Lost detection centers roll and requests fallback throttle. A confirmed target can resume tracking within the allowed loss interval. Loss/acquisition timeouts, stale camera data, stopped frame advancement, changed capture geometry or maximum session duration stop the session.

Stopping requests calibrated idle throttle and centered roll. It cannot guarantee recovery of an aircraft whose attitude is unknown. Browser-started sessions require page heartbeats; GPIO-started sessions enforce the same camera checks without browser heartbeats.

## GPIO and headless mode

Copy `config/gpio-launch.example.json` to root `gpio-launch.json`, select a BCM pin and review the options before enabling it. Pins 2 and 3 are reserved for I2C. The trigger requires a stable inactive level followed by a debounced active edge; a held signal at startup does not launch.

For operation without a web server, configure the controller service to use:

```bash
python3 -m pi_vision.control.remote_control --no-web
```

Run one controller process. Do not start a second one beside the service.

**Electrical zero is different from calibrated idle.** On inverted throttle wiring, 0 V means full throttle. Verify polarity, calibration and stop behavior with propulsion disconnected before enabling outputs. Deployment and UI checks should leave calibration and `remote-power.json` alone.
