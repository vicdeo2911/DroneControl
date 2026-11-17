"""Shared source deployment with staging, backups, readiness checks and rollback.

Runtime data and systemd model overrides are outside the source manifests.
"""
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import shlex

from pi_ssh import execute, password_for
from project_layout import (PI_ROOT, CAMERA_FILES, CONTROLLER_FILES, EXECUTABLE_FILES,
                            validate_source_paths)

REMOTE = PI_ROOT  # Compatibility exports; edit payloads only in project_layout.py.


def read_json(client, url):
    _, stdout, stderr = client.exec_command('curl --fail --silent --max-time 3 ' + shlex.quote(url))
    body = stdout.read()
    if stdout.channel.recv_exit_status():
        raise RuntimeError('Could not check service state: ' + stderr.read().decode())
    return json.loads(body)


def source_commands(files, stage, backup, services):
    """Build commands once for both deploy entrypoints; paths are project-relative."""
    validate_source_paths(files)
    quote = shlex.quote
    directories = {str(PurePosixPath(name).parent) for name in files}
    setup = [f'cd {quote(REMOTE)}', 'mkdir -p ' + ' '.join(
        quote(f'{base}/{directory}') for base in (stage, backup) for directory in sorted(directories))]
    python = [f'{stage}/{name}' for name in files if name.endswith('.py')]
    prepare = [f'cd {quote(REMOTE)}']
    if python:
        prepare.append('python3 -m py_compile ' + ' '.join(map(quote, python)))
    for name in files:
        prepare.append(f'if [ -f {quote(name)} ]; then cp -p {quote(name)} {quote(backup + "/" + name)}; '
                       f'else touch {quote(backup + "/" + name + ".absent")}; fi')
    service_names = ' '.join(map(quote, services))
    update = [f'cd {quote(REMOTE)}', 'systemctl stop ' + service_names]
    restore = list(update)
    for name in files:
        update.append(f'cp {quote(stage + "/" + name)} {quote(name)}')
        restore.append(f'if [ -f {quote(backup + "/" + name + ".absent")} ]; then rm -f -- {quote(name)}; '
                       f'else cp -p {quote(backup + "/" + name)} {quote(name)}; fi')
    executable=[name for name in files if name in EXECUTABLE_FILES]
    if executable:
        update.append('chmod 755 '+ ' '.join(map(quote,executable)))
    update.append('systemctl start ' + service_names)
    restore.append('systemctl start ' + service_names)
    return tuple(' && '.join(commands) for commands in (setup, prepare, update, restore))


def readiness_command(urls):
    checks = ' && '.join('curl --fail --silent --max-time 2 ' + shlex.quote(url) + ' >/dev/null' for url in urls)
    return f'for i in $(seq 1 10); do if {checks}; then exit 0; fi; sleep 1; done; exit 1'


def deploy_sources(client, files, services, urls, *, host, user, label):
    files = tuple(dict.fromkeys(files))
    validate_source_paths(files)
    for name in files:
        if not (Path(__file__).parent/name).is_file():
            raise ValueError('Missing deployment source: '+name)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    folder = f'maintenance/deployments/{label}-{stamp}'
    stage, backup = folder+'/staged', folder+'/backup'
    setup, prepare, update, restore = source_commands(files, stage, backup, services)
    if execute(client, setup):
        return 1
    with client.open_sftp() as sftp:
        for name in files:
            sftp.put(str(Path(__file__).parent / name), f'{REMOTE}/{stage}/{name}')
    if execute(client, prepare):
        return 1
    credential = password_for(host, user)
    try:
        failed = bool(execute(client, update, sudo_password=credential))
        if not failed:
            failed = bool(execute(client, readiness_command(urls), timeout=60))
    except Exception as exc:
        print('Deployment/check failed; attempting rollback: ' + str(exc))
        failed = True
    if failed:
        code = execute(client, restore, sudo_password=credential)
        raise RuntimeError('Deployment failed; ' + ('rollback failed; inspect Pi services.' if code else 'previous files restored.'))
    print('Source backup: ' + REMOTE + '/' + backup)
    return 0
