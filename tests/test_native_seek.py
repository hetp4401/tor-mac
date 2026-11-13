"""Native seek preparation must stay optional, bounded and off the probe path."""
import ast
import bisect
from http.server import BaseHTTPRequestHandler
import math
from pathlib import Path
import threading
import time
import unittest


class NativeSeekTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).parents[1] / 'runners/tor.py').read_text())
        handler = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
        self.ns = dict(BaseHTTPRequestHandler=BaseHTTPRequestHandler, bisect=bisect, math=math,
                       threading=threading, time=time, REPLICA=1, CHUNK_KEY='test', ORIG=':ov', HINTS=3,
                       open_token=lambda token: ('source', 'https://source.test/video') if token == 'valid' else None,
                       src_meta={'source': {'reader': 1}}, reader_known={}, films={'source': {'done': set()}},
                       probed={'source': {'cuts': [0, 1, 2, 3, 4], 'ocuts': [0, 1.5, 3, 4]}},
                       hls_lock=threading.Lock(), hinted={}, piece_has=lambda *args: False)
        exec(compile(ast.Module(body=[handler], type_ignores=[]), '<handler>', 'exec'), self.ns)
        self.handler = self.ns['Handler'].__new__(self.ns['Handler'])
        self.responses, self.hints = [], []
        self.handler.send = lambda status, body, **kwargs: self.responses.append((status, body))
        self.handler.hls_hint = lambda *args, **kwargs: self.hints.append((args, kwargs))

    def test_converted_and_original_map_their_own_timeline(self):
        self.handler.hls_seek('valid', {'t': ['2.2']})
        self.assertEqual(self.hints[-1], (('valid', 2, {'r': ['1'], 'v': ['conv']}), {'playback': True}))
        self.handler.hls_seek('valid', {'t': ['2.2'], 'v': ['orig']})
        self.assertEqual(self.hints[-1][0][1], 1)
        self.assertEqual(self.hints[-1][0][2]['v'], ['orig'])

    def test_invalid_or_finished_times_do_not_start_work(self):
        for target in ('nan', 'inf', '-1', 'oops'):
            self.handler.hls_seek('valid', {'t': [target]})
            self.assertEqual(self.responses[-1][0], 400)
        self.handler.hls_seek('valid', {'t': ['4']})
        self.assertEqual(self.responses[-1][0], 204)
        self.assertFalse(self.hints)

    def test_unknown_metadata_or_inactive_film_does_not_probe(self):
        self.ns['probe_source'] = lambda *args: self.fail('seek hint started a source probe')
        self.ns['probed'].clear()
        self.handler.hls_seek('valid', {'t': ['2']})
        self.assertEqual(self.responses[-1][0], 204)
        self.ns['src_meta'].clear()
        self.handler.hls_seek('valid', {'t': ['2']})
        self.assertEqual(self.responses[-1][0], 204)
        self.handler.hls_seek('invalid', {'t': ['2']})
        self.assertEqual(self.responses[-1][0], 404)
        self.assertFalse(self.hints)

    def test_remote_reader_is_forwarded_without_local_probe(self):
        calls = []
        class Connections:
            def request(self, *args, **kwargs):
                calls.append((args, kwargs))
                return 202, {}, b'{}'
        self.ns.update(conns=Connections(), url_of=lambda n: f'https://frame-{n}.test')
        self.ns['src_meta']['source']['reader'] = 2
        self.ns['probed'].clear()
        self.handler.hls_seek('valid', {'t': ['2.2'], 'v': ['orig']})
        self.assertIn('/seek?t=2.200000&r=2&v=orig', calls[0][0][1])
        self.assertEqual(calls[0][1]['timeout'], 3)
        self.assertEqual(self.responses[-1][0], 202)

    def test_actual_seek_prioritizes_target_and_remains_rate_limited(self):
        del self.handler.hls_hint
        events = []
        self.ns.update(film_heard=lambda *args: events.append(('landing', args[2])),
                       mac_start=lambda key, n, priority, speculative: events.append(('mac', n, priority, speculative)) or object())
        for _ in range(4):
            self.handler.hls_seek('valid', {'t': ['2.2']})
        self.assertEqual([r[0] for r in self.responses], [202, 202, 202, 204])
        self.assertEqual(events[0:2], [('landing', 2), ('mac', 2, 0, False)])
        self.assertEqual(len(events), 6)

    def test_speculative_hint_keeps_background_priority(self):
        del self.handler.hls_hint
        events = []
        self.ns.update(film_heard=lambda *args: self.fail('hover hint moved the playback head'),
                       mac_start=lambda key, n, priority, speculative: events.append((priority, speculative)) or object())
        self.handler.hls_hint('valid', 2, {'r': ['1']})
        self.assertEqual(events, [(1, True)])

    def test_cached_video_still_prepares_cold_audio_for_actual_seek(self):
        del self.handler.hls_hint
        events = []
        class Thread:
            def __init__(self, target, args, daemon):
                self.args = args
            def start(self):
                events.append(self.args)
        self.ns.update(threading=type('Threads', (), {'Thread': Thread}), piece_has=lambda *args: True,
                       film_heard=lambda *args: events.append(('landing', args[2])),
                       quietly=object(), audio_piece='audio', orig_piece='video',
                       mac_start=lambda *args: self.fail('cached video must not be encoded again'))
        self.ns['films']['source']['audio'] = (9, True)
        self.handler.hls_seek('valid', {'t': ['2.2'], 'v': ['orig']})
        self.assertEqual(events, [('landing', 1), ('audio', 'source', 1, 9, True)])
        self.assertEqual(self.responses[-1][0], 202)


if __name__ == '__main__':
    unittest.main()
