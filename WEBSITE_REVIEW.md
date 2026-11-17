# Website review - 2026-11-11

Reviewed the camera page, manual controls, autonomous controls, calibration, joystick, flight logs and laptop training studio.

## Repairs

| Issue | Repair |
|---|---|
| A rejected camera-settings response was accessed as a successful result, hiding its actual error behind a JavaScript exception | Check HTTP success before reading settings; regression checks preserve the service's error message |
| Browser preview scheduling followed capture FPS even when JPEG output had a separate lower limit | Shared preview helper now uses the camera's preview limit, bounded by capture rate; capture/detection/controller settings are unchanged |
| Autonomous page lacked the navigation available on the other control pages | Added the same five-link menu and current-page indicator |
| Training defaults selected full frames while introductory text recommended native crops | Instructions now match full-frame defaults and describe crops as an optional experiment |
| Duplicate clip-length label and a 1,000-frame description beside a 10,000-frame extraction limit | Removed the duplicate and corrected the description |
| Literal question marks appeared in model-output labels, status separators and possessives | Repaired the affected text |
| Laptop studio was stopped | Started it using the existing `start-training.ps1` command |
| Camera test fixture could patch a different imported module instance and use project recording/settings paths | Preload the shared module and assert that both paths point inside the temporary test directory |

## Verification

- Full unit suite: 177 tests completed, with the Pi-only symlink test skipped on Windows (previously verified on the Pi).
- Browser regressions: camera, general training, HEF inspection/live preview, autonomous dry run, joystick/calibration, flight logs and video extraction. Control interactions use fake hardware; training/extraction use temporary data.
- Added checks for camera API errors, preview rate limits, autonomous navigation and corrected training-page instructions.
- Live HTTP checks: all seven pages and their JS/CSS assets return 200, without duplicate element IDs. The active HEF live-status endpoint responds successfully.
- The initial automated live-page browser audit timed out, including through its read-only proxy. Its failed prototype was removed. Successful browser interaction/layout checks use local test servers; deployed live pages were separately checked over HTTP. Do not treat the failed audit report as a successful visual check of the live services.

Evidence is under `test-artifacts/`: `website-http-review.json`, `website-fixes-before.json`, `website-fixes-after.json`, plus browser screenshots. Camera and autonomous mobile screenshots were visually inspected.

## Installed state

The camera source payload was installed with `deploy.py`'s guarded camera workflow. This briefly restarted only `pi-vision`; it retained the same model SHA, camera settings and installed service override. Autonomous HTML/JS were backed up and replaced without restarting the controller. No control enable, calibration, model activation, training or recording command was sent to real hardware during inspection.

Before/after verification confirmed unchanged persisted JSON settings, calibration, output-zero state, service units, active model SHA and controller PID. The active detector remains the e6685d1b model at 640 input with 1080p50 capture. Camera backup is under `maintenance/deployments/camera-20261110-164251-612255/backup`; autonomous assets are backed up under `maintenance/web-assets/20261110-164256` (IDs use the execution host's clock).

Refresh open pages to load the repaired assets. Detector accuracy and physical flight behavior were not evaluated by these UI tests.
