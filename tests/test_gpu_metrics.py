from types import SimpleNamespace as N
import unittest

from gpu_metrics import GpuMetrics, normalize


class GpuMetricsTests(unittest.TestCase):
    def test_driver_counters_keep_real_zero_and_distinguish_memory_from_allocation(self):
        row = normalize([{'name': 'AGX', 'statistics': {'Device Utilization %': 0,
                         'Renderer Utilization %': 20, 'Tiler Utilization %': 10,
                         'In use system memory': 1024, 'Alloc system memory': 4096}}], 100)
        self.assertEqual(row['gpu_state'], 'available')
        self.assertEqual(row['gpu_percent'], 0)
        self.assertEqual(row['gpu_renderer_percent'], 20)
        self.assertEqual(row['gpu_memory_used'], 1024)
        self.assertEqual(row['gpu_memory_allocated'], 4096)

    def test_missing_virtualized_or_invalid_counters_are_unavailable_not_idle(self):
        for rows in ([], [{'statistics': {}}], [{'statistics': {'Device Utilization %': 101,
                      'Renderer Utilization %': True, 'Tiler Utilization %': float('nan'), 'In use system memory': -1}}]):
            row = normalize(rows, 100)
            self.assertEqual(row['gpu_state'], 'unavailable')
            self.assertIsNone(row['gpu_percent'])
            self.assertIsNone(row['gpu_memory_used'])
        row = GpuMetrics(platform='linux').sample(100)
        self.assertEqual(row['gpu_state'], 'unavailable')

    def test_failed_reader_retains_last_sample_with_error_and_original_timestamp(self):
        reader = N(read=lambda: [{'statistics': {'Device Utilization %': 30}}])
        gpu = GpuMetrics(reader=reader, platform='darwin')
        gpu.sample(100)
        def fail():
            raise OSError('No driver access')
        reader.read = fail
        row = gpu.sample(110)
        self.assertEqual(row['gpu_state'], 'error')
        self.assertEqual(row['gpu_sample_at'], 100)
        self.assertEqual(row['gpu_percent'], 30)
        self.assertTrue(row['gpu_error'])

    def test_multiple_adapters_keep_usage_and_memory_on_the_same_device(self):
        row = normalize([{'name':'idle', 'statistics': {'Device Utilization %': 0, 'In use system memory': 500}},
                         {'name':'busy', 'statistics': {'Device Utilization %': 80, 'In use system memory': 100}}], 100)
        self.assertEqual(row['gpu_name'], 'busy')
        self.assertEqual(row['gpu_devices'], 2)
        self.assertEqual(row['gpu_percent'], 80)
        self.assertEqual(row['gpu_memory_used'], 100)
        self.assertEqual(row['gpu_reason'], 'Busiest adapter')


if __name__ == '__main__':
    unittest.main()
