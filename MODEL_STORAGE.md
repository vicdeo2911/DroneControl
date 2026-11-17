# Model storage

There are **three unique compiled HEFs**. Copies and compatibility links are not additional trained models.

| Training run | Input | Laptop HEF | Pi status at cleanup |
|---|---|---|---|
| `20261109-022355-9ed07a` | 640 | `<run>/compiled/plane-target-h8.hef` | Older model, SHA starts `0744be9a` |
| `20261109-022355-9ed07a` | 1920 | `<run>/compiled-1920/plane-target-h8.hef` | Resolution candidate, SHA starts `92b2ec21` |
| `20261110-220047-2a98de` | 640 | `<run>/compiled/plane-target-h8.hef` | Active model, SHA starts `e6685d1b` |

## Laptop

All paths above are relative to `runs/plane-target/`. Each run has one `compiled/` output for 640 or `compiled-<size>/` for another input size. The full compiler output directory, including calibration/compiler metadata and HAR files, stays together.

Old compiler outputs were moved from nested `hailo-deployment/compiled` and `resolution-candidates/.../compiled-1920` directories to this convention. One duplicate HEF was archived outside `runs/` under `maintenance/archive/`; it no longer appears in the selector. Validation evidence remains in its original deployment folder. The older deployment CLI now finds the canonical HEF while retaining those validation paths.

Standard YOLO weights remain under `<run>/weights/`. No weights, annotations, image datasets or recording files were changed. The HEF dropdown discovers disk files dynamically; refresh the page to see normalized paths. Website controls and routes are unchanged.

## Raspberry Pi

Canonical HEFs use the existing studio activation convention:

```text
/home/user/pi-vision/
  models/
    studio-<full-sha256>.hef
    studio-<full-sha256>.labels.txt
    index.json
    README.md
  maintenance/
    archive/<cleanup-id>/      # Old source deployments and override snapshots
    cleanup/<cleanup-id>/      # Cleanup plans and inventory results
    deployments/<update-id>/   # Future source staging and backup
    model-deployments/         # Older model deployment CLI staging
    model-sources.json         # Laptop source paths/hashes at cleanup
```

`models/index.json` lists hashes, corresponding training runs/input sizes, label paths and compatibility aliases. It is an inventory snapshot; use the camera `/api/status` for current active model identity. Timestamps in Pi-generated journals use the Pi's own clock.

Old `plane-target-...hef` filenames remain as symlinks to canonical HEFs so earlier service overrides and rollback snapshots still resolve. They do not consume another HEF's worth of disk space. The running model's path and bytes did not change, and no service restart was needed.

49 historical deployment directories/files were relocated from the Pi project root to `maintenance/archive/`. Their contents were retained. `.hef-inspection/` remains in place because inspection proofs and activation diagnostics use those paths. Runtime JSON settings, locks, recordings, flight logs and active service overrides remain in place.

## Repeatable maintenance

On the laptop, with the studio running and training idle:

```powershell
.venv-training/Scripts/python.exe tidy_storage.py plan --scope local
.venv-training/Scripts/python.exe tidy_storage.py apply --scope local
```

On the Pi:

```bash
python3 tidy_storage.py plan --scope pi
sudo python3 tidy_storage.py apply --scope pi --sources maintenance/model-sources.json
```

Pi apply requires a ready camera, idle recording/controls and an exclusive camera-maintenance lock. It does not activate a HEF, restart services or delete archived files. Local apply checks that training is idle. Plans are saved before moves; existing destinations and paths outside the project are rejected. If interrupted, inspect the saved plan and current paths before retrying. The Pi cleanup helper is an operator tool copied explicitly, not part of either running web service's source payload.

To recover an old deployment backup, use its new archive path. Restoring the old root location is a checked move of that particular archived item; a source rollback must still obey the idle controls/recording requirement. Do not remove model compatibility links while historical overrides may refer to them.
