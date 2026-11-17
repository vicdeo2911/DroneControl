#!/usr/bin/env bash
# Run on the Raspberry Pi, from any directory. Keep native camera/NPU packages in apt.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
if [[ $(uname -m) != aarch64 ]]; then
  echo 'This demo needs 64-bit Raspberry Pi OS on a Raspberry Pi 5.' >&2
  exit 1
fi
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  dkms hailo-all python3-hailort python3-picamera2 python3-opencv \
  python3-flask python3-venv rpicam-apps
python3 -m venv --system-site-packages .venv
echo 'Setup complete. After a first Hailo driver installation, reboot, then run bash scripts/run-camera.sh.'
