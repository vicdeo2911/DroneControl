#!/usr/bin/env python3
"""Expose the Pi remote at localhost so browsers can read a laptop joystick.

Run on the laptop: python joystick_tunnel.py
Requires paramiko; on Windows, keyring reads the saved Windows credential.
"""
import argparse
import getpass
import select
import socketserver
import sys

import paramiko


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.137.190')
    parser.add_argument('--user', default='user')
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    if sys.platform == 'win32':
        from pi_ssh import password_for
        password = password_for(args.host, args.user)
    else:
        password = getpass.getpass('Pi SSH password: ') if sys.stdin.isatty() else sys.stdin.readline().rstrip('\r\n')
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # Reject unknown host keys; connect with SSH first to trust a new Pi.
    client.connect(args.host, username=args.user, password=password,
                   look_for_keys=False, allow_agent=False, timeout=10)
    password = None
    transport = client.get_transport()
    transport.set_keepalive(15)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            channel = None
            try:
                channel = transport.open_channel('direct-tcpip', ('127.0.0.1', 8001), self.client_address, timeout=5)
                while True:
                    readable, _, _ = select.select([self.request, channel], [], [], 30)
                    if self.request in readable:
                        data = self.request.recv(65536)
                        if not data:
                            break
                        channel.sendall(data)
                    if channel in readable:
                        data = channel.recv(65536)
                        if not data:
                            break
                        self.request.sendall(data)
            except (OSError, EOFError, paramiko.SSHException):
                pass  # Browser errors trigger the remote's existing stop/watchdog behavior.
            finally:
                if channel is not None:
                    channel.close()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        with Server(('127.0.0.1', args.port), Handler) as server:
            print(f'Joystick page: http://localhost:{args.port}/joystick', flush=True)
            server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == '__main__':
    main()
