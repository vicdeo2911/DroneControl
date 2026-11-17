# Plane and target training

## Continuous live testing on the Pi

In the inspection panel choose **Compiled Hailo (.hef)**, select the HEF that is
active on the Pi, and click **Start live Pi preview**. The page shows the Pi's
annotated continuous camera stream and actual NPU detection rate, inference time,
frame age, preview age, active mode and confidence cutoff. It uses the existing
Pi inference worker; it does not run laptop YOLO, send per-frame test requests,
pause/restart the camera, activate a model, or send aircraft commands.

For a different HEF, first run a **Full frame** test, review detections and use
**Activate on Pi**. Then start live preview. Model SHA-256 is checked against the
loaded Pi HEF and again against the stream response header. If the active model
changes, local HEF changes, preview is stale, or the connection fails, viewing
stops rather than presenting the wrong model's results. A same-model camera
restart is detected by an instance ID and reconnects when the new status is ready.

While live, inspection-only view/input/confidence controls are hidden; the page
reports the Pi settings actually in use. Stop live preview to resume single-image
inspection controls. **Test one frame on Pi** remains a separate paused-camera
candidate check. Live viewing is read-only and can monitor normal flight/recording;
the stopped-controls requirement applies to single-frame maintenance and activation.

Detection FPS and preview FPS differ. The current Pi preview limit is15FPS;
detection remained about36FPS in the live check, with1080p50 capture. Preview
encoding/streaming is separate from controller feedback. Stopping/hiding the page
closes its stream and status polling. Multiple viewers share the same NPU worker.

Restart the laptop training studio and refresh the page to load the new backend.
If its API lacks the live capability, the new page explains that restart is needed.
When training/compilation is active, let it finish first.


## Activate a tested HEF on the Pi

In the inspection panel select **Compiled Hailo (.hef)**, choose a completed model,
choose **Full frame**, and run **Test selected image** or **Test camera frame on Pi**.
Review the detections. **Activate on Pi** becomes available after a successful
Full frame test of those exact HEF bytes. A native-tile-only test does not enable
activation because activation uses full-frame inference. A successful test confirms
the model runs; it does not establish accuracy across flight conditions.

Click **Activate on Pi** and confirm the model/input size shown. Selecting or
testing a HEF does not activate it. Activation keeps the existing camera arguments,
including its confidence cutoff and preview limit; saved recording geometry/rate,
controller settings, calibration and GPIO settings are retained. The inspection
confidence slider is not applied to the production detector. Activation restarts
camera inference and does not arm the aircraft.

Both the laptop and Pi check the tested model's SHA-256. If the HEF changes, retest
it. The test proof is local to the current studio session, so a studio restart also
requires another test before activation. Stop controls/autonomy and recording first;
the existing maintenance lock blocks starts during activation.

The Pi stores an immutable model copy named `models/studio-<sha256>.hef` and its
plane/target labels, backs up the current systemd override in the activation job,
then restarts and checks the actual active model name and input shape. If startup
fails, it restores the previous override and detector. Existing model files remain.
Activation diagnostics/backups are under `.hef-inspection/<job-id>/` on the Pi.


## Inspecting compiled Hailo models

In **04 / Inspect plane -> target tracking**, choose **Model format**:

- **Standard YOLO (.pt)** runs saved weights on the laptop, with an adjustable
  input size and the existing continuous live preview.
- **Compiled Hailo (.hef)** lists completed compiler outputs, including resolution
  candidates. Identical copied HEFs are listed once. Select the HEF, then use
  **Test selected image** or **Test camera frame on Pi**.

HEF tests run on the Pi's actual NPU using the selected model. The detector input
is fixed inside the HEF; its selector is read-only. Full-frame inspection uses
that fixed input. Native tiles require a 640-square HEF and remain experimental.
The confidence filter applies to the detections emitted by the HEF; its compiled
NMS limits also apply. Selecting/testing a HEF does not activate it for flight; use the separate activation action above.

Each HEF test briefly pauses the Pi camera inference service, tests one saved
image or one freshly fetched camera frame, and restores the camera service with
its original active model. Continuous HEF preview is available separately through Start live Pi preview for the active HEF.
The result shows NPU inference time separately from the total upload/test/restart
round trip. On this Pi, single-image tests took about6-7seconds end to end, while
640 NPU inference itself took about14ms. Tile time sums all processed tiles.
These are test measurements, not a live camera-to-controller FPS benchmark.

Aircraft controls/autonomy and recording must be stopped before testing. A
cross-process camera-operation lock prevents new control/recording starts during
the test. Service restoration also runs after an invalid HEF or failed inference.
The Pi controller must advertise the new guard capability or the test is refused.
Test artifacts and diagnostics live under `.hef-inspection/<job-id>/` on the Pi;
large uploaded model/image copies are removed after each test. User recordings,
training data, model activation configuration and calibration are not changed.

HEF inspection uses the saved Windows SSH credential and trusted Pi host key.
`requirements-training.txt` now includes `requirements-ssh.txt`; existing training
Python dependencies were updated for this feature. For a new setup, run
`setup-training.ps1`, save SSH credentials with `python pi_ssh.py --save-password`,
and deploy both services with `python deploy_autonomous.py`.

**If the studio is already training or compiling, let it finish before restarting
it.** Then run `start-training.ps1` and refresh the page to load the new backend.
The updated page explains when it is connected to an older backend. A HEF being
produced by the current running studio job is hidden until that job completes.
Existing completed HEFs can be tested independently of laptop GPU training.


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


## Recommended distant-plane workflow (2026-11-10)

Record **1920x1080 at 50 FPS**, keeping the full camera view. This is deployed and
verified with simultaneous adaptive detection: 1001 frames at 50.04 recorded FPS,
zero recording-queue drops, approximately 50 crop detections/s. Use sharp, well-lit
footage, correct distant focus and a short exposure where lighting permits; high
frame rate alone does not prevent motion blur. Include farthest-distance passes,
turns, entries near the frame edge, changing backgrounds and negative footage.
Start labelling 2-5 frames/s, plus difficult transitions. Split by complete flights.

In the studio choose **YOLOv8s**, **Native 640 crops + full frames**, detector input
**640**. The crop profile locks input to 640 to match the deployed adaptive path.
Recordings and annotations remain native resolution; dataset exports add overlapping
search tiles and object crops without resizing. All derivative crops stay in the
source session's split. Partial objects are labelled; crops with tiny annotation
fragments are skipped instead of treating those fragments as background. Full
frames remain in the export for broader scale/context training. Batch 1 is a safe
starting point; increase batch if GPU memory allows. Standard YOLO and optional
Hailo compilation remain separate output choices. Hailo calibration reads the
exported training images, including crops. Compilation does not activate a model.

YOLOv8m is available for a controlled accuracy comparison at the same 640 input.
The current YOLOv8s crop pipeline already reaches the 50 FPS camera rate, so a
larger model should be selected by held-out recall, false detections and latency.
No new model has been trained automatically on the previous footage.

Inspection offers **Native tiles** (exhaustive search with duplicate merging) or
**Full frame**. Native inspection time covers the entire image; live tracking
usually processes only one crop. For accurate model comparisons, use the original
held-out frames and count each plane once, rather than interpreting a mixture of
full-image and overlapping-crop validation metrics as independent examples.

The Pi now uses `--detection-mode adaptive` with a **640x640 HEF**. It scans eight
overlapping native tiles of a 1080p frame, then follows a unique plane with a
moving 640 crop. A 12-pixel plane remains 12 pixels wide inside the crop; resizing
the whole 1080p view to 640 would reduce it to about four pixels. The model does
not inspect the whole view on every inference. An empty scene takes roughly
160 ms for an eight-tile pass at 50 crop results/s. Reacquisition and motion can
change that time. Multiple aircraft outside the active crop cannot be ruled out.

Current evidence: old full640 float validation matched 95/120 planes, native tiles
106/120 on 128 existing 960x540 validation images (confidence .6, IoU .5). Small
plane matches improved 59/84 to 73/84; unmatched predictions increased 1 to 6.
This supports the approach, but does not establish accuracy on new distant 1080p
flights or quantized moving-crop tracking.

1536x864@120 remains an experiment: that sensor mode crops the field of view and
is not enabled in the recording UI. Its narrower view can lose the aircraft.
The verified 1080p50 mode is the current collection and flight-camera choice.

Deployment after training/validation:

```powershell
python pi_ssh.py --deploy-camera
python deploy_hailo.py --run RUN_ID --input-size 640 --detection-mode adaptive
python benchmark_detection.py --output test-artifacts/detection-live.json
```

Source deployment must precede selecting adaptive mode. Model deployment still
checks stopped controls/recording, validates conversion and rolls back on failure.
Full-frame detector sizes remain supported via the existing full preparation and
`--detection-mode full`. Preview encoding is independent of flight observations;
the deployed preview limit is 15 FPS. `--preview-fps 0` disables JPEG work for a
headless camera service while capture, recording and inference continue.
The following resolution notes include historical 1920-square experiments.


The local studio trains a detector with two classes, `0: plane` and `1: target`.
The fixed ground camera watches both. Use the sign as `target` while developing;
collect and label balloon examples when you switch to a balloon. A sign-only
dataset does not establish balloon detection performance.

The studio runs on this Windows laptop at **http://localhost:8010**. Images,
labels, exports and weights stay on this computer. The existing Pi remote still
provides manual throttle/roll control and command logging, plus a separate
[autonomous launch/controller page](AUTONOMOUS.md). See [WEB.md](WEB.md) for all pages.

## Start

In PowerShell in this folder:

```powershell
.\start-training.ps1
```

Keep the terminal open. The prepared `.venv-training` is separate from the Pi's
native camera/Hailo environment. To recreate it, run `./setup-training.ps1` first.
It installs CUDA PyTorch for this NVIDIA laptop; `-CpuOnly` skips the CUDA install.
If PowerShell blocks local scripts, run the Python entry point directly:

```powershell
.\.venv-training\Scripts\python.exe training_studio.py
```

CUDA was verified on the RTX 4060 Laptop GPU (8 GB) with a real one-epoch
synthetic training run, checkpoint loading and inference. This verifies the
training installation, **not** real plane/target accuracy. No aircraft dataset
or trained aircraft checkpoint has been fabricated.

## 1. Collect independent recordings

### Record first, label later (recommended)

Open http://localhost:8010 and follow **Record video and extract frames**. Choose
a clip length (typically 30–60 seconds), then **Record video**. **Stop & save**
finishes early; otherwise the clip stops at the chosen duration (maximum five
minutes). You can also start/stop clips on http://192.168.137.190:8000/.

Clean MJPEG/AVI clips are saved directly on the Pi under
`/home/user/pi-vision/data/recordings/`, using the existing camera frames before
boxes/text are drawn. Browser speed does not determine the recording rate.
The status shows written frames, measured recording FPS and dropped frames.
The recorder runs independently of browser visibility/connection and retains
clips until you deliberately remove them. Keep this directory during deployments.
Camera FPS and zoom changes are blocked during a clip to keep its settings fixed.

When the clip is completed, select it in the laptop studio, choose **1 second
(1 fps)** or **0.5 seconds (2 fps)**, select the train/validation/test split under
Collect, then click **Extract frames**. The laptop downloads the video, saves a
copy in `data/plane-target/videos/`, and adds sampled images to the annotation
library. The extraction runs in the server even if the browser closes. A clip's
identity keeps it in one dataset split, including when resampled. Exact duplicate
images are skipped. Each extracted image starts unreviewed; you draw the plane
and target boxes. You can also ask the agent to extract a saved clip for you.

At 1 fps a 60-second recording provides about 60 labelable images; at 2 fps it
provides about 120. Extraction uses recorded frame timestamps, so it can respect
actual capture timing when frames were dropped. AVI playback itself uses the
selected fixed camera FPS; check dropped frames/measured FPS for capture gaps.
No audio is recorded. An interrupted or failed clip is marked and is not offered
for normal extraction. Up to 1,000 frames are sampled per website extraction.

The studio preview now uses a continuous clean MJPEG stream. The older preview
waited 500 ms between images and was limited to roughly 2 fps; this did not reflect
the Pi's source camera FPS. After the fix, LAN stream measurements received
approximately 15 fresh frames per second through both the Pi and laptop proxy.

### Import photos or existing videos

Choose a recording/session name, such as `flight-01`, and a split. Use different
recordings for training, validation and final testing, roughly 70/20/10% overall.
The app prevents one session from entering different splits and skips exact
duplicate images. It cannot detect differently encoded or nearby frames from
the same recording: keep those together yourself.

Import photos using **Import selected**, or sample an existing video:

```powershell
.\.venv-training\Scripts\python.exe import_video.py .\flight-01.mp4 --session flight-01 --split train --interval 1
.\.venv-training\Scripts\python.exe import_video.py .\flight-02.mp4 --session flight-02 --split val --interval 1
```

Click **Refresh library** afterward. Video import defaults to at most 1,000
samples. Adjust `--interval` or `--max-frames` if needed.

For live capture, the Pi needs the updated `app.py` from this folder. Install it:

```powershell
.\deploy-camera.ps1
```

The helper uses `pi_ssh.py` and the saved password in Windows Credential Manager
for SSH and sudo. Install its dependencies with
`python -m pip install -r requirements-ssh.txt` if recreating this laptop setup.
The helper backs up and uploads the camera app, recorder module, camera template
and recording controls, validates Python files and restarts only `pi-vision`.
It preserves calibration, remote settings, power state and flight logs. Do not
restart the camera during a flight. Verify
http://192.168.137.190:8000/raw.jpg before using **Capture frame** or interval
capture. The studio never substitutes annotated images for clean frames.

The clean-image update was deployed over authenticated SSH on November 8 and its
SHA-256 verified against the local file. After reseating the ribbon and rebooting,
the IMX708 camera was detected and the service ran at 960 x 540, 15 fps. Clean
snapshots and the laptop studio camera proxy were checked successfully. Open
http://localhost:8010 and use View camera, Capture frame or interval capture.
If detection fails again, check the cable with Pi power off, then reboot and run
`rpicam-hello --list-cameras`. Photo/video import also works without the Pi.

For a first useful dataset, aim for **500–1,000 varied labeled frames**. Record
about 10–15 clips of 30–60 seconds and sample 1–2 frames per second, selecting
useful examples. Include both classes together frequently, each separately,
and about 10% background-only images. Keep whole clips in one split: about
70% training, 20% validation and 10% testing. These are practical starting
targets, not accuracy guarantees. For broader coverage, expand toward at least
1,500 images containing each class, guided by held-out failures; see
[Ultralytics dataset guidance](https://docs.ultralytics.com/yolov5/tutorials/tips-for-best-training-results/).
When both objects appear in one image, that image contributes to both classes.

Collect a range of apparent sizes, headings, backgrounds, lighting, motion blur
and partial occlusions, plus empty backgrounds. Keep the small plane visible
at the distances you intend to use. Begin with a few hundred varied labeled
frames and expand based on misses in held-out footage; frame count alone is
not an accuracy criterion. Interval capture is bounded by capture attempts and
stops on camera errors, Escape, or hiding the page. A request already in flight
may finish after capture stops.

## 2. Label both classes

Select an image. Press **1** for plane, **2** for target, then drag a tight box
around every visible object of that class. Select boxes by clicking them or
their chips; delete a wrong box and redraw it. **Save + next** (or Ctrl+S) stores
labels. Use **Confirm no objects + next** only for an image with neither class.
Unreviewed images are excluded from training.

SQLite annotations and clean JPEGs live under `data/plane-target/`. Back up that
entire directory. Class definitions are shared by labeling, training and preview.
Exports include normalized YOLO text labels, a data YAML and an annotation
manifest with import/capture UTC timestamps. Video import timestamps identify
import time, not the original video's wall-clock recording time.

## 3. Train locally

In the studio, start with **YOLOv8s**, **100 epochs**, **640 px**, **batch 8**,
and **Auto**. Start training after both training and validation have reviewed
examples of both classes. Those are minimum integrity checks, not enough data
for a useful model. The first run downloads pretrained weights.

Each run copies a fixed reviewed dataset into `data/plane-target/exports/<id>/`.
Further labeling cannot change a running experiment. Training runs in a separate
process; logs remain on disk and appear on the page. Closing the browser leaves
training running, but exiting the studio stops its training child process.
Only one studio instance should use this library/GPU at a time. Reduce batch to
4 if the GPU runs out of memory. Start another run for retraining; optimizer
resume is not implemented in the studio.

Artifacts live in `runs/plane-target/<run>/`: `weights/best.pt`, `weights/last.pt`,
`results.csv`, plots and training arguments. `best.pt` is selected using validation
performance. Inspect precision, recall and mAP per class, then test unseen footage
visually, especially small/distant planes and similar-looking background objects.

**Export YOLO dataset** also offers a portable ZIP. Extract it before training
elsewhere; its YAML uses the extraction directory. The local YAML printed in
the page uses the absolute snapshot path.

CLI alternatives:

```powershell
.\.venv-training\Scripts\python.exe train_yolo.py train --data data/plane-target/exports/EXPORT_ID/data.yaml --device 0
.\.venv-training\Scripts\python.exe train_yolo.py val --model runs/plane-target/RUN/weights/best.pt --data data/plane-target/exports/EXPORT_ID/data.yaml
.\.venv-training\Scripts\python.exe train_yolo.py test --model runs/plane-target/RUN/weights/best.pt --data data/plane-target/exports/EXPORT_ID/data.yaml
```

`test` requires a reviewed held-out test split in that export. The Windows trainer
uses `workers=0` and a guarded main entry point. See the official
[Ultralytics training documentation](https://docs.ultralytics.com/modes/train/).

## 4. Preview and improve

Select a saved `best.pt`, then **Test selected image** or **Start live preview**.
Live preview needs the updated, working camera service. The server performs
inference locally and displays the exact image it analyzed. It reports normalized
target-minus-plane center offsets only when exactly one of each is detected.
Missing or multiple objects make the geometry unavailable. Predictions never
become accepted annotations automatically. Do not tune on your final test set;
collect new training examples of the failure cases and retrain.

The studio preview remains observation-only and does not command the aircraft.
Its API returns `mode: observation_only` and `passage_confirmed: false`.
Supervised autonomous launch and horizontal steering are available separately
on the Pi remote's [Autonomous page](AUTONOMOUS.md). Automatic balloon
interception is not implemented. Two image boxes do not establish depth,
collision or a successful pass; throttle and roll provide no measured attitude
or altitude. Physical tuning and flight validation remain necessary.

## 5. Hailo deployment

Laptop `.pt` preview works directly after training. The Pi Hailo pipeline requires
a compiled **HAILO8 HEF**, not a renamed `.pt` or ONNX file. Export an intermediate:

```powershell
.\.venv-training\Scripts\python.exe train_yolo.py export --model runs/plane-target/RUN/weights/best.pt --imgsz 640
```

Compilation requires a compatible Hailo Dataflow Compiler / Model Zoo environment,
representative clean training calibration images, and two-class postprocessing.
The ONNX export is static, raw-head, opset 13; parser node names and export support
must be checked against the compiler you install. The current trained model has
been compiled with DFC 3.34.0, and the trained HEF is now active on the Pi.
Live inference was verified during the controller deployment. Hailo's compatibility notice specifies Model
Zoo **v2.x** with Dataflow Compiler **v3.x** for Hailo-8; consult
[Hailo compatibility](https://github.com/hailo-ai/hailo_model_zoo) and its
[YOLOv8 retraining instructions](https://github.com/hailo-ai/hailo_model_zoo/blob/v2.16/training/yolov8/README.rst).
Do not replace the Pi's working HailoRT/native camera packages with pip packages.

Once a compatible two-class HEF is actually available, copy it and
`plane-labels.txt` to the Pi, stop the existing camera service, then run with
`--model /path/to/model.hef --labels /path/to/plane-labels.txt`. Custom labels
are opt-in; the default existing COCO model continues to use `labels.txt`.

### Compiler on this Windows laptop

Docker Desktop uses WSL2. Container `pi-vision-hailo-compiler` provides Ubuntu
22.04, Python 3.10, Hailo Dataflow Compiler 3.34.0 and TensorFlow CUDA libraries.
The project is mounted at `/workspace` and Downloads at `/packages` (read-only).
The compiler wheel comes from the Hailo Developer Zone; it is not part of this
repository. Compiler setup leaves the Pi's native camera and Hailo packages alone.

`compile_hailo.py` accepts a static YOLOv8 ONNX, a directory of representative
training images, and an output directory. It reads the static square input size from ONNX (320-1920, multiples of 32)
and uses the same RGB letterboxing as the Pi app and emits a two-class Hailo NMS output. Intermediate
`parsed.har` and `quantized.har` files support `--stage parse`, `--stage optimize`
and `--stage compile`. This script targets this project's YOLOv8 detector.

For the current run, from PowerShell:

```powershell
docker exec -e CUDA_VISIBLE_DEVICES=0 pi-vision-hailo-compiler python -u /workspace/compile_hailo.py --onnx model.onnx --calibration calibration --output compiled
```

Explicit GPU selection matters: Hailo's automatic selection skips GPUs with more
than 5% of memory already occupied, which can include this laptop's display GPU.
Calibration uses 256 training images. Optimization disables optional bias
correction because that pass is very slow in this WSL environment. Compare the
resulting INT8 model against validation images before accepting it for deployment.

`verify_hailo.py` runs saved images through the Pi's actual HEF runtime and the
camera app's preprocessing/decoder. Stop `pi-vision` for that check because it
owns the Hailo device, and restart the service afterward. Preserve the current
service configuration before switching models. Use the Pi camera page for live
NPU inference; the studio's trained `.pt` preview still runs on the laptop.

For the prepared run, execute `python deploy_hailo.py --run 20261109-022355-9ed07a`
from the project on the laptop. It performs NPU validation on 16 held-out frames,
requires at least 90% of original detections to match at IoU >= 0.5, installs the
camera-service model override, and checks the running model. Activation failure
restores the prior override and restarts the service. This smoke check does not
replace evaluation on the full validation set. The prepared run is already active;
there is no need to redeploy it to install controller or camera source updates.

## Verification

```powershell
.\.venv-training\Scripts\python.exe -m unittest discover -s tests
# Browser checks additionally need: pip install playwright (inside this venv).
.\.venv-training\Scripts\python.exe tests/check_training_browser.py
# Downloads YOLOv8n weights if absent, trains temporary synthetic data on CUDA:
.\.venv-training\Scripts\python.exe tests/check_training_smoke.py
```

These checks never issue aircraft output commands. Synthetic images/checkpoints
are confined to temporary directories; the reusable pretrained download is kept
in the project and ignored by version control.


## Choose standard YOLO or Hailo output

The studio's **Training output** selector defaults to **Standard YOLO**. It
trains and retains `runs/plane-target/RUN/weights/best.pt`, which works with
standard Ultralytics inference and the existing laptop preview. Docker and the
Hailo compiler are not used for this choice.

Choose **YOLO + compile Hailo-8 HEF** to additionally export static ONNX and compile
`runs/plane-target/RUN/compiled/plane-target-h8.hef` at 640 input, or
`compiled-SIZE/plane-target-h8.hef` for a different detector size. The selected
Detector input value is used for training, ONNX export, calibration and Hailo
NMS. Start Docker Desktop plus the existing compiler container first:

```powershell
docker start pi-vision-hailo-compiler
```

The studio checks compiler availability before beginning training. Compilation
uses up to 256 clean training images from the immutable dataset snapshot; held-out
validation/test images are not used for quantization. Very small datasets may
compile but do not establish detector quality. ONNX export/compilation output
appears in the training log. If compilation fails, `best.pt` remains available.
Stopping a Hailo run also attempts to stop its specific compiler process. The
container workspace is retained for diagnosing compiler failures.

Command-line equivalent:

```powershell
.\.venv-training\Scripts\python.exe train_yolo.py train --data PATH/data.yaml --target yolo
.\.venv-training\Scripts\python.exe train_yolo.py train --data PATH/data.yaml --target hailo --imgsz 640
```

This choice does not change the active Pi detector. The existing Pi camera
runtime still requires an HEF; standard `.pt` output is for a standard YOLO
runtime. Validate compiled detections and explicitly deploy/activate the HEF using
the existing model workflow. See [AUTONOMOUS.md](AUTONOMOUS.md) for the separately
accessible autonomous controller.


## Detector input resolution

The studio now accepts detector input sizes from 320 to 1920 in steps of 32 for
both standard YOLO and the optional Hailo compilation path. Common comparison
sizes are 640, 960, 1280, 1536 and 1920. The inspection panel has its own Detector input
selector so the same trained weights can be tested on the same image at each
size. Preview uses fixed square padding to match the static exported model.

A larger input does not require retraining merely to try it: export or compile
existing YOLOv8 weights at a new size. Training/fine-tuning at that resolution
can be evaluated separately. The November 9 Hailo model was fixed at 1920; its input
size cannot be changed by a runtime slider. A new HEF must be compiled, checked
and activated before the Pi detector changes.

For example, recompile existing weights at 960 without another training run:

```powershell
.\.venv-training\Scripts\python.exe train_yolo.py compile --model runs/plane-target/RUN/weights/best.pt --data data/plane-target/exports/SNAPSHOT/data.yaml --imgsz 960 --name RUN-960
```

Use a unique name for each compiler job. This command requires the existing
compiler container to be running and unpaused. It retains the checkpoint and
writes `runs/plane-target/RUN/compiled-960/plane-target-h8.hef`. Size 1280 works
through the same configurable path, subject to actual Hailo compiler resource
limits. A 1920-square candidate has now been compiled and verified on this Pi.

The compiler reads actual ONNX dimensions and applies them to calibration
letterboxing and NMS. Parsed/quantized manifests record input size and ONNX hash
to prevent reusing HAR files from another resolution/checkpoint. Old 640 HARs
without manifests retain legacy resume support; rebuild them if provenance is
uncertain. Calibration batch size reduces at larger inputs to limit memory use.

The Pi camera app already reads input dimensions from the HEF. It now reports
`model_input_shape` in status, displayed separately from camera capture size on
the autonomous page. The HEF validation script also accepts larger static input
shapes. Camera capture resolution is independent: a 1280 input applied to a
960-wide capture cannot recover detail lost at capture. Use suitable source
footage and compare missed distant planes, false detections and end-to-end FPS.

A real 960x960 ONNX export of the existing plane/target weights was verified at
`test-artifacts/detector-960/plane-target.onnx`. A later update compiled and activated the 1920 input candidate described below.


## November 9 snapshot: 1080p50 collection and 1920-input detector

The camera and training studio recording panels now provide **Recording
resolution** (960x540, 1280x720, 1920x1080) and **Recording FPS** (15/25/30/50).
Defaults are 1920x1080 at 50 fps. Pressing Record applies that mode, waits for
sensor settings to settle, then starts the clean clip. Resolution/rate are
locked during the clip. Camera settings persist in camera-settings.json.

The Pi's actual IMX708 sensor-mode query returned 2304x1296 at 56.03 fps; that
full-view binned mode supplies 1080p50. Capture/recording run in the camera
process, while Hailo inference runs in a separate process. Synchronous Hailo
calls otherwise stalled capture despite using different Python threads. The
inference side consumes the latest frame with one outstanding request and skips
older captures, independently of the recording feed.

Recording uses OpenCV's parallel MJPEG AVI encoder, quality 90 and four stripes.
The bounded queue can absorb SD-card write pauses, with a 768 MiB maximum raw
frame budget (129 frames at 1080p). Actual frame rate and queue drops are shown
in recording status. High-quality 1080p50 clips are large: the measured minute
was about 898 MB. Clips stop around 1.75 GiB, leaving space for queued frames and
remaining below the extraction/download 2 GiB limit. The duration maximum remains
300 seconds; a large file may finish earlier. Use separate clips for long flights.

A real 60-second test with the live preview open and the 1920 detector active
recorded **2975 frames at 49.6 fps, with zero recording-queue drops**. Capture
FPS is separate from detection FPS. See test-artifacts/1080p50-sustained.json.

Extraction supports rates up to 60 fps and an explicit **Maximum frames** setting
(1-10000, default 1000). To sample essentially every frame of a 60-second 50 fps
clip, select 50 fps and set the maximum to at least 3000. The default extraction
rate remains 1 fps for a smaller labeling workload. Keep an entire clip in one
train/validation/test split.

The November 9 Hailo-8 detector used **1920x1920**, preserving the full width of a
1920x1080 capture before square padding. This is the largest input tested here
and a native-width choice for 1080p; it is not a claim about the chip's absolute
maximum tensor dimensions. The existing YOLOv8s weights compiled in one Hailo
context and passed conversion smoke validation: 13/14 float-model detections
matched across 16 held-out images at IoU >=0.5, with mean matched IoU 0.8985.
Raw hardware inference averaged about 78 ms; the complete live detector runs
roughly 7-8 fps. Recording continues at approximately 50 fps.

This is a resolution/compilation update, not retraining on new data. The old
weights were trained at 640. On the old small held-out set, the float 1920 run
produced 14 detections versus 30 in the earlier 640 run; the conversion check is
not evidence of improved accuracy. Retake/label native 1080p distant-plane footage
and train/fine-tune at the intended input size before judging distant performance.
At that stage the studio defaulted to 1920 input and batch 1. Reduce the
input if GPU training memory is insufficient.

The current candidate is under:
`runs/plane-target/20261109-022355-9ed07a/resolution-candidates/1920/`.
Its prepared deployment can be verified/activated with:

```powershell
python deploy_hailo.py --run 20261109-022355-9ed07a --input-size 1920
```

It is already active; this command is not required again for collecting videos.
The previous 640 model and service-override backup remain available for rollback.
