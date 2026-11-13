"""The restored player sends compact bytes to Macs and releases failed jobs."""
import ast
from pathlib import Path
import queue
import threading
import time
import unittest


class CompactMacTests(unittest.TestCase):
    def run_call(self, priority, error=None):
        worker = 'https://mac.example.test'
        call = {'u': worker, 'cls': priority}
        state = {'inflight': 1, 'readied': int(priority > 0), 'calls': [call]}
        requests = []

        class Connection:
            def request(self, *args, **kwargs):
                requests.append((args, kwargs))
                if error:
                    raise error
                return 200, {}, b'converted-video'

        source = Path(__file__).resolve().parents[1] / 'runners/tor.py'
        fn = next(n for n in ast.parse(source.read_text()).body
                  if isinstance(n, ast.FunctionDef) and n.name == 'mac_call')
        scope = {'time': time, 'conns': Connection(), 'MAC_WAIT': {0: 30, 1: 60},
                 'MAC_KEY': 'fixture-key', 'mac_lock': threading.Lock(), 'macs': {worker: state}}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), scope)
        job = {'news': queue.Queue()}
        scope['mac_call'](call, 't=63&l=1&s=0&a=-1&h=1&w=3840&k=1', b'compact-matroska', job)
        return requests, state, call, job['news'].get_nowait()

    def test_compact_bytes_and_timing_options_reach_mac_unchanged(self):
        for priority in (0, 1):
            with self.subTest(priority=priority):
                requests, state, call, reply = self.run_call(priority)
                args, options = requests[0]
                self.assertEqual(args, ('POST', 'https://mac.example.test/convert?t=63&l=1&s=0&a=-1&h=1&w=3840&k=1&p=' + str(priority)))
                self.assertEqual(options['body'], b'compact-matroska')
                self.assertEqual(options['headers'], {'X-Mac-Key': 'fixture-key', 'Content-Type': 'application/octet-stream'})
                self.assertIs(options['hold'], call)
                self.assertEqual(options['timeout'], 30 if priority == 0 else 60)
                self.assertEqual((state['inflight'], state['readied'], state['calls']), (0, 0, []))
                self.assertEqual(reply[1:], (200, b'converted-video'))

    def test_failed_mac_releases_capacity_and_notifies_fallback(self):
        _, state, _, reply = self.run_call(1, TimeoutError('fixture timeout'))
        self.assertEqual((state['inflight'], state['readied'], state['calls']), (0, 0, []))
        self.assertIsNone(reply[1])
        self.assertIn(b'TimeoutError', reply[2])


if __name__ == '__main__':
    unittest.main()
