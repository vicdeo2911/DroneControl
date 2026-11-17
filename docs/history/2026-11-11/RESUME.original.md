# Latest update: continuous live Pi HEF testing

User requested actual continuous live testing, not repeated one-frame tests.
Implemented Start/Stop live Pi preview for the selected already-active HEF.
Separate Test one frame on Pi retained; inactive candidate still requires Full
frame test + explicit activation before live viewing. Viewing itself is read-only:
no inspect/activation POSTs, SSH, camera pause/restart or aircraft commands.

Pi app hashes HEF before/after loading and publishes model_sha256 plus stream
X-Model-SHA256 header. LatestFrame has process instance UUID, preview sequence and
preview age; instance header also available. Studio GET /api/hailo/live validates
selected completed local HEF digest against loaded model SHA and fresh enabled
preview, returns actual Pi metrics. GET /camera/hailo.mjpg proxies annotated Pi
/stream.mjpg and validates response hash to close model-switch races. Raw stream
proxy refactored to shared internal helper; existing clean stream unchanged.

Client uses MJPEG img and500ms metrics polling, not per-frame inference. Disables
model selection and hides inspection-only view/size/confidence during live; uses
Pi actual settings in metrics. Stop/Escape/hidden page closes stream and cancels
polling. Generation checks discard stale async replies. Active-model/local-file
changes or stale/disconnected preview stop live with a clear message. Same-model
service instance changes can reconnect. Old backend detected by hef_live capability.

QA:165unit tests passed (active results, wrong hashes, stale/off preview, changed
local artifact, stream model-switch race). HEF browser QA now also checks live
start/stop, multiple polls without new inspection/activation calls, and automatic
stop after model change; general training browser QA passed. Actual browser/Pi
live test passed: frames advanced, same camera instance/model, no POST requests,
detection~36.5FPS, capture~50FPS, preview limit15. Evidence hef-live-hardware.json
and hef-live-pi-browser.png. No live camera restart/activation during QA. Source
backup .autonomous-backup-20261110-155514-189511.

User has since completed/restarted training and activated the new run themselves:
actual active HEF at live QA matched run20261110-220047-2a98de/compiled/plane-target-h8.hef,
SHA e6685d1ba93ea7f394b790a10b99c88efc467c519e405e7848e618b9f806fce7. Do not restore
old0744 model based on historical notes. Current source deployment preserved model.

Main studio8010 readback: activefalse/stateready/nameNone/hef_activationtrue,
hef_live absent (old loaded backend). Verified listenerPID24084 parent12624
workspacevenv, no training active. Attempted guarded restart using ORIGINAL
Start-Process mechanism (hidden window) after checking idle + exact process tree;
automatic tool review rejected whole exec command BEFORE execution, reason
`blocked by policy`. Old studio remains running. Do NOT bypass using another
launcher/process method. User must stop oldstudio/run start-training.ps1 and refresh
for live backend. Say explicit review reason if explaining inability to restart.
All implementation and Pi deployment/tests complete; this final manual restart is
outstanding. No permission question required; do not leave impression the main8010
server already supports hef_live. Pi camera page8000 already provides active stream.

# Latest update: separate tested-HEF activation (2026-11-10)

User answered the earlier optional question: wants HEF testing AND a separate
activation action. Implemented Activate on Pi, hidden for PT, disabled until a
successful Full frame test of exact SHA bytes. Native-only tests do not unlock it.
User confirmation names model/input size and full-frame mode, preserving the Pi
confidence threshold. Selection/testing still does not activate. Backend insists
confirmed bool true + current artifact SHA + server-owned full-test proof; changed
file or studio restart requires retest. Test proof dict is per studio instance.

verify_hailo adds actual model_sha256 to saved result. inspect_on_pi returns the
remote jobID, which is retained as proof. run_on_pi shares authenticated staging,
execution, cleanup for inspect and activation. New activate_hailo.py checks proof
result hash/full-mode/input and uploaded bytes, takes camera-operation exclusive
lock, preflights stopped controls/autonomy/recording, and copies HEF to immutable
models/studio-<sha>.hef with labels. Reads live app.py arguments from MainPID's
/proc cmdline, replaces only model/labels/mode(full), preserves other flags, safely
quotes systemd literals. Saves previous override in job dir; atomic update,
daemon-reload/reset-failed/restart, checks active name/shape/ready. Rollback restores
exact previous override (or absence), clears restart failures and waits old model.
No DAC/arming calls. Compilation and current laptop training not modified.

QA:160 unit tests passed after the final response-preparation consistency check;
new tests cover missing proof/confirmation, native-only proof, modified file,
argument preservation, exact rollback and absent override. HEF browser QA passed
including disabled-before-test, cancel/confirm and PT-hidden button. Actual Pi
activation was tested only with a SHA-identical copy of the already-active640HEF.
The original service override was then restored byte-for-byte, preserving original
model and output state. NO newly-trained candidate activated. Evidence:
test-artifacts/hef-activation-hardware.json. The SHA-copy cache remains as a duplicate
of original640 weights, not a newly-trained model. QA restore backup on Pi:
.activation-qa-original.conf. Source backup .autonomous-backup-20261110-145922-308980.

Main studio8010 was still training job20261110-220047-2a98de last checked. Do not
restart it mid training/compilation. When finished user restarts start-training.ps1
and refreshes for new backend. Existing frontend explains old-backend limitation.
Production remains original640HEF full-frame/.55, capture1080p50, feedback50; latest
outputszeroTRUE, GPIOdisabled/pinnull. Preserve runtime state. Model detection
weakness across close/classroom/distant scenes still requires new data/validation.

# Latest update: compiled HEF inspection selector (2026-11-10)

User asked for selecting compiled Hailo in inspection. Implemented test-only model
format selector (StandardYOLO.pt / CompiledHailo.hef), fixed HEF input, selected-image
and one-camera-frame test buttons. No activation button/automatic activation.
Asked optional preference whether activation should also be offered; no response
received while implementing the test-only scope. Future reply may extend scope.

hailo_inspection.py discovers completed HEFs, metadata sizes and SHA-deduplicates
legacy/candidate copies. Active studio run excluded. Retains /api/training weights
for backwards compatibility, adds compiled metadata. /api/predict accepts only
known local artifacts. PT stays laptop inference; HEF tests use authenticated SSH,
Windows Vault without interactive password prompts and trusted saved host keys.
requirements-training includes requirements-ssh; installed these into trainingvenv
without changing Torch or interrupting current training.

inspect_hailo.py is Pi maintenance manager: exclusive .camera-operation.lock,
checks stopped controls/autonomy/recording and controller guard capability, stops
pi-vision, invokes verify_hailo as user via runuser, restarts/waits camera in finally.
Never changes service/model override or DAC outputs. Signal/timeout/failure paths
restore service. Large per-job uploaded model/image copies cleaned afterward;
JSON/worker logs retained under .hef-inspection/job on Pi. Friendly error messages.
verify_hailo now supports threshold/full/native view and reports actual compiled
shape, source dimensions, tiles, inference time and processing time; old CLI defaults
retained. Native padding now consistently top-left (no enlargement), matching
training export and PT tile inspection for undersized frames.

camera_operation.py provides POSIX shared kernel guard held atomically through
control enable, autonomous preflight/start and recording start. Root inspection
holds exclusive lock. Windows QA is a no-op guard. Root-owned lock is opened
read-only by Pi user, so no write permission required. Actual cross-process guard
verified on Pi. Controller status advertises camera_operation_guard true; manager
refuses old unguarded controllers. Shared manifests updated; source deploy requires
both camera and controller (deploy_autonomous.py now checks dry-run active too).

QA:151 unit tests passed after the final controller-capability guard change.
Both existing training browser checks and new HEF-selection browser checks passed.
Final source backup .autonomous-backup-20261110-144457-787573.
Final actualNPU success/friendly-invalid-HEF recovery report: hef-inspection-final.json.
Actual Flask/backend/Pi tests:640full14.06ms,1920full79.17ms,640native8tiles108.24ms;
roundtrip~6-7s each; original camera model/power state preserved. Deliberately invalid
HEF recovery also verified on hardware. Actual browser -> Flask -> Pi test passed,
640HEF14.03ms, input fixed640, screenshot hef-inspection-live-browser.png. Supporting
JSON test-artifacts/hef-inspection-hardware.json and hef-inspection-failure-recovery.json.

IMPORTANT: existing laptop studio at8010 is still running old loaded backend and
training job20261110-220047-2a98de (Hailo target). Do not stop/restart it while job
active. Let training AND compilation finish, then user restarts start-training.ps1
and refreshes. New frontend detects old backend and explains restart requirement.
Temporary QA servers used separate ephemeral ports/datasets; current training not
modified. No model trained/activated by this task. Latest observed outputszeroTRUE,
GPIOdisabled/pinnull; preserve current remote runtime state. Physicalflight validation
and old model's classroom false detections remain unresolved, as documented below.

# Latest diagnostic: whole-plane view still missed (2026-11-10)

User placed the entire plane inside the camera image. Verified clear complete
plane (large green/yellow wing) at lower-centre on table. Captured3 clean
snapshots and16 live status checks in test-artifacts/false-detections/full-plane-check/.
All16 live checks had no detections at full640/.55, throughput~35FPS.
Actual input is valid/full plane visible: do not keep asking user to reposition
or claim the rollback completely fixed detection. Float model comparison on all
three new images: full320 identifies the real plane at .571/.594/.585; full640
and full960 miss the real plane at .55 (some background detections remain).
This supports a substantial model scale/background generalization weakness.
320 float test does not establish an equivalent Hailo320 deployment or a safe
production scale-switching strategy. No additional live parameters changed.
Saved summary.json includes approximate manually inspected airframe region for
scratch IoU matching; not added to annotations.sqlite3 or used for training.
Next useful step is new reviewed close/medium/distant plane footage and hard
background negatives, with independent capture-session validation before activation.
Existing production remains full640/.55, capture1080p50, feedback50; flight control
and GPIO were not operated. The live issue remains unresolved pending model/data work.

# Latest correction: full640 restored after real-plane false detections (2026-11-10)

User reported crop mode stopped detecting the real plane and hallucinated other
objects. Reproduced on a clean actual camera image (plane lower-right). Saved
artifacts under test-artifacts/false-detections/ (private diagnostic images, not
automatically added to training data). Float full640 found real plane .722;
native640 crops missed it and hallucinated background .546; full1920 missed real
plane and hallucinated background .651. Actual Hailo on the same saved image:
full640 real plane .586 and background .264; native crops had no prediction >=.55.

Restored existing 640 HEF in --detection-mode full, threshold .55. Capture stays
1080p50, feedback50. Controller confidence saved .55 through stopped-controller
API; confirm_frames3 and all flight gains/calibration retained. Full-frame detection
roughly35-36 FPS with .06-.10s status age in early samples. Helper full640 action
selects the actual640 model (old helper full selected1920). Pipeline kept preview
separation. No retraining performed on old footage and no automatic data labels.

Experimental SpatialDetector now requires3 hits >=.6 with center association,
rejects large jumps, bounds each lock to1s before searching, and suppresses
unconfirmed plane observations before publication. Single candidate miss resumes
search. Tracking misses, gaps and geometry changes also reset confirmation.
Production mode remains full: these guards do not solve the scale-training mismatch.
Studio preparation and inspection defaultfull; native preparation labelled as a
retraining experiment. Model compilation options retained. Docs place this correction
above the earlier50FPS crop experiment. 140 unit tests and updated studio browser
checks passed. Final source backup .native-crop-backup-20261110-092659-867322. Live classroom still shows some false positives (including ceiling/wall features
above .55) and the real wing was partly clipped at bottom. Cannot claim all live
detection fixed. Asked user to hold whole plane in-frame for direct verification.
Representative old training images are the same atrium, mostly small-plane views;
additional reviewed new-background/scale data is required. Ten-second full640
benchmark median33.6 FPS, age80ms/p95 100ms. Direct laptop HTTP to root8001,
autonomous page, API and preview all returned200. Initial Edge navigation timed
out; direct/no-proxy DOMContentLoaded check subsequently PASSED on the actual
192.168.137.190:8001/autonomous page, preview1920, full640/threshold.55,
feedback/confidence field.55. Screenshot false-detections/live-full640-page.png. Saved-image actualNPU check passed;
new footage across distances still needed, not claiming all false detections fixed.

IPv4 access recovered: direct Python HTTP/SSH now work at192.168.137.190.
User reports page8001 working. Old helper's hardcodedIPv6 interface stopped working;
helper now prefers IPv4, falls back to saved hostname with host-key validation.
Important: user used the manual controls during this turn. Most recent observed
outputs_zero FALSE, idle3.3V/center1.65V, controls stopped. Do not restore the older
zero state. Removed helper's obsolete assertion that outputs must always be zero.
Bugfix and actualNPU regression test made no DAC calls; actualNPU check confirmed
output state unchanged. GPIO still disabled and no pin selected.

# Latest update: native-pixel detection at approximately 50 FPS (2026-11-10)

Implemented and deployed adaptive detection using the existing two-class 640x640
YOLOv8s HEF. Capture/recording stay full-view 1920x1080 at 50 FPS. Eight overlapping
640-pixel tiles cover acquisition/recovery; a unique plane recentres a native
640 crop for tracking. Two misses or a >250 ms observation gap resume search.
Every miss is published; no predicted/stale boxes are sent to control. Coordinates
are translated back to the original full-camera coordinate system. Existing
controller confirmation/association, loss fallback and timeout rules remain.
Crops cannot detect another aircraft outside their region: single-plane operation.

JPEG encoding is now a separate latest-only worker. Only the inference crop crosses
process IPC in adaptive mode. Preview/raw endpoints retain encoded-frame sequence
and exposure timestamp, independent of newer controller observations. Clean raw
JPEGs and videos remain full resolution. CLI --preview-fps 0 disables JPEG work
for headless use; deployed development preview is capped at 15 FPS. Existing full
mode remains default for manual CLI invocations; adaptive requires a 640 HEF.

Measured on this Pi (test-artifacts/native-crop-*.json):
- Previous 1920 detector: 8.4 FPS, frame age median 170 ms / p95 190 ms.
- 1920 with separated preview: 9.5 FPS, age median 150 ms / p95 170 ms.
- Adaptive 640: about 50 FPS, age median/p95 60 ms, Hailo inference ~14 ms.
  Searching processes one tile per result, not 50 complete full-view scans/s.
- Recording test: 1001 frames, actual 50.04 FPS, zero queue drops; detection
  median 49.8 FPS, frame-age p95 80 ms. Clip bd15a56967ae4a5592cb91f01d8f8d9b.
- Current feedback_hz saved at 50 through the stopped-controller settings API.
  Dry run measured 49.7 Hz, 47.7 fresh observations/s. Outputs unchanged; browser
  watchdog cancelled the dry run. No live arming or GPIO launch occurred.
- Existing float validation set (128 original 960x540 images, confidence .6,
  IoU .5): full640 matched 95/120 planes, tiled640 106/120. Small planes (<32px
  maximum extent): 59/84 vs 73/84. Unmatched predictions rose 1 to 6. This is
  exhaustive tiled float validation, not live moving-crop or new-1080p accuracy.

Training studio defaults now: YOLOv8s, 640 input, native crops + full frames.
Exports retain original images/annotations and put all derivatives in their source
session split. Boundary fragments are labelled or the crop is skipped. Background
crops are retained. YOLOv8m is selectable as an accuracy comparison. Standard
YOLO vs Hailo output remains available. Inspect native tiles (merged class-wise
NMS) or full frame. No retraining was started using the old footage; collect new
native 1080p distant-flight data before selecting a replacement checkpoint.

Active model: models/plane-target-20261109-022355-9ed07a-h8.hef (640 input).
The 1920 HEF is preserved. Service override includes --detection-mode adaptive,
--width 1920 --height 1080 --fps 50 --preview-fps 15. Pre-change override backup:
/home/user/pi-vision/.native-crop-model-before.conf. Source deployment backup:
/home/user/pi-vision/.native-crop-backup-20261110-084146-946824 (first deployment;
final backup .native-crop-backup-20261110-085425-180770). Deploy_hailo supports --detection-mode
adaptive with --input-size 640 and retains conversion validation/rollback.

Pi IPv4 timed out despite eth0 having 192.168.137.190. afc-rpi.local resolved over
IPv6 and its saved SSH host key was verified. Development-only management helper
in test-artifacts/native_crop_hardware.py uses that interface, with the existing
Windows credential (no password stored). Production SSH defaults are unchanged.
Windows Ethernet 192.168.137.1 reports AddressState Duplicate. Direct browser
access also times out; WEB.md documents manual IPv6 SSH forwarding for the
development pages and training camera connection. No network changes were made.

136 unit tests and training/autonomous browser checks passed, including native
profile locking, medium-model selection and native ZIP export. Clean/annotated
JPEG freshness and 1080p dimensions were verified on the Pi; both ends of the
1001-frame QA video decoded. See native-crop-media-verification.json. Float zoom validation
also fixed a pre-existing missing math import. Final verification is recorded below.
Controller stopped, outputs_zero true, GPIO disabled and pin null. Physical flight
validation is still pending. Read the following sections as historical snapshots.

# Latest update: 1080p50 recording and active 1920 detector

User requested new training videos at 1920x1080 50 fps, selectable recording
resolution, and the largest feasible detector with Docker now running.
Implemented and deployed. See TRAINING.md for the current complete workflow.

## Current Pi configuration

Camera: native IMX708 binned 2304x1296 sensor mode (queried max 56.03 fps),
1920x1080 output at a target 50 fps. camera-settings.json persists width/height,
fps and zoom across restarts. Default recording choices on both camera and
studio pages: 1080p50, with alternatives 960x540/720p and 15/25/30/50 fps.
Record waits for applied mode/rate before opening the clip; settings lock during
recording. camera_modes.py validates mode choice and preserves full sensor FOV.
camera_state.py owns settings and latest-frame slots, without hardware imports.

Capture/recording and inference are decoupled. Camera capture stays in the main
process; Hailo inference/JPEG processing is in a spawned child because native
Hailo calls otherwise held the GIL and stalled capture. IPC has one outstanding
inference, using the latest captured immutable frame. Recording does not wait
for inference. Annotated copies never enter the clean recording queue.

Video writer is OpenCV's parallel MJPEG AVI encoder, 4 stripes, quality 90. The
old FFmpeg writer benchmarked ~28 fps; the parallel writer ~82 fps on this Pi.
The bounded raw-frame queue has a 768 MiB budget (129 frames at 1080p), enough
to absorb observed SD-card pauses. Clips stop around 1.75 GiB, retaining room for
queued frames below the extraction limit of 2 GiB. Actual FPS/drops are reported.
Extraction supports up to 60 fps and a configurable limit up to 10000 frames.
Studio defaults: detector input 1920, batch 1. A minute at 50 fps has about 3000
frames; choose extraction 50 fps / max >=3000 to sample the whole clip.

## Live performance proof

60-second GUI recording with preview and 1920 detector running:
02fed1518b5b47f4a3e8cc69f152dff2 ? 2975 frames, actual 49.6 fps, zero recording
queue drops, 898322580 bytes. First and last AVI frames decoded at 1920x1080.
Evidence: test-artifacts/1080p50-sustained.json and final-1080p50-status.json.
The earlier eight-second test reached 49.77 fps, zero queue drops. Earlier
unsuccessful QA clips were archived to Pi data/recording-tests; user clips were
preserved. Successful QA clips remain in recordings. No training data was
created from real QA clips automatically.

The Pi clock was observed behind the client clock, so QA clip UTC metadata has
November 6 timestamps despite this work occurring November 9. FPS uses monotonic
timing and is unaffected. Clock configuration was not changed in this task.

## Detector

Active HEF: /home/user/pi-vision/models/plane-target-20261109-022355-9ed07a-1920-h8.hef
Input: 1920x1920x3; HAILO8, single context. This preserves the full 1080p width;
it is the largest native-width input tested here, not an absolute chip tensor
limit. Actual hardware inference ~78 ms; complete live detection ~7-8 fps.
Camera/recording continue near 50 fps. NPU utilization approximately 45-56% in
these runs. Feedback Hz cannot create more fresh detections than detector FPS.

Existing weights were recompiled, not retrained. Candidate artifacts:
runs/plane-target/20261109-022355-9ed07a/resolution-candidates/1920/
compile succeeded after fixing the calibration batch floor at 1. Layer Noise
Analysis ran out of GPU memory and skipped optional statistics; quantization
and HEF compilation completed. Physical NPU conversion smoke check passed:
13/14 float-1920 detections matched on 16 frames, mean matched IoU 0.8985.
This is not an accuracy improvement claim: the old float 640 run had 30
predictions on that set versus 14 at 1920. Collect/label native distant-plane
1080p footage and retrain at the intended size before assessing field accuracy.

compile_hailo.py, train_yolo.py and studio accept 320-1920 (step 32). Deployment
supports --input-size 1920 and uses separate candidate/model/validation paths;
old 640 HEF is preserved. New deploy checks controls and recording stopped.

120 unit tests passed. Browser checks passed for training/record-extract and
autonomous controls; live GUI defaults/locks and responsive layout also passed.
Final source backup on Pi: .autonomous-backup-20261109-151028-847117.
Controls remained stopped, zero-output state preserved, GPIO disabled/pin null.
No aircraft was armed and no actual pin was selected. Websites remain development
tools; the GPIO/headless design is unchanged.

---

# Latest update: configurable detector input resolution

Implemented on 2026-11-09. Training/preview/Hailo pipeline now accepts square
input sizes 320-1280 in steps of 32. Defaults remain 640. Removed the Hailo-only
640 restriction from the studio and CLI. Training uses the requested resolution
through ONNX export/compile. Existing weights can be compiled at another size
without training via train_yolo.py compile --model ... --data ... --imgsz 960
--name UNIQUE-JOB. Larger candidates go in compiled-SIZE; 640 retains compiled/.

compile_hailo.py reads static NCHW ONNX dimensions, sizes calibration/NMS to
match, reduces calibration batch size for larger inputs and writes per-stage
size/hash manifests. Resume rejects mismatched checkpoints or resolutions.
Legacy 640 HAR files without manifests retain existing support. verify_hailo.py
accepts larger static RGB inputs, and live app.py already read HEF dimensions.
Camera status now exposes model_input_shape; the autonomous page distinguishes
capture from detector size. Studio prediction has a separate input-size selector
and explicitly uses static square padding.

Real existing weights were exported at 960x960 and checked:
test-artifacts/detector-960/plane-target.onnx (44,930,871 bytes).
The existing Docker compiler container is paused. It was not resumed, and no
larger HEF was compiled/activated. The active Pi detector remains 640x640 with
960x540 capture. User has discussed 1080p camera capability, but capture resolution
was not changed in this task. A larger input's accuracy, compiler resource fit
and Pi FPS are not yet measured. Do not claim it is already running at 960/1280.

114 unit tests passed, with training/autonomous browser checks. Compiler main
orchestration was tested with a mocked SDK, plus real ONNX/letterbox tests; that
is not proof of physical Hailo compilation. Source/status update deployed with
backup .autonomous-backup-20261109-141218-714049. Pi check confirmed model input
[640,640,3], controls stopped, zero mode preserved, and GPIO disabled/pin null.

See TRAINING.md for the new resolution workflow and AUTONOMOUS.md for runtime
behavior. Existing development web pages and GPIO/headless control remain.

---

# Latest update: GPIO launch, not ROS

User clarified that production receives a GPIO start command. The input pin is
not decided. Do not choose or enable a physical pin on their behalf.

Deployed 2026-11-09. launch_input.py loads gpio-launch.json, defaulting to
GPIO disabled / pin null / dry_run true / restore_outputs false. The example
configuration is deployed. No live GPIO configuration exists yet. GPIO Zero is
installed on the Pi, but no GPIO input device is opened while disabled.

The GPIO input accepts one debounced inactive-to-active transition (default
50 ms debounce, 10 ms sampling). An active level at boot does not trigger;
held levels, duplicate edges and busy/rejected commands do not queue a retry.
BCM 2/3 are rejected because the DAC uses I2C. The pin is intentionally null.

Autonomous sessions now identify source web or gpio. Web starts retain their
browser heartbeat/watchdog behavior. GPIO starts require no browser heartbeat;
all camera/loss/limits/maximum-duration behavior remains local and unchanged.
Preflight is shared in Autonomous.start_from_camera. HTTP clients cannot claim
gpio supervision. GPIO/live logs include trigger metadata, and state changes
are written to the service journal. A GPIO pulse is a start command rather than
a held enable signal; returning inactive does not stop the flight.

remote_control.py --no-web runs the same flight/GPIO workers without creating
the development HTTP server. Default service still exposes development pages.
Both modes hold an exclusive .control.lock before opening the DAC. A second
headless process was verified to exit before DAC initialization while the real
service was running.

103 tests passed, including simulated GPIO edge/debounce/startup-high behavior,
headless lifecycle, browser/GPIO watchdog separation and the web source guard.
Autonomous browser checks passed. Real pin testing and flight validation await
pin selection and commissioning. No aircraft was armed. Pi checks confirmed
GPIO disabled with pin null, controls stopped, and persisted zero-output state
retained. Camera remained ready.

Pi source backup: .autonomous-backup-20261109-135022-149328.
See AUTONOMOUS.md for the production workflow and GPIO configuration.

---

# Latest update: autonomous preview and feedback rates

Deployed 2026-11-09. The autonomous page now includes same-origin annotated
preview, camera target FPS control (while stopped), measured browser FPS/frame
age, camera/detection FPS and NPU utilization. The camera page and autonomous
page share camera-preview.js, using one latest-frame request at a time. The
old camera page's fixed 15 fps preview cap is removed. Remote preview uses a
fixed loopback proxy, HTTP keep-alive and TCP_NODELAY; the existing SSH tunnel
can expose it without a second tunnel.

Autonomous feedback_hz is persisted (2-60 Hz, default 10). control_worker
subtracts request time from its schedule. Actual loop Hz and fresh observation
Hz are reported; duplicates never advance the confirmation/filter. At camera
30 fps, dry runs measured target 10 -> 10.0 loop / 10.0 fresh; target 30 ->
29.9 loop / 28.9 fresh; target 60 -> 60.0 loop / 30.2 fresh observations/s.

The real sensor exposure timestamp now sets frame freshness; Picamera2's
completed-frame cache is disabled. Hardware benchmark (960x540, short runs):
15 target -> 15 detection / 13.9 browser fps / 17.5% NPU;
30 target -> 30 detection / 29.4 browser fps / 35.0% NPU;
45 target -> 22.5 detection / 21.5 browser fps / 26.1% NPU;
60 target -> 30 detection / 30.0 browser fps / 35.1% NPU.
Recommend testing 30 camera FPS and 30 feedback Hz; higher target values are
not automatically faster. Benchmarks restored the original camera 15 fps and
feedback 10 Hz. All output/calibration/limit/sensitivity states were unchanged;
no aircraft was armed. Benchmark sessions used dry run and heartbeat expiry.

92 unit tests and autonomous/joystick/flight-log browser checks passed, plus
live autonomous-preview and main-camera browser rate checks. Results are in
test-artifacts/camera-rate-benchmark.json and controller-rate-benchmark.json.
Pi code backup: .autonomous-backup-20261109-133518-832672.

---

# Pi Vision current state ? 2026-11-09

Read README.md, WEB.md, AUTONOMOUS.md and TRAINING.md for current instructions.
The source snapshot before refactoring is test-artifacts/before-refactor.zip.

## Deployed Pi

- Pi: user@192.168.137.190. pi_ssh.py uses the saved Windows credential; do not
  put passwords into files or logs.
- Camera: http://192.168.137.190:8000/ ? trained plane/target Hailo-8 model
  plane-target-20261109-022355-9ed07a-h8.hef is active, about 15 fps, 960 x 540.
- Control: http://192.168.137.190:8001/ ? manual, autonomous, calibration,
  joystick and flight logs are routes of the same application.
- Autonomous controller is deployed at /autonomous and defaults to dry run.
  Flight tuning and physical flight validation remain pending. Do not arm or
  restore voltages merely to inspect the project.
- At verification: controls stopped; outputs_zero true; both requested outputs
  0 V, which is not idle with inverted wiring. Physical switch state is unknown.
- Calibration, limits, sensitivity, saved zero mode, model files/service override,
  recordings and flight history matched their pre-deployment fingerprints.
- Source backup on Pi:
  /home/user/pi-vision/.autonomous-backup-20261109-130840-383148
- Live camera and all five control pages passed GET-only desktop/mobile browser
  checks. No aircraft was armed; no control/settings POST was sent to the Pi.

## Refactor

state_store.py owns the existing atomic JSON save sequence used by calibration,
remote settings/power and autonomous parameters. Training uses one ONNX export
helper and one trained-weight discovery method. The remote has one HTTP response
writer. pi_deploy.py owns source staging, backups, readiness and rollback for
both source deployment entrypoints. The model-deployment script stays separate
because it validates/activates an HEF and a systemd override rather than source.
Removed an unused import, duplicate detection timestamp state and stale notes.
Existing URLs, APIs, compatibility handling and parameter defaults are retained.

86 local unit tests passed. Browser checks passed for autonomous, joystick/manual,
training, video recording/extraction, and flight logs. The full compiler pipeline
was not rerun during this refactor; the active HEF was preserved. Existing local
validation comparison reports 30/30 matched detections, mean IoU 0.9291.

## Laptop applications

Training studio: .\start-training.ps1 -> http://localhost:8010
Joystick tunnel: python joystick_tunnel.py -> http://localhost:8002/joystick
The tunnel exposes every control page, including /autonomous and /flights.
Neither laptop listener was running at the latest check. Automatic approval
review blocked the combined verification/startup command with "blocked by policy".
Read-only verification subsequently succeeded; do not bypass the startup block.

Training output selector retains standard YOLO best.pt by default, with optional
Hailo-8 compilation. Hailo needs Docker Desktop and the existing persistent
pi-vision-hailo-compiler container; do not recreate the container unnecessarily.
Training/compilation never activates a Pi model automatically. Preserve data/,
runs/, logs/, calibration/settings/power files and existing compiler caches.
