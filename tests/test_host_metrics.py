"""Host telemetry must be cheap, honest about missing data, and off request paths."""
from collections import namedtuple
from types import SimpleNamespace as N
import threading
import time
import unittest

from host_metrics import HostMetrics, delta
from gpu_metrics import GpuMetrics

Cpu = namedtuple('Cpu', 'user system idle')


class Provider:
    def __init__(self):
        self.tick = 0
        self.fail = False

    def cpu_times(self, percpu=False):
        if self.fail:
            raise OSError('unavailable')
        total = Cpu(100 + self.tick * 5, 50 + self.tick * 5, 500 + self.tick * 30)
        return [Cpu(100+self.tick*10, 50, 500+self.tick*30), Cpu(100, 50, 500+self.tick*40)] if percpu else total

    def virtual_memory(self):
        return N(total=1000, available=700, percent=30)

    def disk_usage(self, path):
        return N(total=2000, used=500, free=1500, percent=25)

    def disk_io_counters(self):
        return N(read_bytes=1000 + self.tick * 100, write_bytes=2000 + self.tick * 200)

    def net_io_counters(self, pernic):
        def nic(multiplier):
            return N(bytes_recv=1000 + self.tick * 100 * multiplier, bytes_sent=2000 + self.tick * 50 * multiplier,
                     errin=self.tick, errout=0, dropin=0, dropout=self.tick * 2)
        return {'en0': nic(1), 'lo0': nic(100), 'utun1': nic(100), 'awdl0': nic(100)}

    def cpu_count(self):
        return 4

    def boot_time(self):
        return 100


class HostMetricsTests(unittest.TestCase):
    def setUp(self):
        self.p = Provider()
        self.h = HostMetrics(self.p, gpu=GpuMetrics(platform='test'))

    def test_first_sample_has_capacity_but_no_invented_rates(self):
        row = self.h.sample(now=1000, monotonic=10)
        self.assertEqual(row['memory_used'], 300)
        self.assertEqual(row['memory_percent'], 30)
        self.assertEqual(row['disk_free'], 1500)
        self.assertEqual(row['uptime_s'], 900)
        for key in ('cpu_percent', 'network_rx', 'network_tx', 'disk_read', 'disk_write'):
            self.assertIsNone(row[key])

    def test_rates_use_elapsed_time_and_exclude_duplicate_tunnel_traffic(self):
        self.h.sample(now=1000, monotonic=10)
        self.p.tick = 1
        row = self.h.sample(now=1005, monotonic=15)
        self.assertEqual(row['cpu_percent'], 25)
        self.assertEqual(row['cpu_per_core'], [25, 0])
        self.assertEqual(row['network_rx'], 20)
        self.assertEqual(row['network_tx'], 10)
        self.assertEqual(row['disk_read'], 20)
        self.assertEqual(row['disk_write'], 40)
        self.assertEqual(row['network_drops_tx'], .4)
        self.assertEqual(row['sample_interval_s'], 5)

    def test_shared_apfs_capacity_counts_space_used_by_sibling_volumes(self):
        self.p.disk_usage = lambda _: N(total=1000, free=100, used=50, percent=33.3)
        row = self.h.sample(now=1000, monotonic=10)
        self.assertEqual(row['disk_used'], 900)
        self.assertEqual(row['disk_percent'], 90)

    def test_zero_traffic_is_valid_but_reset_counters_are_unknown(self):
        self.h.sample(now=1000, monotonic=10)
        row = self.h.sample(now=1005, monotonic=15)
        self.assertEqual(row['network_rx'], 0)
        self.assertEqual(row['disk_read'], 0)
        self.p.tick = -1
        row = self.h.sample(now=1010, monotonic=20)
        self.assertIsNone(row['network_rx'])
        self.assertIsNone(row['disk_read'])
        self.assertIsNone(delta(100, 10, 0))

    def test_snapshot_is_a_copy_and_never_samples(self):
        self.h.sample(now=1000, monotonic=10)
        self.p.fail = True
        snapshot = self.h.snapshot()
        snapshot['memory_used'] = 999
        self.assertEqual(self.h.snapshot()['memory_used'], 300)
        self.assertIsNone(self.h.snapshot()['error'])

    def test_collector_failure_preserves_last_sample_and_never_multiplies_threads(self):
        self.h.sample(now=1000, monotonic=10)
        self.p.fail = True
        self.h.interval = .01
        self.h.start()
        thread = self.h.thread
        self.h.start()
        self.assertIs(self.h.thread, thread)
        deadline = time.monotonic() + 1
        while not self.h.snapshot()['error'] and time.monotonic() < deadline:
            threading.Event().wait(.01)
        self.h.stop_event.set()
        thread.join(1)
        self.assertEqual(self.h.snapshot()['sample_at'], 1000)
        self.assertEqual(self.h.snapshot()['memory_used'], 300)
        self.assertEqual(self.h.snapshot()['error'], 'Host telemetry sample failed')


if __name__ == '__main__':
    unittest.main()
