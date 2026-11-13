"""Low-cost host telemetry. Sampling never runs in a media or health request."""
import threading
import time
from gpu_metrics import GpuMetrics

try:
    import psutil
except ImportError:
    psutil = None


def delta(current, previous, seconds):
    if current is None or previous is None or seconds <= 0 or current < previous:
        return None
    return (current - previous) / seconds


def cpu_usage(current, previous):
    if not previous:
        return None
    diffs = {key: val - previous.get(key, val) for key, val in current.items() if key not in ('guest', 'guest_nice')}
    total = sum(diffs.values())
    if total <= 0 or any(value < 0 for value in diffs.values()):
        return None
    return round(max(0, min(100, (total-diffs.get('idle', 0)-diffs.get('iowait', 0))/total*100)), 1)


class HostMetrics:
    def __init__(self, provider=None, interval=5, gpu=None):
        self.provider = provider if provider is not None else psutil
        self.interval = interval
        self.gpu = gpu if gpu is not None else GpuMetrics()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.previous = None
        self.data = {'sample_at': None, 'error': 'Waiting for host telemetry' if self.provider else 'Host telemetry collector is not installed'}

    def start(self):
        with self.lock:
            if self.thread or self.provider is None:
                return
            self.thread = threading.Thread(target=self._loop, daemon=True, name='host-metrics')
            self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.sample()
            except Exception:
                # Retain the last sample and mark it stale; never fail playback.
                with self.lock:
                    self.data = {**self.data, 'error': 'Host telemetry sample failed'}
            self.stop_event.wait(self.interval)

    def sample(self, now=None, monotonic=None):
        began = time.monotonic()
        now = time.time() if now is None else now
        stamp = time.monotonic() if monotonic is None else monotonic
        p = self.provider
        if p is None:
            return self.snapshot()
        cpu = p.cpu_times()._asdict()
        cores = [row._asdict() for row in p.cpu_times(percpu=True)]
        vm = p.virtual_memory()
        disk = p.disk_usage('/')
        # APFS volumes share a container. Root-volume "used" excludes sibling
        # volumes; total minus available space describes actual disk pressure.
        disk_used = max(0, disk.total - disk.free)
        disk_percent = round(disk_used / disk.total * 100, 1) if disk.total else None
        io = p.disk_io_counters()
        # Exclude loopback and virtual interfaces so tunneled/local copies are
        # not counted twice alongside the physical host traffic.
        interfaces = {name: row for name, row in p.net_io_counters(pernic=True).items()
                      if not name.startswith(('lo', 'utun', 'tun', 'ipsec', 'awdl', 'llw', 'bridge', 'gif', 'stf', 'veth', 'docker', 'br-'))}
        current = {'at':stamp, 'cpu':cpu, 'cores':cores, 'io':io, 'net':interfaces}
        previous = self.previous
        self.previous = current
        elapsed = stamp - previous['at'] if previous else 0
        cpu_percent = cpu_usage(cpu, previous['cpu']) if previous and elapsed > 0 else None
        core_percent = [cpu_usage(core, previous['cores'][i]) if previous and elapsed > 0 and len(cores)==len(previous['cores']) else None
                        for i, core in enumerate(cores)]
        def net_rate(field):
            if not previous or elapsed <= 0:
                return None
            readings = [delta(getattr(row, field, None), getattr(previous['net'].get(name), field, None), elapsed)
                        for name, row in interfaces.items()]
            valid = [x for x in readings if x is not None]
            return round(sum(valid), 2) if valid else None
        def disk_rate(field):
            reading = delta(getattr(io, field, None), getattr(previous['io'], field, None) if previous else None, elapsed)
            return round(reading, 2) if reading is not None else None
        sample = {'sample_at':now, 'sample_interval_s':round(elapsed, 3) if previous else None, 'error':None,
                  'cpu_percent':cpu_percent, 'cpu_per_core':core_percent, 'cpus':p.cpu_count(), 'memory_used':vm.total-vm.available,
                  'memory_total':vm.total, 'memory_percent':vm.percent,
                  'disk_used':disk_used, 'disk_free':disk.free, 'disk_total':disk.total, 'disk_percent':disk_percent,
                  'disk_read':disk_rate('read_bytes'), 'disk_write':disk_rate('write_bytes'),
                  'network_rx':net_rate('bytes_recv'), 'network_tx':net_rate('bytes_sent'),
                  'network_errors_rx':net_rate('errin'), 'network_errors_tx':net_rate('errout'),
                  'network_drops_rx':net_rate('dropin'), 'network_drops_tx':net_rate('dropout'),
                  'uptime_s':max(0, now-p.boot_time())}
        sample.update(self.gpu.sample(now))
        sample['sample_cost_ms'] = round((time.monotonic()-began)*1000, 3)
        with self.lock:
            self.data = sample
        return sample
