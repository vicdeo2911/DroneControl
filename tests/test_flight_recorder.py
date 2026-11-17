import csv
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
import threading
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pi_vision.control.flight_recorder import FlightRecorder
from pi_vision.control.remote_control import Remote, handler_for
from pi_vision.control.control_calibration import Calibration
from test_remote_control import FakeBus, PROFILE


class FlightLogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'logs' / 'flights.sqlite3'
        self.recorder = FlightRecorder(self.path)
        self.remote = Remote(FakeBus(), recorder=self.recorder)
        self.remote.calibration = Calibration(PROFILE)

    def tearDown(self):
        self.recorder.close()
        self.folder.cleanup()

    def rows(self, flight_id=None):
        self.recorder.flush()
        data = self.recorder.download(flight_id or self.recorder.latest_id, 'csv')
        return list(csv.DictReader(io.StringIO(data.decode())))

    def test_complete_flight_records_commands_sweep_and_settings_without_token(self):
        self.remote.set_limits(80, 60)
        token = self.remote.enable(source='joystick')['token']
        with patch('pi_vision.control.remote_control.time.sleep'):
            self.remote._sweep(token)
        self.remote.update(token, 0, 40, -30)
        self.remote.stop('Pilot finished flight')
        rows = self.rows()
        self.assertEqual(rows[0]['event'], 'enable')
        self.assertEqual(len([row for row in rows if row['event'] == 'initialization']), 9)
        control = next(row for row in rows if row['event'] == 'control')
        self.assertEqual(control['source'], 'joystick')
        self.assertEqual(float(control['throttle_percent']), 40)
        self.assertEqual(float(control['roll_percent']), -30)
        self.assertAlmostEqual(float(control['throttle_volts']), 1.54)
        self.assertEqual(control['sequence'], '0')
        self.assertEqual(rows[-1]['event'], 'stop')
        self.assertEqual(rows[-1]['reason'], 'Pilot finished flight')
        self.assertEqual(float(rows[-1]['throttle_volts']), .7)
        times = [float(row['elapsed_seconds']) for row in rows]
        self.assertEqual(times, sorted(times))
        details = json.loads(self.recorder.download(self.recorder.latest_id, 'json'))
        self.assertEqual(details['samples'], len(rows))
        self.assertEqual(details['settings']['calibration'], PROFILE)
        self.assertEqual(details['settings']['limits'], dict(throttle=80, roll=60))
        self.assertIsNotNone(details['ended_at'])
        self.assertNotIn(token, json.dumps(details) + json.dumps(rows))

    def test_logs_survive_restart_and_unfinished_flights_are_marked_interrupted(self):
        token = self.remote.enable()['token']
        self.remote.update(token, 0, 25, 0)
        flight_id = self.recorder.latest_id
        self.recorder.close()
        self.recorder = FlightRecorder(self.path)
        flights = self.recorder.list_flights()
        self.assertEqual(flights[0]['id'], flight_id)
        self.assertEqual(flights[0]['end_reason'], 'Service interrupted')
        self.assertIsNotNone(flights[0]['ended_at'])
        self.assertFalse(self.recorder.status()['recording'])
        self.assertEqual(self.rows(flight_id)[-1]['throttle_percent'], '25.0')

    def test_watchdog_finishes_and_rejected_commands_do_not_get_logged(self):
        token = self.remote.enable()['token']
        with self.assertRaises(ValueError):
            self.remote.update(token, 0, 101, 0)
        self.remote.update(token, 0, 20, 0)
        self.remote.last_seen -= 2
        self.remote.watchdog()
        rows = self.rows()
        self.assertEqual([row['event'] for row in rows], ['enable', 'control', 'stop'])
        self.assertIn('Connection lost', rows[-1]['reason'])
        self.assertFalse(self.recorder.status()['recording'])

    def test_power_off_records_zero_mode_and_does_not_invent_idle_percent(self):
        self.remote.enable()
        self.remote.power_off(True)
        self.remote.stop()
        rows = self.rows()
        self.assertEqual(rows[-1]['event'], 'power_off')
        self.assertEqual(rows[-1]['throttle_volts'], '0.0')
        self.assertEqual(rows[-1]['throttle_percent'], '')
        self.assertEqual(rows[-1]['outputs_zero'], '1')
        self.assertEqual(len(rows), 2)

    def test_calibration_and_status_do_not_create_flights(self):
        token = self.remote.enable(mode='calibration')['token']
        self.remote.update(token, 0, .8, 1.6)
        self.remote.stop()
        self.recorder.flush()
        self.assertEqual(self.recorder.list_flights(), [])

    def test_disk_failure_does_not_interrupt_controls_or_stop(self):
        token = self.remote.enable()['token']
        self.recorder.flush()
        with patch.object(FlightRecorder, '_connect', side_effect=sqlite3.OperationalError('disk full')):
            failed = FlightRecorder(Path(self.folder.name) / 'unavailable.sqlite3')
        self.assertFalse(failed.status()['available'])
        self.remote.recorder = failed
        self.remote.update(token, 0, 40, 0)
        self.assertAlmostEqual(self.remote.throttle, 1.54)
        self.remote.stop()
        self.assertEqual(self.remote.throttle, .7)
        self.assertEqual(failed.status()['error'], 'disk full')

    def test_recording_failure_is_reported_and_control_updates_keep_working(self):
        token = self.remote.enable()['token']
        self.recorder.flush()
        # Force the writer's next sample INSERT to fail, like a damaged/full database.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('DROP TABLE samples')
            db.commit()
        self.remote.update(token, 0, 30, 0)
        self.recorder.flush()
        self.assertFalse(self.recorder.status()['available'])
        self.assertIsNotNone(self.recorder.status()['error'])
        self.remote.update(token, 1, 40, 0)
        self.assertAlmostEqual(self.remote.throttle, 1.54)
        self.remote.stop()
        self.assertEqual(self.remote.throttle, .7)


class FlightApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.recorder = FlightRecorder(Path(self.folder.name) / 'flights.sqlite3')
        self.remote = Remote(FakeBus(), recorder=self.recorder)
        self.remote.calibration = Calibration(PROFILE)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.remote))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.recorder.close()
        self.folder.cleanup()

    def post(self, path, data):
        request = Request(self.base + '/api/' + path, json.dumps(dict(version=2, **data)).encode(),
                          {'Content-Type': 'application/json'})
        with urlopen(request) as response:
            return json.load(response)

    def test_log_assets_downloads_and_source_reason_validation(self):
        for path in ['/flights', '/flights.js']:
            with urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)
        with self.assertRaises(HTTPError):
            self.post('enable', dict(source='=bad'))
        state = self.post('enable', dict(source='joystick'))
        self.assertTrue(state['flight_log']['recording'])
        self.post('control', dict(token=state['token'], sequence=0, throttle=25, roll=0))
        with self.assertRaises(HTTPError): self.post('stop', dict(reason='=bad'))
        self.post('stop', dict(reason='joystick_disconnected'))
        self.recorder.flush()
        with urlopen(self.base + '/api/flights') as response:
            flights = json.load(response)['flights']
        self.assertEqual(len(flights), 1)
        self.assertIn('Joystick disconnected', flights[0]['end_reason'])
        flight_id = flights[0]['id']
        with urlopen(self.base + '/api/flights/' + flight_id + '.csv') as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertIn('text/csv', response.headers['Content-Type'])
            self.assertIn(b'throttle_percent', response.read())
        for path in ['/api/flights/../../remote-calibration.json', '/api/flights/20000101T000000-00000000.csv',
                     '/logs/flights.sqlite3']:
            with self.assertRaises(HTTPError) as caught: urlopen(self.base + path)
            self.assertEqual(caught.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
