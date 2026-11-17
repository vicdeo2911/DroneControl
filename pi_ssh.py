"""Pi SSH using Windows Credential Manager; no password files in the project."""
import argparse
import getpass
import shlex
import sys

def credential_store():
    if sys.platform != 'win32':
        raise RuntimeError('Saved Pi credentials require Windows Credential Manager on this laptop.')
    from keyring.backends.Windows import WinVaultKeyring
    return WinVaultKeyring()


def password_for(host, user, save=False, prompt=True):
    store = credential_store()
    service = 'pi-vision:ssh:' + host
    password = None if save else store.get_password(service, user)
    if password is None:
        if not prompt:
            raise RuntimeError('Save the Pi SSH credential first: python pi_ssh.py --save-password')
        password = getpass.getpass(f'SSH password for {user}@{host}: ')
        if save:
            store.set_password(service, user, password)
            print('Saved in Windows Credential Manager for this Windows account.', flush=True)
    return password


def connect(host='192.168.137.190', user='user', save=False, prompt=True):
    import paramiko

    password = password_for(host, user, save, prompt=prompt)
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # The existing trusted SSH host key is required; do not silently trust a new Pi.
    client.connect(host, username=user, password=password, look_for_keys=False,
                   allow_agent=False, timeout=10, auth_timeout=10, banner_timeout=10)
    return client


def execute(client, command, timeout=90, sudo_password=None):
    if sudo_password is not None:
        command = "sudo -S -p '' -- sh -c " + shlex.quote(command)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if sudo_password is not None:
        stdin.write(sudo_password + '\n')
        stdin.flush()
        stdin.channel.shutdown_write()
    # These management commands have bounded output.
    output = stdout.read().decode('utf-8', errors='replace')
    errors = stderr.read().decode('utf-8', errors='replace')
    if output:
        print(output, end='', flush=True)
    if errors:
        print(errors, end='', file=sys.stderr, flush=True)
    return stdout.channel.recv_exit_status()


def deploy_camera(client, host, user):
    from deploy import deploy_target
    return deploy_target(client, 'camera', host=host, user=user)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.137.190')
    parser.add_argument('--user', default='user')
    parser.add_argument('--save-password', action='store_true')
    parser.add_argument('--deploy-camera', action='store_true')
    parser.add_argument('--sudo', action='store_true', help='Use the saved password for sudo over SSH stdin')
    parser.add_argument('--command', default='hostname; systemctl status pi-vision --no-pager; rpicam-hello --list-cameras')
    args = parser.parse_args()
    client = connect(args.host, args.user, args.save_password)
    try:
        return deploy_camera(client, args.host, args.user) if args.deploy_camera else execute(
            client, args.command, sudo_password=password_for(args.host, args.user) if args.sudo else None)
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
