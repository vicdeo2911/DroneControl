#!/usr/bin/env python3
"""Expose the Pi remote at localhost so browsers can read a laptop joystick.

Run on the laptop: python -m pi_vision.deployment.joystick_tunnel
Requires paramiko; on Windows, keyring reads the saved Windows credential.
"""
import argparse
import getpass
from http.client import HTTPResponse, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys

import paramiko


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.137.190')
    parser.add_argument('--user', default='user')
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    if sys.platform == 'win32':
        from pi_vision.deployment.pi_ssh import password_for
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

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, content_type, body):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(body)

        def reject(self, code, message):
            self.respond(code, 'application/json', json.dumps({'error': message}).encode())

        def local_host(self):
            return self.headers.get('Host') in {f'localhost:{args.port}', f'127.0.0.1:{args.port}'}

        def forward(self, body=b''):
            # Rebuild the request from an allowed method/path and a bounded JSON body.
            headers = (f'{self.command} {self.path} HTTP/1.0\r\nHost: 127.0.0.1:8001\r\n'
                       f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n')
            try:
                with transport.open_channel('direct-tcpip', ('127.0.0.1', 8001),
                                            self.client_address, timeout=5) as channel:
                    channel.settimeout(5)
                    channel.sendall(headers.encode('ascii') + body)
                    with HTTPResponse(channel) as response:
                        response.begin()
                        code = response.status
                        content_type = response.getheader('Content-Type', 'application/octet-stream')
                        payload = response.read()
            except (OSError, EOFError, HTTPException, paramiko.SSHException):
                return self.reject(502, 'Pi controller unavailable.')
            self.respond(code, content_type, payload)

        def do_GET(self):
            if not self.local_host() or self.path not in {
                    '/joystick', '/joystick.js', '/remote.js', '/remote.css', '/api/status'}:
                return self.reject(403, 'Only the local joystick page is available here.')
            self.forward()

        def do_POST(self):
            if self.path not in {'/api/enable', '/api/initialize', '/api/control', '/api/stop'}:
                return self.reject(403, 'Only manual joystick commands and stop are available here.')
            if (not self.local_host() or self.headers.get('Origin') != 'http://' + self.headers['Host']
                    or self.headers.get('Sec-Fetch-Site') == 'cross-site'
                    or self.headers.get_content_type() != 'application/json'):
                return self.reject(403, 'Use the local joystick page.')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048:
                    raise ValueError
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, RecursionError):
                return self.reject(400, 'Invalid joystick request.')
            if self.path == '/api/enable' and (data.get('source'), data.get('mode')) != ('joystick', 'normal'):
                return self.reject(403, 'Only manual joystick sessions can be started here.')
            self.forward(json.dumps(data).encode())

        def log_message(self, *args):
            pass

    try:
        with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
            print(f'Joystick page: http://localhost:{args.port}/joystick', flush=True)
            server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == '__main__':
    main()
