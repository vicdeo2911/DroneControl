from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pi_vision.control.remote_control as remote_control
class HeadlessTests(unittest.TestCase):
    def test_headless_main_does_not_construct_an_http_server(self):
        with tempfile.TemporaryDirectory() as directory:
            fcntl = MagicMock()
            with patch.dict(sys.modules, {'fcntl': fcntl}), \
                 patch.object(sys, 'argv', ['remote_control.py', '--no-web']), \
                 patch.object(remote_control, '__file__', str(Path(directory) / 'pi_vision/control/remote_control.py')), \
                 patch('pi_vision.control.gp8413_bus.open_bus') as bus, \
                 patch('pi_vision.control.remote_control.FlightRecorder'), \
                 patch('pi_vision.control.remote_control.Remote') as remote, \
                 patch('pi_vision.control.launch_input.GpioLaunch') as launch, \
                 patch('pi_vision.control.remote_control.threading.Event') as event, \
                 patch('pi_vision.control.remote_control.signal.signal'), \
                 patch('pi_vision.control.remote_control.logging.basicConfig'), \
                 patch('pi_vision.control.remote_control.threading.Thread'):
                # No hardware, real threads, GPIO or web server in this lifecycle test.
                event.return_value.wait.return_value = True
                launch.return_value.config = {'enabled': False}
                with patch('pi_vision.control.remote_control.ThreadingHTTPServer') as server:
                    remote_control.main()
                    server.assert_not_called()
                bus.return_value.close.assert_called_once()
                remote.return_value.stop.assert_called_once()
                launch.return_value.close.assert_called_once()
