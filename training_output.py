"""Optional Hailo compilation using the existing local DFC container.

Copies inputs explicitly, so no assumption is made about Docker volume mappings.
Never deploys a model or changes aircraft/camera services.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

CONTAINER = 'pi-vision-hailo-compiler'


def export_onnx(model, imgsz=640):
    """Static raw-head ONNX intermediate shared by CLI export and compilation."""
    return Path(model.export(format='onnx', imgsz=imgsz, opset=13,
                             simplify=False, dynamic=False, nms=False))


def workdir(name):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', name):
        raise ValueError('Use an alphanumeric run name for Hailo compilation.')
    return '/tmp/pi-vision-' + name


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, **kwargs)


def stop_compiler(name):
    directory = workdir(name)
    script = ('import os,sys,signal; from pathlib import Path; '
              'p=Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); '
              '(p.parent/"cancelled").touch(); pid=int(p.read_text()) if p.exists() else None; '
              'cmd=Path(f"/proc/{pid}/cmdline") if pid else None; '
              'os.kill(pid,signal.SIGTERM) if cmd and cmd.exists() and '
              'sys.argv[1].rsplit("/",1)[0].encode() in cmd.read_bytes() else None')
    try:
        docker('exec', CONTAINER, 'python', '-c', script, directory + '/compiler.pid',
               capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def compile_weights(best, data, name, imgsz=640):
    from ultralytics import YOLO
    directory = workdir(name)
    data = Path(data).resolve()
    config = json.loads(data.read_text())  # Dataset.export emits JSON-compatible YAML.
    train = Path(config['path']) / config['train']
    images = sorted(p for p in train.iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png'))[:256]
    if not images:
        raise ValueError('Hailo calibration requires training images.')
    onnx = export_onnx(YOLO(str(best)), imgsz)
    output = best.parent.parent / ('compiled' if imgsz == 640 else f'compiled-{imgsz}')
    output.mkdir(exist_ok=True)
    print('Compiling Hailo-8 HEF; standard best.pt is retained even if compilation fails.', flush=True)
    with tempfile.TemporaryDirectory(prefix='pi-vision-compile-') as temporary:
        stage = Path(temporary)
        shutil.copy2(onnx, stage / 'model.onnx')
        shutil.copy2(Path(__file__).with_name('compile_hailo.py'), stage / 'compile_hailo.py')
        calibration = stage / 'calibration'
        calibration.mkdir()
        for image in images:
            shutil.copy2(image, calibration / image.name)
        docker('exec', CONTAINER, 'mkdir', '-p', directory)
        docker('cp', str(stage) + '/.', CONTAINER + ':' + directory)
        script = ('import os,runpy,sys; from pathlib import Path; '
                  'Path(sys.argv[1]+"/compiler.pid").write_text(str(os.getpid())); '
                  'sys.exit("Compilation cancelled") if Path(sys.argv[1]+"/cancelled").exists() else None; '
                  'root=sys.argv[1]; sys.argv=[root+"/compile_hailo.py", "--onnx",root+"/model.onnx", '
                  '"--calibration",root+"/calibration","--output",root+"/compiled"]; '
                  'runpy.run_path(sys.argv[0],run_name="__main__")')
        try:
            docker('exec', '-e', 'CUDA_VISIBLE_DEVICES=0', CONTAINER, 'python', '-u', '-c', script, directory)
            docker('cp', CONTAINER + ':' + directory + '/compiled/.', str(output))
        finally:
            # Keep compiler intermediates in the container for failed-run diagnosis.
            print('Compiler workspace: ' + CONTAINER + ':' + directory, flush=True)
    print('Hailo HEF ready: ' + str(output / 'plane-target-h8.hef'), flush=True)
