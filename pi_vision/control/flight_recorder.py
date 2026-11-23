"""Persistent flight command logs. Disk writes run outside the control thread."""
import csv
from contextlib import closing
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import queue
import secrets
import sqlite3
import threading
import time


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class FlightRecorder:
    COLUMNS = ('timestamp_utc', 'elapsed_seconds', 'event', 'throttle_percent',
               'roll_percent', 'throttle_volts', 'roll_volts', 'sequence',
               'reason', 'initializing', 'outputs_zero')

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.pending = queue.Queue(maxsize=10000)
        self.error = None
        self.available = False
        self.active_id = None
        self.latest_id = None
        self.started = None
        self.samples = 0
        self.plane_detections = None
        self.thread = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS flights (
                        id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                        ended_at TEXT, source TEXT NOT NULL, duration REAL DEFAULT 0,
                        samples INTEGER DEFAULT 0, end_reason TEXT, settings TEXT NOT NULL,
                        plane_detections INTEGER
                    );
                    CREATE TABLE IF NOT EXISTS samples (
                        sample_id INTEGER PRIMARY KEY, flight_id TEXT NOT NULL,
                        timestamp_utc TEXT, elapsed_seconds REAL, event TEXT,
                        throttle_percent REAL, roll_percent REAL, throttle_volts REAL,
                        roll_volts REAL, sequence INTEGER, reason TEXT,
                        initializing INTEGER, outputs_zero INTEGER
                    );
                    CREATE INDEX IF NOT EXISTS sample_flight ON samples(flight_id, sample_id);
                ''')
                if 'plane_detections' not in {row['name'] for row in db.execute('PRAGMA table_info(flights)')}:
                    db.execute('ALTER TABLE flights ADD COLUMN plane_detections INTEGER')
                # A hard service/power interruption has no opportunity to write a stop event.
                db.execute('''UPDATE flights SET ended_at=COALESCE(
                    (SELECT timestamp_utc FROM samples WHERE flight_id=flights.id
                     ORDER BY sample_id DESC LIMIT 1), started_at),
                    end_reason='Service interrupted' WHERE ended_at IS NULL''')
                row = db.execute('SELECT id FROM flights ORDER BY started_at DESC, rowid DESC LIMIT 1').fetchone()
                self.latest_id = row['id'] if row else None
                db.commit()
            self.available = True
            self.thread = threading.Thread(target=self._write, daemon=True, name='flight-recorder')
            self.thread.start()
        except (OSError, sqlite3.Error) as exc:
            self.error = str(exc)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=NORMAL')
        return db

    def _enqueue(self, item):
        if not self.available:
            return False
        try:
            self.pending.put_nowait(item)
            return True
        except queue.Full:
            self.error = 'Flight recorder queue is full. Some flight data could not be saved.'
            return False

    def begin(self, source, settings):
        with self.lock:
            if not self.available:
                return None
            flight_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4)
            started_at = utc_now()
            plane_detections = 0 if source == 'autonomous' else None
            if not self._enqueue(('begin', (flight_id, started_at, source, json.dumps(settings), plane_detections))):
                return None
            self.active_id = self.latest_id = flight_id
            self.started = time.monotonic()
            self.samples = 0
            self.plane_detections = plane_detections
            return flight_id

    def update_plane_detections(self, total):
        with self.lock:
            if self.active_id and self.plane_detections is not None:
                self.plane_detections = total

    def record(self, event, values, finish=False):
        with self.lock:
            if not self.active_id:
                return
            row = dict(timestamp_utc=utc_now(), elapsed_seconds=round(time.monotonic() - self.started, 6),
                       event=event, **values)
            data = tuple(row.get(name) for name in self.COLUMNS)
            if self._enqueue(('sample', (self.active_id, data, finish, self.plane_detections))):
                self.samples += 1
            if finish:
                self.active_id = None

    def status(self):
        with self.lock:
            return dict(available=self.available, recording=bool(self.active_id) and self.available,
                        flight_id=self.active_id, latest_id=self.latest_id, samples=self.samples,
                        elapsed_seconds=round(time.monotonic() - self.started, 1) if self.active_id else 0,
                        error=self.error)

    def _write(self):
        db = None
        try:
            db = self._connect()
            while True:
                item = self.pending.get()
                try:
                    if item is None:
                        break
                    if not self.available:
                        continue
                    kind, values = item
                    with db:
                        if kind == 'begin':
                            db.execute('''INSERT INTO flights(id, started_at, source, settings, plane_detections)
                                VALUES (?, ?, ?, ?, ?)''', values)
                        else:
                            flight_id, data, finish, plane_detections = values
                            columns = ', '.join(self.COLUMNS)
                            placeholders = ', '.join('?' for _ in self.COLUMNS)
                            db.execute(f'INSERT INTO samples(flight_id, {columns}) VALUES (?, {placeholders})',
                                       (flight_id, *data))
                            db.execute('''UPDATE flights SET samples=samples+1, duration=?, plane_detections=?
                                WHERE id=?''', (data[1], plane_detections, flight_id))
                            if finish:
                                db.execute('UPDATE flights SET ended_at=?, end_reason=? WHERE id=?',
                                           (data[0], data[8], flight_id))
                except (OSError, sqlite3.Error) as exc:
                    with self.lock:
                        self.error = str(exc)
                        self.available = False
                finally:
                    self.pending.task_done()
        except (OSError, sqlite3.Error) as exc:
            with self.lock:
                self.error = str(exc)
                self.available = False
            # Unblock flush/close even if opening the writer connection failed.
            while True:
                item = self.pending.get()
                self.pending.task_done()
                if item is None:
                    break
        finally:
            if db is not None:
                db.close()

    def list_flights(self):
        if not self.available:
            return []
        try:
            with closing(self._connect()) as db:
                rows = db.execute('''SELECT id, started_at, ended_at, source, duration, samples, end_reason, plane_detections
                    FROM flights ORDER BY started_at DESC, rowid DESC LIMIT 100''').fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            self.error = str(exc)
            return []

    def download(self, flight_id, format):
        if not self.available:
            raise OSError('Flight recorder unavailable: ' + (self.error or 'not running'))
        with closing(self._connect()) as db:
            flight = db.execute('SELECT * FROM flights WHERE id=?', (flight_id,)).fetchone()
            if flight is None:
                return None
            details = dict(flight)
            details['settings'] = json.loads(details['settings'])
            if format == 'json':
                return json.dumps(details, indent=2).encode()
            output = io.StringIO(newline='')
            writer = csv.writer(output)
            writer.writerow(('flight_id', 'source', *self.COLUMNS))
            for row in db.execute('SELECT * FROM samples WHERE flight_id=? ORDER BY sample_id', (flight_id,)):
                writer.writerow((flight_id, flight['source'], *(row[name] for name in self.COLUMNS)))
            return output.getvalue().encode()

    def flush(self):
        if self.thread:
            self.pending.join()

    def close(self):
        if self.thread:
            self.pending.put(None)
            self.thread.join(timeout=5)
            self.thread = None

