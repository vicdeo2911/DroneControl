"""Validate and activate a compiled model on the Pi, with service rollback.

Run manually on the laptop: python -m pi_vision.deployment.deploy_hailo --run RUN_ID
Uses the existing Windows Credential Manager SSH credential.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shlex

from pi_vision.deployment.pi_ssh import connect, execute, password_for


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


def compare(baseline, converted):
    by_name = {row['image']: row for row in converted}
    expected = matched = 0
    overlaps = []
    for row in baseline:
        candidates = list(by_name[row['image']]['detections'])
        for detection in row['detections']:
            expected += 1
            choices = [(iou(detection['box'], other['box']), index) for index, other in enumerate(candidates)
                       if other['label'] == detection['label']]
            if choices:
                overlap, index = max(choices)
                if overlap >= 0.5:
                    matched += 1
                    overlaps.append(overlap)
                    candidates.pop(index)
    return dict(baseline_detections=expected, matched_detections=matched,
                mean_matched_iou=round(sum(overlaps) / len(overlaps), 4) if overlaps else 0,
                passed=expected > 0 and matched >= math.ceil(expected * 0.9))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--input-size', type=int, default=640)
    parser.add_argument('--detection-mode', choices=('full', 'adaptive'), default='full')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.run):
        parser.error('Invalid run ID.')
    if not 320 <= args.input_size <= 1920 or args.input_size % 32:
        parser.error('Input size must be 320–1920 and divisible by 32.')
    if args.detection_mode == 'adaptive' and args.input_size != 640:
        parser.error('Adaptive native-pixel detection requires a 640x640 model.')
    root = Path(__file__).resolve().parents[2]
    local = root / 'runs/plane-target' / args.run / 'hailo-deployment'
    suffix = ''
    if args.input_size != 640:
        local = root / 'runs/plane-target' / args.run / 'resolution-candidates' / str(args.input_size) / 'hailo-deployment'
        suffix = '-' + str(args.input_size)
    hef = local / 'compiled/plane-target-h8.hef'
    canonical = root / 'runs/plane-target' / args.run / (
        'compiled' if args.input_size == 640 else f'compiled-{args.input_size}') / 'plane-target-h8.hef'
    if canonical.is_file():
        hef = canonical
    baseline = json.loads((local / 'validation/baseline.json').read_text())
    if not baseline or not hef.is_file():
        parser.error('Compile the model and prepare validation/baseline.json first.')
    remote = '/home/user/pi-vision'
    staging = remote + '/maintenance/model-deployments/' + args.run + suffix
    validation = remote + '/data/model-validation/' + args.run + suffix
    model_digest = hashlib.sha256(hef.read_bytes()).hexdigest()
    model = remote + '/models/studio-' + model_digest + '.hef'
    labels = remote + '/models/studio-' + model_digest + '.labels.txt'
    override = '/etc/systemd/system/pi-vision.service.d/50-plane-target.conf'
    quote = shlex.quote
    client = connect()

    def capture(command):
        _, stdout, stderr = client.exec_command(command, timeout=30)
        content, error = stdout.read(), stderr.read()
        if stdout.channel.recv_exit_status():
            raise RuntimeError(error.decode(errors='replace'))
        return content

    credential = password_for('192.168.137.190', 'user')

    def sudo(command):
        if execute(client, command, timeout=180, sudo_password=credential):
            raise RuntimeError('Pi command failed: ' + command)

    try:
        controls = json.loads(capture('curl -fsS http://127.0.0.1:8001/api/status'))
        autonomy = json.loads(capture('curl -fsS http://127.0.0.1:8001/api/autonomous'))
        if controls.get('enabled') or controls.get('initializing') or autonomy.get('active'):
            raise RuntimeError('Stop all aircraft controls before model deployment.')
        state = json.loads(capture('curl -fsS http://127.0.0.1:8000/api/recordings'))
        if state.get('current', {}).get('active'):
            raise RuntimeError('Finish the active camera recording before deployment.')
        with client.open_sftp() as sftp:
            for directory in (remote + '/models', remote + '/maintenance',
                              remote + '/maintenance/model-deployments',
                              remote + '/data/model-validation', staging, validation):
                try:
                    sftp.stat(directory)
                except FileNotFoundError:
                    sftp.mkdir(directory)
            try:
                existing = capture('sha256sum ' + quote(model)).decode().split()[0]
            except RuntimeError:
                existing = None
            if existing is not None and existing != model_digest:
                raise RuntimeError('Stored model checksum mismatch.')
            if existing is None:
                sftp.put(str(hef), model)
            sftp.put(str(root / 'config/plane-labels.txt'), labels)
            # Model updates require a current source deployment.
            sftp.stat(remote + '/pi_vision/maintenance/verify_hailo.py')
            for path in (local / 'validation').glob('*.jpg'):
                sftp.put(str(path), validation + '/' + path.name)
            previous = None
            try:
                with sftp.open(override, 'rb') as stream:
                    previous = stream.read()
            except FileNotFoundError:
                pass
            if previous is not None:
                with sftp.open(staging + '/override.before', 'wb') as stream:
                    stream.write(previous)
            with sftp.open(staging + '/service.before.txt', 'wb') as stream:
                stream.write(capture('systemctl cat pi-vision'))
            config = '[Service]\nExecStart=\nExecStart=' + remote + '/scripts/run-camera.sh --host 0.0.0.0 --port 8000 --camera 0 --fps 50 --width 1920 --height 1080 --detection-mode ' + args.detection_mode + ' --preview-fps 15 --model ' + model + ' --labels ' + labels + '\n'
            with sftp.open(staging + '/model.conf', 'w') as stream:
                stream.write(config)
        digest = capture('sha256sum ' + quote(model)).decode().split()[0]
        if digest != hashlib.sha256(hef.read_bytes()).hexdigest():
            raise RuntimeError('Uploaded model checksum mismatch.')
        print(capture('hailortcli parse-hef ' + quote(model)).decode(), flush=True)
        try:
            sudo('systemctl stop pi-vision')
            command = 'cd ' + remote + ' && .venv/bin/python -m pi_vision.maintenance.verify_hailo --model ' + quote(model) + ' --images ' + quote(validation) + ' --output ' + quote(validation + '/hef-results.json') + ' --imgsz ' + str(args.input_size)
            if execute(client, command, timeout=180):
                raise RuntimeError('NPU validation failed.')
        finally:
            sudo('systemctl start pi-vision')
        with client.open_sftp() as sftp:
            sftp.get(validation + '/hef-results.json', str(local / 'validation/hef-results.json'))
        converted = json.loads((local / 'validation/hef-results.json').read_text())
        comparison = compare(baseline, converted)
        (local / 'validation/comparison.json').write_text(json.dumps(comparison, indent=2))
        print('Validation:', json.dumps(comparison), flush=True)
        if not comparison['passed']:
            raise RuntimeError('Conversion retained fewer than 90% of baseline detections at IoU >= 0.5; original model remains selected.')
        try:
            sudo('install -d /etc/systemd/system/pi-vision.service.d && install -m 644 ' + quote(staging + '/model.conf') + ' ' + quote(override) + ' && systemctl daemon-reload && systemctl restart pi-vision')
            check = (
                'import json,time,urllib.request\n'
                's={}\n'
                'for attempt in range(20):\n'
                '    time.sleep(1)\n'
                '    try:\n'
                "        s=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/status',timeout=1))\n"
                '    except OSError:\n'
                '        continue\n'
                "    if s.get('ready') and s.get('model')==" + repr(Path(model).name) + ":\n"
                '        break\n'
                'print(json.dumps(s))\n'
                "assert s.get('ready') and s.get('model')==" + repr(Path(model).name) + " and s.get('architecture')=='HAILO8',s\n"
            )
            status = capture(remote + '/.venv/bin/python -c ' + quote(check))
            (local / 'deployed-status.json').write_bytes(status)
            print(status.decode(), flush=True)
        except BaseException:
            restore = ('install -m 644 ' + quote(staging + '/override.before') + ' ' + quote(override)) if previous is not None else ('rm -f -- ' + quote(override))
            sudo(restore + ' && systemctl daemon-reload && systemctl restart pi-vision')
            raise
        print('Deployed. Open http://192.168.137.190:8000/', flush=True)
    finally:
        client.close()


if __name__ == '__main__':
    main()
