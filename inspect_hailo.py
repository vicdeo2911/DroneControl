"""Pi-side single-image HEF test; restores camera inference without activation.

Run by the authenticated laptop over SSH with sudo. Aircraft outputs are never
written. A kernel lock blocks control/recording starts throughout the test.
"""
import argparse
import fcntl
import json
from pathlib import Path
import re
import signal
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


def read_status(port, endpoint):
    with urlopen(f'http://127.0.0.1:{port}{endpoint}', timeout=3) as response:
        return json.load(response)


def preflight():
    control = read_status(8001, '/api/status')
    autonomy = read_status(8001, '/api/autonomous')
    recordings = read_status(8000, '/api/recordings')
    if control.get('camera_operation_guard') is not True:
        raise ValueError('Update and restart the Pi controller service before testing HEFs.')
    if control.get('enabled') or control.get('initializing') or autonomy.get('active'):
        raise ValueError('Stop aircraft controls before testing a compiled model.')
    if recordings.get('current', {}).get('active'):
        raise ValueError('Finish the camera recording before testing a compiled model.')


def inspect(job):
    # No user-supplied paths or service names are executed.
    if not re.fullmatch('[0-9a-f]{32}', job):
        raise ValueError('Invalid inspection job.')
    folder = ROOT / '.hef-inspection' / job
    options = json.loads((folder / 'request.json').read_text())
    confidence = options.get('confidence')
    preparation = options.get('preparation')
    if type(confidence) not in (float, int) or not .05 <= confidence <= .95:
        raise ValueError('Confidence must be .05-.95.')
    if preparation not in ('full', 'adaptive'):
        raise ValueError('Unknown inspection view.')
    with (ROOT / '.camera-operation.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another camera operation is in progress.')
        preflight()
        try:
            subprocess.run(['systemctl', 'stop', 'pi-vision'], check=True, timeout=25)
            try:
                worker = subprocess.run(['/usr/sbin/runuser','-u','user','--',
                    str(ROOT / '.venv/bin/python'), str(ROOT / 'verify_hailo.py'),
                    '--model', str(folder / 'model.hef'), '--images', str(folder / 'images'),
                    '--output', str(folder / 'result.json'), '--threshold', str(confidence),
                    '--preparation', preparation], check=True, timeout=60,capture_output=True,text=True)
                (folder/'worker.log').write_text(worker.stdout+worker.stderr)
            except subprocess.CalledProcessError as exc:
                details = exc.stderr or str(exc)
                (folder/'worker.log').write_text(details)
                if 'HAILO_HEF_NOT_SUPPORTED' in details:
                    raise ValueError('The Pi rejected this HEF. Use a valid model compiled for Hailo-8.') from exc
                if 'Native tiles require' in details:
                    raise ValueError('Native tiles require a fixed 640x640 HEF. Choose Full frame for this model.') from exc
                raise ValueError('HEF inference failed. Inspection diagnostics were saved on the Pi.') from exc
            except subprocess.TimeoutExpired as exc:
                raise ValueError('HEF inference timed out. The camera service is being restored.') from exc
        finally:
            subprocess.run(['systemctl', 'start', 'pi-vision'], check=True, timeout=25)
            for _ in range(30):
                try:
                    if read_status(8000, '/healthz').get('ready'):
                        break
                except OSError:
                    pass
                time.sleep(.5)
            else:
                raise RuntimeError('HEF test ended, but the camera service did not become ready. Check pi-vision logs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise RuntimeError('HEF inspection interrupted.')
    signal.signal(signal.SIGTERM, interrupted)
    # SSH disconnect must not strand the stopped camera.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        inspect(args.job)
    except Exception as exc:
        if re.fullmatch('[0-9a-f]{32}',args.job):
            folder = ROOT / '.hef-inspection' / args.job
            if folder.is_dir():
                (folder/'error.json').write_text(json.dumps(dict(error=str(exc))))
        raise


if __name__ == '__main__':
    main()
