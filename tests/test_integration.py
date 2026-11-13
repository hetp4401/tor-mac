"""Exercise the deployed Tor handlers/helpers without starting torrent services."""
import ast
import base64
import concurrent.futures
import hashlib
import hmac
import http.client
import json
import math
from pathlib import Path
import queue
import re
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
KEY = '0123456789abcdef'
OTHER_KEY = 'fedcba9876543210'
DATA = bytes(range(256)) * 8
WORKER = 'https://mac-1.example.test'


def load():
    names = {'mac_source', 'mac_source_grant', 'mac_source_workers', 'mac_probe_source', 'mac_call', 'mac_race', 'mac_audio_source', 'audio_make', 'audio_run', 'Handler'}
    tree = ast.parse((HERE.parent / 'runners' / 'tor.py').read_text())
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in selected} == names
    reads = []
    ns = dict(globals())
    ns.update({
        'SEAL': b'test-only-random-source-signing-secret', 'REPLICA': 2,
        'MAC_SOURCE_TTL': 600, 'MAC_INPUT_MODE': 'source', 'MAC_KEY': 'test-worker-key',
        'MAC_WAIT': {0: 30, 1: 60}, 'CHUNK_KEY': 'test-legacy-fleet-key',
        'SRC_BLOCK': 512, 'SRC_LEAD': 4, 'JUMP_ALONE': 10, 'PIECE_S': 1.0,
        'src_meta': {k: {'size': len(DATA), 'duration': 100} for k in (KEY, OTHER_KEY)},
        'films': {}, 'src_waits': {}, 'mac_source_stats': {'requests': 0, 'bytes': 0, 'probes': 0, 'conversions': 0, 'audio': 0},
        'src_lookup': lambda key: {'size': len(DATA)}, 'land_part': lambda *args: None,
        'wanted_block': lambda *args: False, 'url_of': lambda replica: f'https://tor-{replica}.example.test',
        'land_now': lambda *args: None, 'piece_urgent': lambda key, n, urgent: urgent,
        'mac_lock': threading.Lock(), 'mac_room': lambda u: True,
    })
    def block(key, b, urgent, n):
        reads.append((key, b, urgent, n))
        return DATA[b * 512:(b + 1) * 512]
    ns['src_block'] = block
    ns['source_reads'] = reads
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(HERE.parent / 'runners' / 'tor.py'), 'exec'), ns)
    return ns


class SourceEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), cls.ns['Handler'])
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.ns['source_reads'].clear()

    def request(self, path=None, method='GET', headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            conn.request(method, path or '/mac-source/' + KEY, headers=headers or {})
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            conn.close()

    def grant(self, key=KEY, piece=None, urgent=True):
        return self.ns['mac_source'](key, piece, urgent)['headers']

    def test_missing_auth_denied_before_source_read(self):
        status, _, _ = self.request()
        self.assertEqual(status, 403)
        self.assertFalse(self.ns['source_reads'])

    def test_full_and_closed_range_reads(self):
        status, headers, data = self.request(headers=self.grant())
        self.assertEqual((status, data), (200, DATA))
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertEqual(headers['Cache-Control'], 'private, no-store')
        status, headers, data = self.request(headers={**self.grant(), 'Range': 'bytes=510-519'})
        self.assertEqual((status, data), (206, DATA[510:520]))
        self.assertEqual(headers['Content-Range'], f'bytes 510-519/{len(DATA)}')
        self.assertEqual(headers['Content-Length'], '10')

    def test_suffix_and_open_ranges(self):
        for requested, start in [('bytes=-7', len(DATA) - 7), ('bytes=2040-', 2040), ('bytes=-9999', 0)]:
            with self.subTest(requested=requested):
                status, headers, data = self.request(headers={**self.grant(), 'Range': requested})
                self.assertEqual((status, data), (206, DATA[start:]))
                self.assertEqual(headers['Content-Range'], f'bytes {start}-{len(DATA)-1}/{len(DATA)}')

    def test_invalid_ranges_do_not_read_source(self):
        for requested in ['bytes=9-2', 'bytes=2048-', 'bytes=-0', 'bytes=-', 'bytes=0-1,3-4', 'not-a-range', 'bytes=0-1garbage']:
            with self.subTest(requested=requested):
                status, headers, data = self.request(headers={**self.grant(), 'Range': requested})
                self.assertEqual((status, data), (416, b''))
                self.assertEqual(headers['Content-Range'], f'bytes */{len(DATA)}')
        self.assertFalse(self.ns['source_reads'])

    def test_head_has_correct_metadata_and_no_reads(self):
        status, headers, data = self.request(method='HEAD', headers={**self.grant(), 'Range': 'bytes=10-19'})
        self.assertEqual((status, data), (206, b''))
        self.assertEqual(headers['Content-Length'], '10')
        self.assertFalse(self.ns['source_reads'])

    def test_token_cannot_read_other_source_or_legacy_route(self):
        for path in ['/mac-source/' + OTHER_KEY, '/src/' + KEY]:
            with self.subTest(path=path):
                status, _, _ = self.request(path, headers=self.grant())
                self.assertEqual(status, 403)
        self.assertFalse(self.ns['source_reads'])

    def test_expired_tampered_and_other_replica_tokens_denied(self):
        now = time.time()
        with patch('time.time', return_value=now - 601):
            expired = self.grant()
        good = self.grant()['Authorization']
        bad = good[:-1] + ('0' if good[-1] != '0' else '1')
        old_replica = self.ns['REPLICA']
        self.ns['REPLICA'] = old_replica + 1
        other = self.grant()
        self.ns['REPLICA'] = old_replica
        for headers in [expired, {'Authorization': bad}, other]:
            status, _, _ = self.request(headers=headers)
            self.assertEqual(status, 403)
        self.assertFalse(self.ns['source_reads'])

    def test_read_priority_and_piece_are_scoped_in_grant_not_query(self):
        status, _, data = self.request('/mac-source/' + KEY + '?u=1&n=999',
                                      headers={**self.grant(piece=5, urgent=False), 'Range': 'bytes=0-3'})
        self.assertEqual((status, data), (206, DATA[:4]))
        self.assertEqual(self.ns['source_reads'], [(KEY, 0, False, 5)])

    def test_legacy_source_auth_still_works(self):
        status, _, data = self.request('/src/' + KEY, headers={'X-Tor-Key': self.ns['CHUNK_KEY'], 'Range': 'bytes=1-3'})
        self.assertEqual((status, data), (206, DATA[1:4]))


class ClientContractTests(unittest.TestCase):
    def setUp(self):
        self.ns = load()

    def run_call(self, body, cls=0, error=None):
        call = {'u': WORKER, 'cls': cls}
        state = {'inflight': 1, 'readied': int(cls > 0), 'calls': [call]}
        self.ns['macs'] = {WORKER: state}
        self.ns['mac_source_stats']['conversions'] = 0
        requests = []
        class Connection:
            def request(self, *args, **kwargs):
                requests.append((args, kwargs))
                if error:
                    raise error
                return 200, {}, b'converted'
        self.ns['conns'] = Connection()
        job = {'news': queue.Queue()}
        self.ns['mac_call'](call, 't=63&l=1', body, job)
        return requests, state, job['news'].get_nowait(), call

    def test_source_protocol_carries_original_audio_and_priority(self):
        source = self.ns['mac_source'](KEY, 63)
        body = {'source': source, 'start': 63.5, 'duration': 1, 'source_start': .5, 'audio': 3,
                'hdr': 1, 'width': 3840, 'segment_seconds': 0, 'priority': 1}
        requests, state, reply, call = self.run_call(body)
        args, kwargs = requests[0]
        self.assertEqual(args, ('POST', WORKER + '/convert'))
        payload = json.loads(kwargs['body'])
        self.assertEqual(payload['audio'], 3)
        self.assertEqual(payload['priority'], 0)
        self.assertEqual(payload['start'] - payload['source_start'], 63)
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/json')
        self.assertEqual(call['input_bytes'], len(kwargs['body']))
        self.assertEqual((state['inflight'], state['readied'], state['calls']), (0, 0, []))
        self.assertEqual(reply[1:], (200, b'converted'))

    def test_binary_protocol_is_unchanged(self):
        requests, state, reply, _ = self.run_call(b'original-mkv', cls=1)
        args, kwargs = requests[0]
        self.assertEqual(args, ('POST', WORKER + '/convert?t=63&l=1&p=1'))
        self.assertEqual(kwargs['body'], b'original-mkv')
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/octet-stream')
        self.assertEqual(state['readied'], 0)

    def test_failed_request_releases_slot_and_notifies_job(self):
        _, state, reply, _ = self.run_call({'source': self.ns['mac_source'](KEY)}, error=TimeoutError('test'))
        self.assertEqual((state['inflight'], state['readied'], state['calls']), (0, 0, []))
        self.assertIsNone(reply[1])
        self.assertIn(b'TimeoutError', reply[2])

    def test_worker_selection_rejects_legacy_and_draining(self):
        self.ns['macs'] = {
            'source': {'up': True, 'health': {'capabilities': ['source-v1']}},
            'legacy': {'up': True, 'health': {}},
            'draining': {'up': True, 'health': {'capabilities': ['source-v1'], 'draining': True}},
            'down': {'up': False, 'health': {'capabilities': ['source-v1']}},
        }
        self.ns['mac_check'] = lambda: list(self.ns['macs'])
        self.assertEqual(self.ns['mac_source_workers'](), ['source'])
        self.ns['MAC_INPUT_MODE'] = 'legacy'
        self.assertEqual(self.ns['mac_source_workers'](), [])

    def test_unknown_worker_is_probed_before_selection(self):
        self.ns['macs'] = {'new': {'up': True, 'health': None}}
        self.ns['mac_check'] = lambda: ['new']
        def look(u):
            self.ns['macs'][u]['health'] = {'capabilities': ['source-v1']}
        self.ns['mac_look'] = look
        self.assertEqual(self.ns['mac_source_workers'](), ['new'])

    def test_source_job_cancels_losing_call_when_output_already_cached(self):
        call = {'u': WORKER, 'over': False, 't': time.time(), 'cls': 0}
        job = {'calls': [call], 'pieces': [0], 'cls': 0, 'dropped': False,
               'data': None, 'done': threading.Event()}
        cancelled = []
        self.ns.update({
            'probed': {KEY: {'start': 0, 'hdr': 0, 'w': 1920}},
            'mac_check': lambda: [WORKER], 'MAC_URLS': [WORKER],
            'mac_source_workers': lambda: [WORKER],
            'piece_cut': lambda key, piece: (0, 1, None, None),
            'muxed': lambda info: -1, 'piece_has': lambda key, piece: True,
            'end_call': lambda call: cancelled.append(call), 'mac_jobs': {(KEY, 0): job},
            'mac_failed': {'n': 0}, 'Dropped': type('Dropped', (Exception,), {}),
        })
        self.ns['mac_race'](KEY, 0, job)
        self.assertEqual(cancelled, [call])
        self.assertTrue(job['done'].is_set())
        self.assertNotIn((KEY, 0), self.ns['mac_jobs'])

    def test_probe_skips_busy_workers(self):
        self.ns['mac_source_workers'] = lambda: [WORKER]
        self.ns['mac_load'] = lambda u: 0
        self.ns['mac_room'] = lambda u: False
        class Connection:
            def request(self, *args, **kwargs):
                raise AssertionError('busy worker must not be called')
        self.ns['conns'] = Connection()
        self.assertIsNone(self.ns['mac_probe_source'](KEY))

    def test_probe_retries_share_one_deadline(self):
        workers = [WORKER, WORKER + '-2', WORKER + '-3']
        self.ns['mac_source_workers'] = lambda: workers
        self.ns['mac_load'] = lambda u: 0
        elapsed, calls = [0], []
        class Clock:
            def monotonic(self):
                return elapsed[0]
            def time(self):
                return time.time()
        self.ns['time'] = Clock()
        class Connection:
            def request(self, *args, **kwargs):
                calls.append(kwargs['timeout'])
                elapsed[0] += 31
                raise TimeoutError('fixture timeout')
        self.ns['conns'] = Connection()
        self.assertIsNone(self.ns['mac_probe_source'](KEY))
        self.assertEqual(calls, [30, 29])

    def test_probe_contract_returns_metadata_and_keyframes(self):
        self.ns['mac_source_workers'] = lambda: [WORKER]
        self.ns['mac_load'] = lambda u: 0
        calls = []
        expected = {'format': {'duration': '100'}, 'streams': [], 'keyframes': [[0, 100, 0], [2, 400, 0]]}
        class Connection:
            def request(self, *args, **kwargs):
                calls.append((args, kwargs))
                return 200, {}, json.dumps(expected).encode()
        self.ns['conns'] = Connection()
        self.assertEqual(self.ns['mac_probe_source'](KEY), expected)
        self.assertEqual(calls[0][0], ('POST', WORKER + '/probe'))
        self.assertEqual(json.loads(calls[0][1]['body'])['source']['url'], 'https://tor-2.example.test/mac-source/' + KEY)
        self.assertEqual(self.ns['mac_source_stats']['probes'], 1)


class AudioContractTests(unittest.TestCase):
    def setUp(self):
        self.ns = load()
        self.calls = []
        self.ns.update({
            'probed': {KEY: {'start': 1.5, 'cuts': [0, 1, 2], 'ocuts': [0, 2]}},
            'macs': {WORKER: {'health': {'capabilities': ['source-v1', 'source-audio-v1']}, 'inflight': 0}},
            'mac_source_workers': lambda: [WORKER], 'mac_load': lambda u: 0,
            'TS_BASE': 10, 'is_ts': lambda data: data == b'good-ts',
        })
        class Connection:
            def request(inner, *args, **kwargs):
                self.calls.append((args, kwargs))
                return 200, {}, b'good-ts'
        self.ns['conns'] = Connection()

    def test_track_priority_and_time_are_preserved(self):
        self.assertEqual(self.ns['mac_audio_source'](KEY, 1, 3, False, 1), (None, b'good-ts'))
        args, kwargs = self.calls[0]
        self.assertEqual(args, ('POST', WORKER + '/audio'))
        value = json.loads(kwargs['body'])
        self.assertEqual((value['start'], value['duration'], value['source_start'], value['audio'], value['priority']), (2.5, 1, 1.5, 3, 1))
        self.assertFalse(value['original'])
        self.assertEqual(self.ns['macs'][WORKER]['inflight'], 0)

    def test_legacy_and_incapable_workers_skip_audio(self):
        self.ns['MAC_INPUT_MODE'] = 'legacy'
        self.assertIsNone(self.ns['mac_audio_source'](KEY, 0, 1, False))
        self.ns['MAC_INPUT_MODE'] = 'source'
        self.ns['macs'][WORKER]['health']['capabilities'] = ['source-v1']
        self.assertIsNone(self.ns['mac_audio_source'](KEY, 0, 1, False))
        self.assertFalse(self.calls)

    def test_audio_make_uses_remote_result_without_local_preparation(self):
        self.ns['mac_audio_source'] = lambda *args: (None, b'remote')
        self.ns['audio_source'] = lambda *args: self.fail('local audio preparation was called')
        self.assertEqual(self.ns['audio_make'](KEY, 1, 3, False), (None, b'remote'))

    def test_background_audio_uses_remote_jobs_and_keeps_piece_caches(self):
        saved = []
        self.ns['audio_make'] = lambda key, n, a, orig, mac_priority=0: (None, bytes([n, mac_priority]))
        self.ns['piece_has'] = lambda *args: False
        self.ns['piece_put'] = lambda *args: saved.append(args)
        result = self.ns['audio_run'](KEY, [0, 1], 3)
        self.assertEqual(result, {0: b'\x00\x01', 1: b'\x01\x01'})
        self.assertEqual([r[0] for r in saved], [KEY + ':ca3', KEY + ':ca3'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
