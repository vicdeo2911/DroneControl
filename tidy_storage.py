"""Plan/apply model storage cleanup without restarting services or selecting a model.

Run local scope on the laptop, Pi scope on the Pi. No recursive deletion is used.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.request import urlopen


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inside(root, path):
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Storage path leaves the project: '+str(path))
    return path


def relocate(root, source, destination):
    """Move only checked paths; never overwrite an existing destination."""
    inside(root, source)
    inside(root, destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Archive/destination already exists: '+str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)


def local_plan(root, active_run=None):
    from hailo_inspection import compiled_models
    runs = root/'runs/plane-target'
    records = compiled_models(runs, active_run)
    moves, duplicates = [], []
    for record in records:
        source = runs/record['path']
        size = record['input_size']
        if size is None:
            raise ValueError('Cannot organize HEF without input metadata: '+str(source))
        run = Path(record['path']).parts[0]
        destination = runs/run/('compiled' if size == 640 else f'compiled-{size}')/source.name
        if source != destination:
            if destination.parent.exists():
                raise ValueError('Compiler destination already exists: '+str(destination.parent))
            moves.append(dict(source=str(source.parent.relative_to(root)),
                              destination=str(destination.parent.relative_to(root))))
        for other in (runs/run).rglob('*.hef'):
            if other != source and digest(other) == record['sha256']:
                duplicates.append(str(other.relative_to(root)))
    return dict(moves=moves, duplicates=duplicates)


def apply_local(root, plan, stamp):
    # Plan and apply run in one process; verify paths before each move.
    journal = root/'maintenance/cleanup'/stamp
    journal.mkdir(parents=True, exist_ok=False)
    (journal/'local-plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    for row in plan['moves']:
        relocate(root, root/row['source'], root/row['destination'])
    for relative in plan['duplicates']:
        relocate(root, root/relative, root/'maintenance/archive'/stamp/'duplicate-hefs'/relative)


OLD_DEPLOYMENT = re.compile(
    r'\.(?:(?:autonomous|native-crop|camera|controller|all)-(?:stage|backup)-[0-9].*'
    r'|(?:camera-update|flight-logs|model-deployment|remote-decay|remote-joystick|remote-update)-[0-9].*'
    r'|activation-qa-original\.conf|native-crop-model(?:-before)?\.conf)'
    r'|app\.py\.before-training-[0-9].*')


def pi_plan(root):
    files = sorted((root/'models').glob('*.hef'))
    models = []
    for path in files:
        inside(root, path)
        sha = digest(path)
        canonical = root/'models'/('studio-'+sha+'.hef')
        models.append(dict(path=str(path.relative_to(root)), sha256=sha,
                           canonical=str(canonical.relative_to(root)), alias=path.is_symlink()))
    archives = [p.name for p in sorted(root.iterdir()) if OLD_DEPLOYMENT.fullmatch(p.name)]
    return dict(models=models, archives=archives)


def apply_pi(root, plan, stamp, sources, active_sha):
    """Caller must hold camera-operation lock and verify controls are idle."""
    journal = root/'maintenance/cleanup'/stamp
    journal.mkdir(parents=True, exist_ok=False)
    (journal/'pi-plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    for row in plan['models']:
        path, canonical = root/row['path'], root/row['canonical']
        inside(root, path)
        inside(root, canonical)
        if digest(path) != row['sha256']:
            raise ValueError('HEF changed during cleanup: '+str(path))
        if path == canonical:
            continue
        if not canonical.exists():
            # Copy then atomically replace the old path with a compatibility alias.
            # A running process can continue using its open file descriptor.
            temporary = canonical.with_suffix('.pending')
            shutil.copy2(path, temporary)
            if digest(temporary) != row['sha256']:
                raise ValueError('HEF copy verification failed.')
            temporary.replace(canonical)
        if digest(canonical) != row['sha256']:
            raise ValueError('Canonical HEF checksum mismatch.')
        if not path.is_symlink():
            alias = path.with_suffix('.alias-pending')
            alias.symlink_to(canonical.name)
            alias.replace(path)
    for name in plan['archives']:
        relocate(root, root/name, root/'maintenance/archive'/stamp/name)
    records = []
    for sha in sorted({r['sha256'] for r in plan['models']}):
        model = root/'models'/('studio-'+sha+'.hef')
        labels = model.with_suffix('.labels.txt')
        if not labels.exists() and (root/'models/plane-labels.txt').exists():
            shutil.copy2(root/'models/plane-labels.txt', labels)
        records.append(dict(sha256=sha, file=str(model.relative_to(root)),
            labels=str(labels.relative_to(root)) if labels.exists() else None,
            aliases=[r['path'] for r in plan['models'] if r['sha256']==sha and r['path']!=str(model.relative_to(root))],
            sources=[r for r in sources if r['sha256']==sha], active_at_audit=sha==active_sha))
    result = dict(generated_at=datetime.now(timezone.utc).isoformat(), models=records,
                  note='Inventory snapshot. The camera API is authoritative for the active model.')
    (root/'models/index.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    (journal/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def read_api(port, path):
    with urlopen(f'http://127.0.0.1:{port}{path}', timeout=3) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'apply'))
    parser.add_argument('--scope', choices=('local', 'pi'), required=True)
    parser.add_argument('--sources', type=Path, help='Optional JSON list of local HEF source records')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    if args.scope == 'local':
        studio = read_api(8010, '/api/training')
        if studio.get('active') is not False:
            raise ValueError('Stop training before organizing artifacts.')
        plan = local_plan(root)
        print(json.dumps(plan, indent=2))
        if args.action == 'apply':
            apply_local(root, plan, stamp)
        return
    if root != Path('/home/user/pi-vision'):
        raise ValueError('Pi cleanup must run in /home/user/pi-vision.')
    if args.action == 'plan':
        print(json.dumps(pi_plan(root), indent=2))
        return
    import fcntl
    with (root/'.camera-operation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        controls = read_api(8001, '/api/status')
        autonomy = read_api(8001, '/api/autonomous')
        recording = read_api(8000, '/api/recordings')
        camera = read_api(8000, '/api/status')
        if (controls.get('enabled') is not False or controls.get('initializing') is not False
                or autonomy.get('active') is not False
                or recording.get('current', {}).get('active') is not False or not camera.get('ready')):
            raise ValueError('Stop controls/recording and wait for the camera before cleanup.')
        sources = json.loads(args.sources.read_text()) if args.sources else []
        print(json.dumps(apply_pi(root, pi_plan(root), stamp, sources, camera['model_sha256']), indent=2))


if __name__ == '__main__':
    main()
