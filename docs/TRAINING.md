# Training

Start `scripts/start-training.ps1` and open http://127.0.0.1:8010/.

## Record and label

1. Record clean footage on the Pi or import a local video.
2. Sample varied frames, including distant planes, different backgrounds and frames without a plane.
3. Label the images and split by recording/session to keep neighboring frames out of different dataset splits.
4. Export a dataset. Exports are snapshots; later label edits do not change an existing export.

Images and annotations live in `data/plane-target/` on the laptop. Pi clips live in `data/recordings/`.

## Train

Choose starting weights, epochs, batch size and detector input size. Full frames are the default; native crops are an experimental option for small-object detection. Reduce batch size if GPU memory runs out.

**Data loader workers** controls parallel image loading and augmentation. The default is 10, adjustable from 0 to 16. Use 0 to disable multiprocessing if Windows has loader problems. More workers use additional system RAM and can add startup overhead; they do not increase batch size or model input resolution. CLI training/validation accepts `--workers 10` as well. This count does not pin processes to Intel P-cores; the operating system schedules them. The default was increased from 4 at the user's request; performance with 10 has not been benchmarked.

A short RTX 4060 Laptop comparison on 2026-11-19 used YOLOv8s, batch 16, input 640 and the same 25% dataset subset. After the first 10 batches, average batch intervals were 0.312 seconds with 0 workers and 0.216 seconds with 4 workers. First-epoch training time was 17.0 versus 19.9 seconds because worker startup offset the later gain. This supports trying 4 for longer runs; it is not a measured whole-run speedup or accuracy result. Reports/logs are under `test-artifacts/training-workers-20261119/` and `test-artifacts/training-workers-*.log`.

Choose **YOLO** for standard weights or **YOLO + Hailo** to also export ONNX and compile a HEF. Compilation requires representative calibration images and the configured `pi-vision-hailo-compiler` Docker container.

Outputs live under `runs/plane-target/<run>/`:

- `weights/best.pt` — YOLO weights.
- `compiled/` — 640-input Hailo output.
- `compiled-<size>/` — other Hailo input sizes.

Hailo export accepts sizes 320–1920 divisible by 32, subject to compiler/model support. A HEF has a fixed input size. Camera resolution, model input size and preview resolution are separate settings.

## Test and activate

1. Compare candidates on held-out footage. The inspector can run PT weights locally or HEFs on the Pi.
2. Select a HEF and use **Test one frame on Pi** with **Full frame**. Controls and recording must be idle; the camera pauses for testing and is restored afterward.
3. After a successful test, use **Activate on Pi**. Activation requires proof for the exact HEF bytes; changing the file or restarting the studio requires another test.
4. Activation installs the model, updates the camera override, checks readiness and attempts rollback on failure. It selects full-frame inference and preserves the confidence setting.
5. Use **Start live Pi preview** to inspect the active model and metrics.

Native tiles preserve small-object detail through multiple inferences. Native-only testing does not unlock activation; adaptive flight detection requires a validated 640-input model. Compare accuracy and latency on the actual Pi before changing the working model. Read the camera API for the active model rather than relying on an old filename or note.
