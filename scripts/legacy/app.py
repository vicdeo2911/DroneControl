"""Deployed as root app.py for older Pi services; edit pi_vision/camera/app.py."""
from pi_vision.camera.app import main

if __name__ == "__main__":
    raise SystemExit(main())
