"""Read Linux CPU/V3D counters and the HailoRT monitor, without root privileges."""
from collections import defaultdict
import fcntl
import math
import os
from pathlib import Path
import time


def cpu_counters(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0].startswith('cpu'):
            ticks = list(map(int, fields[1:9]))
            result[fields[0]] = (sum(ticks), ticks[3] + ticks[4])
    return result


def cpu_percent(previous, current):
    total, idle = current[0] - previous[0], current[1] - previous[1]
    return round(100 * max(0, min(total, total - idle)) / total, 1) if total > 0 else None


def parse_gpu_client(text):
    fields = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
    if fields.get('drm-driver', '').strip() != 'v3d' or 'drm-client-id' not in fields:
        return None
    engines = {}
    for key, value in fields.items():
        if key.startswith('drm-engine-') and key[11:] in ('bin', 'render', 'tfu', 'csd'):
            engines[key[11:]] = int(value.strip().split()[0])
    return fields['drm-client-id'].strip(), engines


def gpu_clients():
    # Inherited/duplicated DRM fds share one client ID. Count each client once.
    clients = {}
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            for fd in (process / 'fd').iterdir():
                try:
                    if str(fd.readlink()).startswith('/dev/dri/'):
                        parsed = parse_gpu_client((process / 'fdinfo' / fd.name).read_text())
                        if parsed:
                            clients[parsed[0]] = parsed[1]
                except (OSError, ValueError):
                    continue
        except OSError:
            continue
    return clients


def gpu_percent(previous, current, elapsed):
    engines = defaultdict(int)
    for client, counters in current.items():
        if client not in previous:
            continue
        for engine, ticks in counters.items():
            engines[engine] += max(0, ticks - previous[client].get(engine, ticks))
    return {engine: round(min(100, ticks / (elapsed * 1e9) * 100), 1)
            for engine, ticks in engines.items()} if elapsed > 0 else {}


def npu_percent(started_at):
    # HailoRT 4.23 writes a locked text file with a random suffix each run.
    # PrivateTmp isolates this service's monitor from other Hailo applications.
    candidates = []
    for path in Path('/tmp/nnc_utilization').glob('nnc_utilization*'):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for modified, path in sorted(candidates, reverse=True):
        try:
            if modified < started_at or time.time() - modified > 3:
                continue
            with path.open() as source:
                fcntl.flock(source, fcntl.LOCK_SH | fcntl.LOCK_NB)
                value = float(source.read())
                if math.isfinite(value) and 0 <= value <= 100:
                    return round(value, 1)
        except (OSError, ValueError):
            continue
    return None


class SystemMonitor:
    def __init__(self):
        self.started_at = time.time()
        self.previous_cpu = cpu_counters(Path('/proc/stat').read_text())
        self.previous_gpu = gpu_clients()
        self.previous_time = time.monotonic()
        self.previous_process = sum(os.times()[:2])

    def sample(self):
        now = time.monotonic()
        elapsed = now - self.previous_time
        cpu = cpu_counters(Path('/proc/stat').read_text())
        gpu = gpu_clients()
        process = sum(os.times()[:2])
        per_cpu = {name: cpu_percent(self.previous_cpu[name], ticks)
                   for name, ticks in cpu.items() if name in self.previous_cpu}
        engines = gpu_percent(self.previous_gpu, gpu, elapsed)
        info = {'sampled_at': time.time(), 'cpu_percent': per_cpu.pop('cpu', None), 'cpu_cores': per_cpu,
                'process_cpu_percent': round(min(100, max(0, process - self.previous_process)
                                                / max(elapsed, .001) / (os.cpu_count() or 1) * 100), 1),
                'gpu_percent': max(engines.values()) if engines else None,
                'gpu_engines': engines, 'gpu_clients': len(gpu),
                'gpu_scope': 'Busiest V3D engine across readable DRM clients',
                'npu_percent': npu_percent(self.started_at),
                'npu_source': 'HailoRT monitor', 'sample_seconds': round(elapsed, 2)}
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        total, available = (int(memory[name].split()[0]) for name in ('MemTotal', 'MemAvailable'))
        info['memory_percent'] = round((total - available) / total * 100, 1)
        self.previous_cpu, self.previous_gpu = cpu, gpu
        self.previous_time, self.previous_process = now, process
        return info
