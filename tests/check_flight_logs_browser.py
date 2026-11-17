"""Verify flight recording UI and downloads with a fake DAC only."""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flight_recorder import FlightRecorder
from remote_control import Remote, handler_for
from control_calibration import Calibration
from test_remote_control import FakeBus, PROFILE


def main():
    with tempfile.TemporaryDirectory() as folder:
        recorder = FlightRecorder(Path(folder) / 'flights.sqlite3')
        remote = Remote(FakeBus(), recorder=recorder)
        remote.calibration = Calibration(PROFILE)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(remote))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(accept_downloads=True)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/flights')
                page.locator('#emptyFlights').wait_for(state='visible')
                assert not remote.status()['enabled']
                page.goto(base + '/')
                page.wait_for_function("!document.getElementById('arm').disabled")
                assert 'records when armed' in page.locator('#recordingStatus').inner_text()
                page.locator('#arm').click()
                page.wait_for_function('token !== null && !starting && !state.initializing')
                page.wait_for_function("document.getElementById('recordingStatus').textContent.startsWith('Recording')")
                page.keyboard.down('w')
                page.wait_for_timeout(650)
                page.keyboard.up('w')
                page.locator('#stop').click()
                page.wait_for_function('!state.enabled')
                recorder.flush()
                with page.expect_download() as transfer:
                    page.locator('#latestFlight').click()
                download = transfer.value
                rows = list(csv.DictReader(io.StringIO(Path(download.path()).read_text())))
                assert any(row['event'] == 'control' and float(row['throttle_percent']) > 0 for row in rows)
                assert rows[-1]['event'] == 'stop'
                assert rows[-1]['source'] == 'keyboard'
                assert 'Stopped by pilot' in rows[-1]['reason']
                page.get_by_role('link', name='View flight logs', exact=True).click()
                page.wait_for_function("document.querySelectorAll('#flightRows tr').length === 1")
                with page.expect_download() as transfer:
                    page.get_by_role('link', name='Setup JSON', exact=True).click()
                details = json.loads(Path(transfer.value.path()).read_text())
                assert details['settings']['calibration'] == PROFILE
                assert details['samples'] == len(rows)
                # Navigating away from an armed control page must finish the log via beacon.
                page.goto(base + '/')
                page.wait_for_function("!document.getElementById('arm').disabled")
                page.locator('#arm').click()
                page.wait_for_function('token !== null && !starting && !state.initializing')
                page.get_by_role('link', name='View flight logs', exact=True).click()
                page.wait_for_function("document.querySelectorAll('#flightRows tr').length === 2")
                page.wait_for_timeout(300)
                assert not remote.status()['enabled']
                recorder.flush()
                assert 'unfocused' in recorder.list_flights()[0]['end_reason']
                page.locator('#refreshFlights').click()
                for width in [390, 800, 1280]:
                    page.set_viewport_size(dict(width=width, height=900))
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                page.goto(base + '/joystick')
                page.locator('#recordingStatus').wait_for(state='visible')
                page.wait_for_timeout(300)
                assert not remote.status()['enabled']
                assert len(recorder.list_flights()) == 2
                page.goto(base + '/calibration')
                page.wait_for_timeout(200)
                assert not errors, errors
                browser.close()
            print('Flight log browser checks passed: empty state, automatic recording, live status, '
                  'CSV/JSON downloads, metadata, stop/leave finalization, history, responsive layout, '
                  'joystick preview isolation and calibration page.')
        finally:
            server.shutdown()
            server.server_close()
            recorder.close()


if __name__ == '__main__':
    main()
