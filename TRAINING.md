# Recording, training and model testing

Use the laptop studio at http://127.0.0.1:8010/. Start it with `./start-training.ps1`; install dependencies with `./setup-training.ps1`. Pi camera and controller services are independent. See [WEB.md](WEB.md) for page ownership and [ARCHITECTURE.md](ARCHITECTURE.md) for the module map.

## Capture and label

The implemented camera presets are 960x540, 1280x720 and 1920x1080. Capture settings accept even dimensions up to 1920x1080 and 1-60 FPS, subject to a compatible sensor mode. Recording resolution is selectable. **1920x1080 at 50 FPS is the current working capture configuration.** Native 2304-wide capture and 1536x864 at 120 FPS are not enabled by the current validation limits.

Record clean footage with the plane at realistic distances, against different backgrounds and lighting. Keep negative examples with no plane. Split by recording/session so nearby video frames do not leak between training and validation/test sets. Sample enough changes in pose, distance and background; 50 nearly identical frames each second do not provide 50 independent training examples.

Pi clips are recorded by `camera_recording.py` under `data/recordings/`. Studio downloads completed clips through `video_collection.py` and samples them with `import_video.py`. This is separate from `flight_recorder.py`, which records control commands into SQLite.

Labels and source images live in laptop `data/plane-target/`. Dataset exports are snapshots under `exports/<id>/`; they preserve session splits. Editing library labels later does not modify an export already being used by a training run.

## Training controls

| Setting | Meaning |
|---|---|
| Starting model | Initial YOLO weights; model size changes compute cost and capacity |
| Epochs | Maximum passes over the training dataset; early stopping can finish sooner |
| Batch | Images per optimizer step; larger values use more GPU memory and can change optimization, not automatically reduce accuracy |
| Detector input | Square model input size used for training/export/compilation; independent of camera capture resolution |
| Training images: full frames | Train on resized full images, matching current full-frame flight inference |
| Training images: native crops + full frames | Add native-pixel crops while retaining full frames and source splits; intended for validating crop-based detection |
| Training output: YOLO | Keep standard `weights/best.pt`, without HEF compilation |
| Training output: YOLO + Hailo | Keep `best.pt` and additionally export ONNX, calibrate/quantize and compile a HEF |
| Compute device | Auto uses CUDA if available; CPU is supported but slower |

`train_yolo.py` uses seed 42, deterministic training, patience 25 and closes mosaic augmentation for the final 10 epochs. Default CLI settings are YOLOv8s, 100 epochs, batch 8 and input 640. The UI exposes the choices; changing resolution or batch alone does not repair missing or incorrect labels.

The Hailo export path accepts input sizes 320-1920 divisible by 32. This is a software validation range, **not a promise that every model/size compiles or meets a flight latency target**. The compiled HEF has a fixed input shape. Selecting another preview size cannot resize an existing HEF's network.

Compilation needs the configured `pi-vision-hailo-compiler` Docker container and representative training calibration images. Files go under `runs/plane-target/<run>/`: weights in `weights/`, HEF in `compiled/` for 640 or `compiled-<size>/` for other sizes. Compilation does not deploy or activate the result.

Existing older outputs now follow the same folder convention. See [MODEL_STORAGE.md](MODEL_STORAGE.md) for the three compiled HEFs, Pi copies and archived duplicate. Refresh an already-open inspector to reload the normalized paths.

## Inspect and compare

The inspector supports standard PT weights on the laptop and compiled HEFs on the Pi.

- **Full frame** resizes the entire image to the detector input. It has one inference per observation but reduces distant-plane pixels when the source is larger.
- **Native tiles** inspect the source in crops and merge duplicate boxes. Timing includes all tiles for that image. It preserves small-object pixels at the cost of multiple inferences.
- **Confidence** filters candidate detections in the inspector. It is not a training parameter and inspection does not change the Pi's flight confidence threshold.

Compare missed planes and false positives on held-out flights at realistic distances. Do not compare laptop PT inference timing directly with Pi HEF timing. Likewise, tile throughput is not the same as complete-camera-view observations per second.

Current flight inference is **full-frame 640x640**, with 1080p50 capture. Adaptive flight crop detection is a separate experimental mode requiring a suitable crop-trained, validated 640 model. Do not turn it on merely because an inspection image looks better with native tiles.

## Test, activate and view a HEF live

1. Select the compiled HEF in the inspector.
2. Use **Test one frame on Pi** with Full frame. The Pi checks idle controls/recording, pauses the camera under a maintenance lock, runs the saved image on its NPU and restores the camera service. Selection/testing alone does not activate the HEF.
3. After a successful test, **Activate on Pi** is a separate confirmed action. It requires proof for the exact current HEF SHA; changed bytes or a studio restart require another test. Native-only inspection does not unlock activation.
4. Activation installs immutable model/label artifacts, updates the camera model override and verifies readiness and model identity, with rollback on failure. The supported studio activation path selects full-frame inference and preserves the live confidence setting.
5. **Start live Pi preview** continuously displays the active annotated stream and metrics. The selected local HEF SHA must match the loaded Pi model. It does not perform repeated one-frame inspections, activate a different model or send aircraft commands. Stop/Escape or hiding the page closes the stream.

Live metrics show actual Pi settings. Preview FPS, detection FPS and controller feedback Hz are different rates. On the latest read-only audit, capture was about 50 FPS and detection about 33 FPS, with preview capped at 15 FPS. This is an observed snapshot, not an accuracy or timing guarantee. Use `benchmark_detection.py` for a longer performance sample and labeled held-out flights for accuracy.

## Resolution decisions

Higher capture resolution retains useful distant-plane detail only if the inference path preserves it. Full-frame resizing from 1080p to 640 still shrinks the plane. Larger fixed model inputs or native crops can preserve more pixels, but their extra compute must be assessed using observation freshness and full-view coverage as well as FPS. A 120 FPS sensor mode cannot by itself make the NPU or controller produce 120 useful observations per second.

Keep the working model as a baseline. Compare trained candidates on the same held-out distant-plane footage and on the actual Pi before activation. The current active model identity belongs in [RESUME.md](RESUME.md), not in training defaults.
