"""Check the complete range/output transactions against the measured bench setup."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pi_vision.control.gp8413_bus import GP8413Bus
from pi_vision.control.remote_control import Remote


class GP8413BusTests(unittest.TestCase):
    def test_range_settles_before_output_and_one_volt_matches_bench(self):
        events = []
        transport = Mock()
        transport.i2c_rdwr.side_effect = lambda message: events.append(message)
        bus = GP8413Bus(transport, lambda address, data: (address, list(data)))
        with patch('pi_vision.control.gp8413_bus.time.sleep', side_effect=lambda delay: events.append(delay)):
            remote = Remote(bus)
            remote._output(0, 1.0)
            remote._output(1, 1.0)
        self.assertEqual(events, [
            (0x58, [0x01, 0x00]), .01,
            (0x58, [0x02, 0x32, 0x33]),
            (0x58, [0x04, 0x32, 0x33]),
        ])

    def test_failed_range_write_prevents_output(self):
        transport = Mock()
        transport.i2c_rdwr.side_effect = OSError('Disconnected')
        bus = GP8413Bus(transport, lambda address, data: (address, list(data)))
        with self.assertRaises(OSError):
            Remote(bus)
        transport.i2c_rdwr.assert_called_once_with((0x58, [0x01, 0x00]))


if __name__ == '__main__':
    unittest.main()
