"""Discover completed HEFs and test one image on the Pi's actual accelerator."""
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
import shlex
import uuid

from PIL import Image

REMOTE = '/home/user/pi-vision'


@lru_cache(maxsize=128)
def artifact_digest(path, size, modified):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def compiled_models(runs, active_run=None):
    runs = Path(runs).resolve()
    records, seen = [], set()
    # Prefer compiler outputs over legacy deployment copies of the same HEF.
    paths = sorted(runs.rglob('*.hef'), key=lambda p:(
        len(p.relative_to(runs).parts) != 3, 'hailo-deployment' in p.parts, p.as_posix()))
    for path in paths:
        if not path.resolve().is_relative_to(runs):
            continue
        relative = path.relative_to(runs)
        if len(relative.parts) < 2 or relative.parts[0] == active_run:
            continue
        stat = path.stat()
        if not stat.st_size:
            continue
        digest = artifact_digest(str(path),stat.st_size,stat.st_mtime_ns)
        if digest in seen:
            continue
        seen.add(digest)
        size = None
        for name,key in (('model-input.json','input_size'),('nms_config.json','image_dims')):
            metadata = path.parent/name
            if metadata.is_file():
                try:
                    value = json.loads(metadata.read_text())[key]
                    if isinstance(value,list):
                        value = value[0] if len(value)==2 and value[0]==value[1] else None
                    if type(value) is int and 320 <= value <= 1920 and value%32==0:
                        size = value
                        break
                except (ValueError,KeyError,TypeError):
                    pass
        records.append(dict(path=relative.as_posix(),input_size=size,sha256=digest,format='hef'))
    return records


def inspect_on_pi(path, content, confidence, preparation, host='192.168.137.190'):
    with Image.open(io.BytesIO(content)) as image:
        output = io.BytesIO()
        image.convert('RGB').save(output,format='PNG')
    records,job = run_on_pi(path,dict(confidence=confidence,preparation=preparation),host,
                          image=output.getvalue())
    if not isinstance(records,list) or len(records)!=1:
        raise ValueError('Pi returned an unexpected inspection result.')
    return {**records[0],'inspection_job':job}


def activate_on_pi(path, proof, host='192.168.137.190'):
    result,_ = run_on_pi(path,dict(sha256=proof['sha256'],inspection_job=proof['inspection_job'],
                                  confirmed=True),host,operation='activate')
    if not isinstance(result,dict) or result.get('activated') is not True:
        raise ValueError('Pi did not confirm model activation.')
    return result


def run_on_pi(path, options, host, image=None, operation='inspect'):
    script = {'inspect':'inspect_hailo.py','activate':'activate_hailo.py'}[operation]
    try:
        from pi_ssh import connect,password_for,execute
        credential = password_for(host,'user',prompt=False)
        client = connect(host=host,prompt=False)
    except ImportError as exc:
        raise ValueError('Install the SSH dependencies with setup-training.ps1 before testing HEFs.') from exc
    except Exception as exc:
        raise ValueError('Cannot connect to the Pi: '+str(exc)) from exc
    job = uuid.uuid4().hex
    folder = REMOTE+'/.hef-inspection/'+job
    try:
        with client.open_sftp() as sftp:
            try:
                sftp.stat(REMOTE+'/'+script)
            except FileNotFoundError as exc:
                raise ValueError('Deploy the updated camera/controller sources before testing HEFs.') from exc
            for directory in (REMOTE+'/.hef-inspection',folder):
                try:
                    sftp.mkdir(directory)
                except OSError:
                    sftp.stat(directory)
            sftp.put(str(path),folder+'/model.hef')
            if image is not None:
                sftp.mkdir(folder+'/images')
                with sftp.open(folder+'/images/frame.png','wb') as stream:
                    stream.write(image)
            with sftp.open(folder+'/request.json','w') as stream:
                stream.write(json.dumps(options))
        command = '/usr/bin/python3 '+REMOTE+'/'+script+' --job '+shlex.quote(job)
        code = execute(client,command,timeout=180,sudo_password=credential)
        with client.open_sftp() as sftp:
            if code:
                try:
                    with sftp.open(folder+'/error.json') as stream:
                        reason = json.loads(stream.read())['error']
                except (OSError,ValueError,KeyError):
                    reason = 'The HEF operation failed on the Pi. Check its camera service logs.'
                raise ValueError(reason)
            with sftp.open(folder+'/result.json') as stream:
                result = json.loads(stream.read())
        return result,job
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Compiled-model operation failed: '+str(exc)) from exc
    finally:
        # Remove only this job's large uploaded copies; retain JSON diagnostics.
        try:
            with client.open_sftp() as sftp:
                for name in ('model.hef','images/frame.png'):
                    try:
                        sftp.remove(folder+'/'+name)
                    except OSError:
                        pass
        except Exception:
            pass
        client.close()
