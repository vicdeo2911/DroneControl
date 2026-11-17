"""Source deployment: plan/status/audit are read-only; apply restarts services."""
import argparse
import hashlib
import json
from pathlib import Path

from pi_deploy import deploy_sources, read_json
from pi_ssh import connect
from project_layout import PI_ROOT, TARGETS, validate_layout

ROOT = Path(__file__).resolve().parent


def preflight(client):
    state = read_json(client, 'http://127.0.0.1:8001/api/status')
    autonomy = read_json(client, 'http://127.0.0.1:8001/api/autonomous')
    if state.get('enabled') or state.get('initializing') or autonomy.get('active'):
        raise RuntimeError('Stop aircraft controls before deployment.')
    recording = read_json(client, 'http://127.0.0.1:8000/api/recordings')
    if recording.get('current', {}).get('active'):
        raise RuntimeError('Stop camera recording before deployment.')


def deploy_target(client, target, *, host, user):
    validate_layout(ROOT)
    preflight(client)
    spec = TARGETS[target]
    return deploy_sources(client, spec.files, spec.services, spec.health_urls,
                          host=host, user=user, label=target)


def source_audit(client, files):
    """Compare disk bytes only; this does not prove which code is loaded."""
    rows = []
    with client.open_sftp() as sftp:
        for name in files:
            local = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
            try:
                with sftp.open(PI_ROOT+'/'+name, 'rb') as remote:
                    digest = hashlib.sha256()
                    while True:
                        chunk = remote.read(1024*1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                remote_hash = digest.hexdigest()
            except FileNotFoundError:
                remote_hash = None
            rows.append(dict(file=name, local_sha256=local, remote_sha256=remote_hash,
                             state='missing' if remote_hash is None else
                             'same' if remote_hash == local else 'different'))
    return rows


def service_status(client):
    result = {}
    for name, url in (
        ('camera', 'http://127.0.0.1:8000/api/status'),
        ('controls', 'http://127.0.0.1:8001/api/status'),
        ('autonomous', 'http://127.0.0.1:8001/api/autonomous'),
    ):
        try:
            result[name] = read_json(client, url)
        except Exception as exc:
            result[name] = {'error': str(exc)}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'status', 'audit', 'apply'))
    parser.add_argument('--target', choices=tuple(TARGETS), default='all')
    parser.add_argument('--host', default='192.168.137.190')
    parser.add_argument('--user', default='user')
    args = parser.parse_args(argv)
    validate_layout(ROOT)
    spec = TARGETS[args.target]
    if args.action == 'plan':
        print(json.dumps(dict(target=args.target, destination=PI_ROOT,
            files=spec.files, services=spec.services, health_urls=spec.health_urls,
            preserves='Runtime settings, recordings, logs, models and systemd overrides'), indent=2))
        return 0
    client = connect(args.host, args.user)
    try:
        if args.action == 'apply':
            return deploy_target(client, args.target, host=args.host, user=args.user)
        result = source_audit(client, spec.files) if args.action == 'audit' else service_status(client)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
