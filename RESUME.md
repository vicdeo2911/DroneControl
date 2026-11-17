# Current handoff - documentation/source cleanup, 2026-11-11

## User constraints

Preserve website behavior and existing routes. Websites support development; production launch is GPIO-triggered with a pin still undecided, not ROS. Do not change calibration, runtime settings, recordings, datasets, training outputs or active models during cleanup.

## Current layout

- Three applications: Pi camera `app.py` :8000; Pi controls `remote_control.py` :8001; laptop studio `training_studio.py` :8010.
- Autonomous, calibration, joystick and flight logs are pages of the same controls service. :8002 is an optional localhost SSH tunnel, not another application.
- [WEB.md](WEB.md) maps URLs to HTML/JS/backend. [ARCHITECTURE.md](ARCHITECTURE.md) maps all root modules, data and host ownership.
- Source manifests now live only in `project_layout.py`; `deploy.py` provides plan/status/audit/apply. The older source commands are compatibility wrappers. `pi_deploy.py` owns staging/backup/restart/readiness/rollback.
- Missing payload dependencies/assets were added, including inference/monitoring, GP8413/flight logs, controller JS/CSS and shared preview. `run.sh` is installed executable. Runtime state/data is explicitly excluded.
- Source deployment, first-time setup and HEF activation are separate workflows. See [DEPLOYMENT.md](DEPLOYMENT.md).
- Original five guides were archived byte-for-byte in `docs/history/2026-11-11/`. They contain historical contradictions and are not operating instructions.

## Latest read-only service audit

Pi is reachable at `user@192.168.137.190`; credentials are in Windows Credential Manager and must not be written to notes. Trusted SSH host keys are required.

Camera ready, capture 1920x1080 at configured 50 FPS; measured capture 49.9 FPS and detection 33.3 FPS at audit time. Current model input is 640 square/full frame, confidence 0.55, preview limit 15 FPS. Performance is an observation, not a guarantee.

Active HEF is `studio-e6685d1ba93ea7f394b790a10b99c88efc467c519e405e7848e618b9f806fce7.hef`, corresponding to run `20261110-220047-2a98de/compiled/plane-target-h8.hef`. Exact SHA:

```text
e6685d1ba93ea7f394b790a10b99c88efc467c519e405e7848e618b9f806fce7
```

This was user-activated. Do not revert to older models cited in archived notes.

Controls disabled, outputs_zero true; autonomous inactive; saved feedback 50 Hz. GPIO disabled/pin null/dry-run true/restore false. **0 V is full throttle on the current inverted wiring**; do not treat outputs_zero as calibrated idle.

Source audit: 34 of 35 payload files match the Pi byte-for-byte. Only fallback `plane-labels.txt` is absent; the active model uses its installed model-specific labels. No file was uploaded and no service was restarted in this cleanup. Evidence: `test-artifacts/deployment-audit-20261111.json`.

Laptop studio :8010 is idle/ready and now reports both `hef_activation: true` and `hef_live: true`. Earlier notes about its pending restart are superseded by this readback. A prior automated restart was rejected by automatic review (`blocked by policy`); do not attempt to bypass such a rejection. This cleanup did not restart the studio.

## Verification and follow-up

172 unit tests pass, including source dependency/asset completeness, runtime exclusions, offline plan and deployment preflight. Training and HEF browser regression checks also pass using temporary data and mocked Pi operations. All 15 HTML/JS/CSS assets were verified byte-identical to the pre-cleanup hashes. Cleanup changes deployment tooling and documentation, not runtime application logic.

A future explicit source deployment can use `deploy.py apply`; it has not been run for this cleanup. Use plan/audit first. For model work, retain a working baseline and compare held-out distant-plane footage; high throughput does not establish detection accuracy. The capture code still caps dimensions at 1920x1080 and FPS at 60; native 2304-wide / 120 FPS capture is not implemented.


## Pi and model storage cleanup - 2026-11-11

User additionally requested actual Pi cleanup and consistent HEF folders. Completed without restarting services or changing the active detector.

- Three unique HEFs now use laptop `<run>/compiled/` (640) or `<run>/compiled-1920/` (1920). Moved both older compiler output folders, retaining metadata/HARs; one duplicate HEF was archived outside runs. The active run path is unchanged. Studio :8010 API was checked and returns exactly these three normalized entries.
- Pi HEFs use `models/studio-<sha>.hef` and paired labels; old legacy filenames are compatibility symlinks. One physical duplicate was removed by replacing it with a verified same-content link. `models/index.json` records source run, size, labels and aliases. The current active e668 model was not moved or rewritten.
- 49 historical deployment entries were moved from the Pi root into `maintenance/archive/<cleanup-id>/`; no archive contents were deleted. Cleanup plans/results live under `maintenance/cleanup/`. Keep `.hef-inspection/` in place for proofs/diagnostics.
- Future source staging/backup uses `maintenance/deployments/`. The older model deploy CLI now reads canonical laptop outputs and writes the same SHA-named Pi convention, with staging in `maintenance/model-deployments/`.
- `tidy_storage.py` is an explicit operator maintenance CLI, copied to the Pi. Its Pi apply holds the camera maintenance lock and refuses active controls/recording. Pi symlink/idempotence regression was run in a temporary directory on the actual Pi.
- Before/after evidence: `test-artifacts/pi-storage-runtime-before.json`, `pi-storage-runtime-after.json`. Runtime Python/assets/settings hashes, service units, PIDs, camera instance, active model SHA and persisted zero-output state were verified unchanged. No enable, activation or restart command was issued.
- Pi-generated journal timestamps reflect its device clock; user-facing cleanup date is 2026-11-11. [MODEL_STORAGE.md](MODEL_STORAGE.md) is the current storage guide.

This section supersedes the earlier statement that no Pi files were uploaded/moved: that described the preceding source/docs-only cleanup. The subsequent storage cleanup copied an operator helper and reorganized only model copies, historical deployments and documentation.

Final storage verification: 177 unit tests completed (176 passed; one Windows-skipped Pi symlink test passed separately on the actual Pi). HEF browser regression passed. The running studio API returns the three normalized HEFs, and all 15 website assets still match their original hashes. Current guides and a model inventory README were copied to the Pi; previous Pi guides were retained in maintenance backups.

## Website repairs - 2026-11-11

User then requested inspection/repair of website inconsistencies. See [WEBSITE_REVIEW.md](WEBSITE_REVIEW.md) for findings, fixes, verification and limits. This subsequent task intentionally changes camera/autonomous/training assets; earlier statements about byte-identical assets apply to the storage cleanup only.

Camera API errors now display correctly; browser scheduling respects the JPEG preview limit; autonomous navigation matches other controls; training instructions/labels now match implemented defaults and extraction limits. A test fixture module-import bug was fixed so synthetic camera tests use temporary recording/settings paths instead of the laptop project paths.

Studio had stopped and was started using the normal start-training.ps1 command. Pi camera was briefly restarted through the guarded source-deployment workflow to load its repaired template; controller process was not restarted. Verified same active e668 model SHA, runtime JSON/calibration/power settings and effective service units. Autonomous HTML/JS were backed up and installed live. No hardware enable or model activation occurred. All seven live pages/assets and active HEF live-status passed read-only HTTP checks; browser interactions are tested against fake hardware.
