"""Activate an exactly tested HEF on the Pi, with service rollback."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from inspect_hailo import preflight, read_status

ROOT = Path(__file__).resolve().parent
OVERRIDE = Path('/etc/systemd/system/pi-vision.service.d/50-plane-target.conf')


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def systemd_quote(value):
    if any(ord(c)<32 for c in value):
        raise ValueError('Unsupported control character in camera arguments.')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def replace_option(arguments, name, value):
    result, index = [], 0
    while index < len(arguments):
        current = arguments[index]
        if current == name:
            if index+1 == len(arguments):
                raise ValueError('Incomplete camera service arguments.')
            index += 2
        elif current.startswith(name+'='):
            index += 1
        else:
            result.append(current)
            index += 1
    return result+[name,value]


def current_arguments():
    pid = int(subprocess.check_output(['systemctl','show','--property=MainPID','--value','pi-vision'],text=True).strip())
    if pid <= 0:
        raise ValueError('Start the camera service before activating a model.')
    arguments = [part.decode() for part in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if part]
    for index,argument in enumerate(arguments):
        if Path(argument).name == 'app.py':
            return arguments[index+1:]
    raise ValueError('The active camera command is not recognised; activation was cancelled.')


def write_override(content):
    OVERRIDE.parent.mkdir(parents=True,exist_ok=True)
    temporary = OVERRIDE.with_suffix('.pending')
    temporary.write_bytes(content)
    os.chmod(temporary,0o644)
    temporary.replace(OVERRIDE)


def restart():
    subprocess.run(['systemctl','daemon-reload'],check=True,timeout=25)
    subprocess.run(['systemctl','reset-failed','pi-vision'],check=True,timeout=25)
    subprocess.run(['systemctl','restart','pi-vision'],check=True,timeout=25)


def wait_camera(model, shape=None):
    for _ in range(40):
        try:
            camera = read_status(8000,'/healthz')
            if (camera.get('ready') and camera.get('model') == model
                    and (shape is None or camera.get('model_input_shape') == shape)):
                return camera
        except OSError:
            pass
        time.sleep(.5)
    raise RuntimeError('Camera did not become ready with the selected model.')


def activate(job):
    if not re.fullmatch('[0-9a-f]{32}',job):
        raise ValueError('Invalid activation job.')
    folder = ROOT / '.hef-inspection' / job
    request = json.loads((folder/'request.json').read_text())
    digest, proof = request.get('sha256',''), request.get('inspection_job','')
    if (request.get('confirmed') is not True or not re.fullmatch('[0-9a-f]{64}',digest)
            or not re.fullmatch('[0-9a-f]{32}',proof)):
        raise ValueError('Test this HEF and explicitly confirm activation first.')
    records = json.loads((ROOT/'.hef-inspection'/proof/'result.json').read_text())
    if (len(records) != 1 or records[0].get('preparation') != 'full'
            or records[0].get('model_sha256') != digest or file_digest(folder/'model.hef') != digest):
        raise ValueError('HEF differs from the successfully tested file. Test it again before activation.')
    shape = records[0].get('model_input_shape')
    if (not isinstance(shape,list) or len(shape)!=3 or shape[2]!=3 or shape[0]!=shape[1]
            or type(shape[0]) is not int or not 320 <= shape[0] <= 1920 or shape[0]%32):
        raise ValueError('The tested HEF input is not supported for activation.')
    with (ROOT/'.camera-operation.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another camera operation is in progress.')
        preflight()
        before = read_status(8000,'/api/status')
        if not before.get('ready'):
            raise ValueError('Wait for the current camera service to be ready.')
        arguments = current_arguments()
        model = ROOT/'models'/('studio-'+digest+'.hef')
        labels = ROOT/'models'/('studio-'+digest+'.labels.txt')
        model.parent.mkdir(exist_ok=True)
        if model.exists():
            if file_digest(model)!=digest:
                raise ValueError('Stored model checksum mismatch. Activation was cancelled.')
        else:
            shutil.copyfile(folder/'model.hef',model)
            os.chmod(model,0o644)
            if file_digest(model)!=digest:
                raise ValueError('Copied model checksum mismatch. Activation was cancelled.')
        labels.write_text('plane\ntarget\n')
        os.chmod(labels,0o644)
        for option,value in (('--model',str(model)),('--labels',str(labels)),('--detection-mode','full')):
            arguments = replace_option(arguments,option,value)
        config = '[Service]\nExecStart=\nExecStart='+ ' '.join(systemd_quote(s) for s in [str(ROOT/'run.sh'),*arguments])+'\n'
        previous = OVERRIDE.read_bytes() if OVERRIDE.exists() else None
        if previous is not None:
            (folder/'override.before').write_bytes(previous)
        else:
            (folder/'override.absent').touch()
        changed = False
        try:
            changed = True
            write_override(config.encode())
            restart()
            camera = wait_camera(model.name,shape)
        except BaseException as exc:
            if changed:
                try:
                    if previous is None:
                        OVERRIDE.unlink(missing_ok=True)
                    else:
                        write_override(previous)
                    restart()
                    wait_camera(before['model'],before.get('model_input_shape'))
                except BaseException as restore_error:
                    raise RuntimeError('Activation failed and camera rollback needs attention. Check the Pi service logs.') from restore_error
                raise ValueError('Activation failed. The previous detector was restored.') from exc
            raise
        result = dict(activated=True,sha256=digest,model=camera['model'],model_input_shape=shape,
                      previous_model=before['model'],detection_mode='full')
        (folder/'result.json').write_text(json.dumps(result,indent=2))
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',required=True)
    args=parser.parse_args()
    def interrupted(signum,frame):
        raise RuntimeError('HEF activation interrupted.')
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    try:
        activate(args.job)
    except Exception as exc:
        if re.fullmatch('[0-9a-f]{32}',args.job):
            folder=ROOT/'.hef-inspection'/args.job
            if folder.is_dir():
                (folder/'error.json').write_text(json.dumps(dict(error=str(exc))))
        raise


if __name__=='__main__':main()
