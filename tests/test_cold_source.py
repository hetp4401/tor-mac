"""Causal tests for publishing the initial source block while it downloads.

Run --measure FILE.mkv for a controlled before/after full ffprobe+Cues comparison.
The buffered reference delays publication until the same complete block arrives.
"""
import ast
import collections
import concurrent.futures
import contextlib
import hashlib
import hmac
import http.client
import itertools
import json
import math
from pathlib import Path
import re
import socket
import subprocess
import sys
import types
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE = Path(__file__).resolve().parents[1] / 'runners' / 'tor.py'
KEY = '0123456789abcdef'
BLOCK = 8 * 2**20
CHUNK = 256 * 1024


class Origin(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *_): pass
    def do_GET(self):
        server = self.server
        requested = self.headers.get('Range', 'bytes=0-')
        first, last = requested.removeprefix('bytes=').split('-')
        start, end = int(first), min(int(last) if last else len(server.data) - 1, len(server.data) - 1)
        self.send_response(206)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Content-Range', f'bytes {start}-{end}/{len(server.data)}')
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        at = start
        try:
            while at <= end:
                size = min(32 * 1024, end - at + 1)
                if server.rate:
                    with server.pace_lock:
                        now = time.monotonic()
                        due = server.next_at or now
                        server.next_at = due + size / server.rate
                    if due > now:
                        time.sleep(due - now)
                data = server.data[at:at + size]
                self.wfile.write(data)
                self.wfile.flush()
                with server.count_lock:
                    server.bytes += len(data)
                at += len(data)
                if start == 0 and at == CHUNK:
                    server.prefix_sent.set()
                    if server.gate:
                        server.release.wait(5)
                    if server.truncate:
                        return
        except (BrokenPipeError, ConnectionResetError):
            pass


class Harness:
    def __init__(self, data, progressive=True, gate=False, truncate=False, rate=0):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
        self.server.daemon_threads = True
        self.server.data, self.server.gate, self.server.truncate, self.server.rate = data, gate, truncate, rate
        self.server.prefix_sent, self.server.release = threading.Event(), threading.Event()
        self.server.bytes, self.server.next_at = 0, 0
        self.server.count_lock, self.server.pace_lock = threading.Lock(), threading.Lock()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/media'
        self.blocks, self.errors, self.background = {}, [], []
        self.ns = dict(globals())
        self.ns.update({
            'SRC_BLOCK': BLOCK, 'LAND_CHUNK': CHUNK, 'PIECE_S': 1.0, 'SRC_LEAD': 4,
            'PROBE_WINDOWS': True, 'UA': 'TorFixture', 'POOL_IDLE': 50, 'readable': lambda sock: False,
            'JUMP_ALONE': 10, 'CHUNK_KEY': 'fixture-key', 'REPLICA': 1,
            'src_meta': {KEY: {'gurl': 'fixture-lookup', 'url': None, 'size': None, 't': 0, 'token': 'fixture', 'reader': 1}},
            'src_blocks': self.blocks, 'reading': {}, 'busy': {}, 'facts_gentle': set(),
            'land_reads': {}, 'src_tails': collections.OrderedDict(), 'films': {}, 'src_waits': {},
            'hls_lock': threading.Lock(), 'turns': threading.Condition(), 'bstats': {'vps': []},
            'src_trouble': {}, 'src_back': {}, 'probed': {}, 'land_now': lambda *a: None,
            'probe_kept': lambda key: None, 'ask_copies': lambda *a: (None, None),
            'mac_probe_source': lambda key: None, 'probe_keep': lambda *a: None,
            'facts_note': lambda *a: None, 'facts_of': lambda info: {}, 'facts_keep': lambda *a: None,
            'piece_urgent': lambda key, n, urgent: urgent, 'log': lambda *a: None,
        })
        ns = self.ns
        self.known = threading.Event()
        ns['src_meta'][KEY]['finding'] = {'known': self.known, 'at': time.time()}
        class Connections:
            def request(_, method, url, *args, **kwargs):
                if url == self.url: return self.pool.request(method, url, *args, **kwargs)
                return 302, {'Location': self.url}, b''
        ns['conns'] = Connections()
        ns['keep_block'] = self.keep_block
        ns['block_cached'] = lambda key, b: self.blocks.get((key, b))
        ns['wanted_block'] = lambda key, b, urgent=True: (key, b) not in self.blocks and (key, b) not in ns['reading']
        ns['src_block'] = self.src_block
        ns['src_lookup'] = lambda key: ns['src_meta'][key]
        ns['unclaim'] = self.unclaim
        ns['keep_tail'] = lambda *a: None
        def quietly(fn, *args):
            try: return fn(*args)
            except Exception as error: self.errors.append(type(error).__name__ + ': ' + str(error))
        ns['quietly'] = quietly
        names = {'src_find', 'land_span', 'land_take', 'land_part', 'src_read', 'ebml_vint', 'ebml',
                 'mkv_keyframes', 'NoIndex', 'SourceBroken', 'probe_blocks', 'probe_now',
                 'piece_cuts', 'keyframe_cuts', 'Handler', 'Pool', 'probe_reader'}
        tree = ast.parse(SOURCE.read_text())
        chosen = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
        assert {n.name for n in chosen} == names
        exec(compile(ast.Module(body=chosen, type_ignores=[]), str(SOURCE), 'exec'), ns)
        self.pool = ns['Pool']()
        if not progressive:
            def buffered_take(key, response, span, left, size, urgent, started):
                # Equivalent to the previous src_find read(min(SRC_BLOCK,size)):
                # no prefix becomes visible before the complete block arrives.
                data = response.read(span['e'] - span['s'])
                if len(data) != span['e'] - span['s']:
                    raise http.client.IncompleteRead(data, span['e'] - span['s'])
                span['buf'][:] = data
                with span['cv']:
                    span['got'] = len(data); span['cv'].notify_all()
                self.keep_block(key, 0, data)
                left.clear()
                yield 0
            ns['land_take'] = buffered_take
        self.local = ThreadingHTTPServer(('127.0.0.1', 0), ns['Handler'])
        self.local.daemon_threads = True
        ns['PAGE_PORT'] = self.local.server_port
        self.local_thread = threading.Thread(target=self.local.serve_forever, daemon=True)
        self.local_thread.start()
        self.finder = None
        def lookup_begin(key):
            if self.finder is None: self.start()
            return ns['src_meta'][key]['finding']
        ns['lookup_begin'] = lookup_begin

    def keep_block(self, key, b, data):
        expected = min(BLOCK, len(self.server.data) - b * BLOCK)
        if len(data) != expected:
            raise AssertionError('partial source block must never be cached')
        self.blocks[(key, b)] = data

    def unclaim(self, key, b):
        self.ns['busy'].pop(('src', key, b), None)
        return self.ns['reading'].pop((key, b), None)

    def src_block(self, key, b, urgent=True, n=None):
        if (key, b) in self.blocks: return self.blocks[(key, b)]
        with self.ns['hls_lock']:
            event = self.ns['reading'].get((key, b))
            owner = event is None
            if owner:
                event = self.ns['reading'][(key, b)] = threading.Event()
        if not owner:
            if not event.wait(10): raise TimeoutError('fixture block')
            if (key, b) not in self.blocks: raise OSError('fixture initial block incomplete')
            return self.blocks[(key, b)]
        try:
            start, end = b * BLOCK, min(len(self.server.data), (b + 1) * BLOCK) - 1
            req = urllib.request.Request(self.url, headers={'Range': f'bytes={start}-{end}'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read()
            self.keep_block(key, b, data)
            return data
        finally:
            with self.ns['hls_lock']:
                self.ns['reading'].pop((key, b), None)
            event.set()

    def start(self):
        def find():
            try: self.ns['src_find'](KEY)
            except Exception as error: self.errors.append(type(error).__name__ + ': ' + str(error))
            finally: self.known.set()
        self.finder = threading.Thread(target=find, daemon=True)
        self.finder.start()
        return self.finder

    def close(self):
        self.server.release.set()
        if self.finder: self.finder.join(10)
        self.local.shutdown(); self.local.server_close(); self.local_thread.join(1)
        self.server.shutdown(); self.server.server_close(); self.thread.join(1)

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class ProgressiveInitialSourceTests(unittest.TestCase):
    data = bytes(range(256)) * (BLOCK // 256)

    def test_prefix_is_available_before_complete_transfer_with_identical_bytes(self):
        for progressive in [False, True]:
            with self.subTest(progressive=progressive), Harness(self.data, progressive=progressive, gate=True) as h:
                h.start()
                self.assertTrue(h.server.prefix_sent.wait(2)); self.assertTrue(h.known.wait(2))
                with concurrent.futures.ThreadPoolExecutor(1) as pool:
                    result = pool.submit(h.ns['src_read'], KEY, 0, CHUNK)
                    if progressive:
                        self.assertEqual(result.result(timeout=.5), self.data[:CHUNK])
                        self.assertTrue(h.finder.is_alive())
                        self.assertNotIn((KEY, 0), h.blocks)
                    else:
                        time.sleep(.1); self.assertFalse(result.done())
                    h.server.release.set()
                    self.assertEqual(result.result(timeout=2), self.data[:CHUNK])
                h.finder.join(2)
                self.assertEqual(hashlib.sha256(h.blocks[(KEY, 0)]).digest(), hashlib.sha256(self.data).digest())
                self.assertEqual(h.server.bytes, len(self.data))
                self.assertTrue(h.ns['land_reads'][KEY]['over'])

    def test_truncated_range_releases_waiters_without_caching_partial_block(self):
        with Harness(self.data, truncate=True) as h:
            h.start(); h.finder.join(2)
            self.assertFalse(h.finder.is_alive())
            span = h.ns['land_reads'][KEY]
            self.assertTrue(span['over']); self.assertTrue(span.get('err'))
            self.assertEqual(h.ns['land_part'](KEY, 0, CHUNK), self.data[:CHUNK])
            start = time.monotonic()
            self.assertIsNone(h.ns['land_part'](KEY, 0, 2 * CHUNK))
            self.assertLess(time.monotonic() - start, .1)
            self.assertNotIn((KEY, 0), h.blocks)
            self.assertNotIn((KEY, 0), h.ns['reading'])
            self.assertNotIn(('src', KEY, 0), h.ns['busy'])

    def test_existing_seek_span_is_not_replaced_by_initial_probe(self):
        with Harness(self.data) as h:
            seek = h.ns['land_span'](BLOCK, 2 * BLOCK); seek['m'] = 99
            h.ns['land_reads'][KEY] = seek
            h.start(); h.finder.join(2)
            self.assertIs(h.ns['land_reads'][KEY], seek)
            self.assertFalse(seek['over'])
            self.assertEqual(h.blocks[(KEY, 0)], self.data)

    def test_later_seek_span_is_not_modified_by_initial_read_cleanup(self):
        with Harness(self.data, gate=True) as h:
            h.start(); self.assertTrue(h.server.prefix_sent.wait(2)); self.assertTrue(h.known.wait(2))
            initial = h.ns['land_reads'][KEY]
            seek = h.ns['land_span'](BLOCK, 2 * BLOCK); seek['m'] = 99
            h.ns['land_reads'][KEY] = seek
            h.server.release.set(); h.finder.join(2)
            self.assertIs(h.ns['land_reads'][KEY], seek)
            self.assertFalse(seek['over']); self.assertTrue(initial['over'])

    def test_progressive_span_map_stays_bounded(self):
        with Harness(self.data) as h:
            for i in range(4):
                h.ns['land_reads'][str(i)] = h.ns['land_span'](0, 1)
            h.start(); h.finder.join(2)
            self.assertEqual(len(h.ns['land_reads']), 4)
            self.assertIn(KEY, h.ns['land_reads'])


class SourceDemandPromotionTests(unittest.TestCase):
    def setUp(self):
        self.waits = 0
        self.on_wait = lambda: self.fail('landing demand waited behind its own missing source')
        outer = self
        class Turns:
            def __init__(inner): inner.lock = threading.RLock()
            def __enter__(inner): inner.lock.acquire(); return inner
            def __exit__(inner, *_): inner.lock.release()
            def notify_all(inner): pass
            def wait(inner, timeout=None):
                outer.waits += 1
                if outer.waits > 3: outer.fail('source demand did not progress')
                outer.on_wait()
        self.ns = dict(globals())
        self.ns.update({'turns': Turns(), 'hls_lock': threading.Lock(), 'SRC_BLOCK': 16,
                        'SRC_ASKS': 6, 'LIGHT_ASKS': 1, 'BG_ASKS': 1, 'HEAD_ALONE': 3,
                        'SRC_PAUSE': 10, 'BG_RUN': 3, 'facts_gentle': set(), 'up_wait': {}, 'reading': {},
                        'busy': {}, 'src_blocks': {}, 'up_now': {'urgent': 0, 'bg': 0, 'light': 0},
                        'up_going': {}, 'paced_at': {}, 'films': {KEY: {'head': 37}},
                        'landing_pending': lambda key: True, 'watched': lambda key: True,
                        'bg_rate': lambda key: 1024, 'runs': {}, 'src_trouble': {}, 'src_back': {},
                        'bstats': {'vps': []}, 'relay_keep': lambda *a: None,
                        'REPLICA': 1, 'src_meta': {KEY: {'reader': 1}},
                        'block_cached': lambda key, b: self.ns['src_blocks'].get((key, b))})
        names = {'upstream_turn', 'up_hurry', 'from_player', 'near_waits', 'read_run', 'let_go', 'once', 'src_block', 'fetch_kind'}
        nodes = [n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def test_busy_demand_before_queue_exists_is_not_lost(self):
        event = self.ns['busy'][('src', KEY, 1)] = threading.Event()
        self.ns['up_hurry'](KEY, 1, 37)
        self.assertEqual(event.n, 37)
        self.assertFalse(self.ns['up_wait'])
        with self.ns['upstream_turn'](KEY, 1, 0, True) as admitted:
            self.assertEqual(admitted, ('urgent', True, 37))
            self.assertEqual(self.ns['up_going'][(KEY, 1)], 37)
        self.assertEqual(self.waits, 0)
        self.assertEqual(self.ns['up_now']['urgent'], 0)

    def test_later_owned_block_promotes_queued_batch(self):
        event = self.ns['reading'][(KEY, 2)] = threading.Event()
        self.ns['busy'][('src', KEY, 2)] = event
        self.on_wait = lambda: self.ns['up_hurry'](KEY, 2, 37)
        with self.ns['upstream_turn'](KEY, 1, 0, False, claims={2: event}) as admitted:
            self.assertEqual(admitted, ('urgent', True, 37))
            self.assertIs(self.ns['reading'][(KEY, 2)], event)
            self.assertFalse(event.is_set())
        self.assertEqual(self.waits, 1)
        self.assertNotIn(KEY, self.ns['paced_at'])

    def test_stale_replaced_claim_does_not_promote_another_batch(self):
        old = threading.Event(); old.n = 37
        self.ns['reading'][(KEY, 2)] = threading.Event()
        class StillBlocked(Exception): pass
        def blocked(): raise StillBlocked()
        self.on_wait = blocked
        with self.assertRaises(StillBlocked):
            with self.ns['upstream_turn'](KEY, 1, 0, True, claims={2: old}): pass
        self.assertEqual(self.ns['up_now']['urgent'], 0)

    def test_promoted_landing_still_obeys_source_admission_cap(self):
        event = self.ns['busy'][('src', KEY, 1)] = threading.Event(); event.n = 37
        self.ns['up_now']['urgent'] = 8
        def free_slot(): self.ns['up_now']['urgent'] -= 1
        self.on_wait = free_slot
        with self.ns['upstream_turn'](KEY, 1, 0, True) as admitted:
            self.assertEqual(admitted, ('urgent', True, 37))
            self.assertEqual(self.ns['up_now']['urgent'], 8)
        self.assertEqual(self.waits, 1)
        self.assertEqual(self.ns['up_now']['urgent'], 7)

    def test_existing_shared_read_remains_deduplicated(self):
        event = self.ns['reading'][(KEY, 1)] = threading.Event()
        with self.ns['upstream_turn'](KEY, 1, 37, True) as admitted:
            self.assertIsNone(admitted[0])
            self.assertEqual(self.ns['up_now']['urgent'], 0)
            self.assertIs(self.ns['reading'][(KEY, 1)], event)
        self.assertEqual(self.waits, 0)

    def handoff_case(self, late=False, second_seek=False):
        owner_waits, canceled, claims = [], [], []
        outer = self
        run = {'key': KEY, 'bg': True, 'left': [1]}
        class Event:
            done = False
            def is_set(inner): return inner.done
            def set(inner): inner.done = True
            def wait(inner, timeout):
                owner_waits.append(timeout)
                if late and len(owner_waits) == 1:
                    outer.ns['runs'][1] = run
                    if second_seek:
                        outer.ns['films'][KEY]['head'] = 100
                        inner.n = 100
                elif second_seek:
                    outer.ns['src_blocks'][(KEY, 1)] = b'finished by current owner'
                    inner.set()
                return inner.done
        event = Event()
        actual_once = self.ns['once']
        def join(kind, key, b, make, waiting=None):
            # Interleave ownership after src_block's first hurry, before once's
            # atomic lookup. The waiting callback must target this new Event.
            claims.append(1)
            self.assertNotIn(('src', KEY, 1), self.ns['busy'])
            self.ns['busy'][('src', KEY, 1)] = event
            if not late: self.ns['runs'][1] = run
            def no_duplicate(): self.fail('shared wait started a duplicate origin fetch')
            return actual_once(kind, key, b, no_duplicate, waiting)
        def cancel(active):
            canceled.append(active)
            self.assertFalse(self.ns['hls_lock'].locked())
            self.assertEqual(event.n, 37)
            self.ns['src_blocks'][(KEY, 1)] = b'finished by current owner'
            event.set()
        self.ns.update({'once': join, 'end_call': cancel})
        self.assertEqual(self.ns['src_block'](KEY, 1, True, 37), b'finished by current owner')
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(canceled), 0 if second_seek else 1)
        self.assertEqual(event.n, 100 if second_seek else 37)
        self.assertGreaterEqual(len(owner_waits), 2 if late else 1)

    def test_owner_created_between_hurry_and_once_gets_landing_demand(self):
        self.handoff_case()

    def test_late_background_registration_is_canceled_on_next_shared_wait(self):
        self.handoff_case(late=True)

    def test_old_waiter_cannot_cancel_or_demote_a_new_seek(self):
        self.handoff_case(late=True, second_seek=True)

    def test_read_run_uses_promoted_identity_for_cancellation_and_one_fetch(self):
        event = self.ns['reading'][(KEY, 2)] = threading.Event(); event.n = 37
        self.ns['busy'][('src', KEY, 2)] = event
        self.ns['src_lookup'] = lambda *a, **k: {'url': 'http://fixture/media', 'size': 48}
        calls, kept = [], []
        outer = self
        class Response:
            status = 206
            def read(inner, size=None): return b'x' * (size or 0)
        class Connection:
            sock = object()
            def request(inner, *args, **kwargs):
                calls.append((args, kwargs))
                active = list(outer.ns['runs'].values())
                outer.assertEqual(len(active), 1)
                outer.assertEqual(active[0]['n'], 37)
                outer.assertFalse(active[0]['bg'])
            def getresponse(inner): return Response()
            def close(inner): pass
        self.ns['relay_conn'] = lambda *a: (Connection(), False)
        self.ns['keep_block'] = lambda key, b, data: (kept.append(b), self.ns['src_blocks'].__setitem__((key, b), data))
        arrived, result = threading.Event(), {}
        self.ns['read_run'](KEY, [1, 2], False, 0, arrived, result)
        self.assertEqual(result, {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(kept, [1, 2])
        self.assertTrue(arrived.is_set()); self.assertTrue(event.is_set())
        self.assertFalse(self.ns['runs']); self.assertFalse(self.ns['reading'])
        self.assertEqual(self.ns['up_now'], {'urgent': 0, 'bg': 0, 'light': 0})


class MetadataWindowTests(unittest.TestCase):
    def setUp(self):
        self.data = bytes(range(256)) * (12 * CHUNK // 256 + 1)
        self.calls, self.fallbacks, self.blocks = [], [], {}
        self.ns = dict(globals())
        self.ns.update({'PROBE_WINDOWS': True, 'SRC_BLOCK': BLOCK,
                        'src_meta': {KEY: {'size': len(self.data), 'url': 'http://fixture/media'}},
                        'block_cached': lambda key, b: self.blocks.get(b)})
        outer = self
        class Connections:
            def request(inner, method, url, *, headers, timeout, limit):
                a, z = map(int, headers['Range'][6:].split('-'))
                outer.calls.append((a, z, limit))
                return 206, {'Content-Range': f'bytes {a}-{z}/{len(outer.data)}'}, outer.data[a:z+1]
        self.ns['conns'] = Connections()
        def fallback(key, start, n):
            self.fallbacks.append((start, n)); return self.data[start:start+n]
        self.ns['src_read'] = fallback
        node = next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'probe_reader')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        self.read = self.ns['probe_reader'](KEY, self.data[:CHUNK])

    def test_head_and_video_cache_are_reused(self):
        self.assertEqual(self.read(10, 100), self.data[10:110])
        self.blocks[0] = self.data
        self.assertEqual(self.read(2*CHUNK, 100), self.data[2*CHUNK:2*CHUNK+100])
        self.assertEqual(self.calls, [])

    def test_cue_header_and_payload_share_one_bounded_window(self):
        at = 3 * CHUNK + 5
        self.assertEqual(self.read(at, 16), self.data[at:at+16])
        self.assertEqual(self.read(at + 9, 1000), self.data[at+9:at+1009])
        self.assertEqual(self.calls, [(at, at + CHUNK - 1, CHUNK)])
        self.assertFalse(self.fallbacks)

    def test_large_reads_cross_windows_and_eof_without_excess(self):
        at = 10 * CHUNK + 123
        self.assertEqual(self.read(at, 4*CHUNK), self.data[at:])
        self.assertTrue(all(z-a+1 == limit <= CHUNK for a,z,limit in self.calls))
        self.assertEqual(sum(limit for _,_,limit in self.calls), len(self.data)-at)

    def test_cache_holds_at_most_eight_windows(self):
        for i in range(1, 12): self.assertEqual(self.read(i*CHUNK, 16), self.data[i*CHUNK:i*CHUNK+16])
        closure = dict(zip(self.read.__code__.co_freevars, [c.cell_contents for c in self.read.__closure__]))
        self.assertLessEqual(len(closure['windows']), 8)
        self.assertLessEqual(sum(map(len, closure['windows'].values())), 8*CHUNK)

    def test_invalid_responses_fall_back_once_for_the_whole_probe(self):
        variants = [(200, {'Content-Range': f'bytes {CHUNK}-{2*CHUNK-1}/{len(self.data)}'}, self.data[CHUNK:2*CHUNK]),
                    (206, {'Content-Range': f'bytes 0-{CHUNK-1}/{len(self.data)}'}, self.data[:CHUNK]),
                    (206, {'Content-Range': f'bytes {CHUNK}-{2*CHUNK-1}/1'}, self.data[:CHUNK]),
                    (206, {'Content-Range': f'bytes {CHUNK}-{2*CHUNK-1}/{len(self.data)}'}, b'short')]
        for answer in variants:
            with self.subTest(answer=answer[:2]):
                calls = []
                self.ns['conns'] = types.SimpleNamespace(request=lambda *a, **k: (calls.append(1) or answer))
                read = self.ns['probe_reader'](KEY, self.data[:CHUNK])
                self.assertEqual(read(CHUNK, 16), self.data[CHUNK:CHUNK+16])
                self.assertEqual(read(2*CHUNK, 16), self.data[2*CHUNK:2*CHUNK+16])
                self.assertEqual(len(calls), 1)

    def test_network_error_and_disabled_flag_use_block_fallback(self):
        def fail(*a, **k): raise OSError('fixture interruption')
        self.ns['conns'] = types.SimpleNamespace(request=fail)
        self.assertEqual(self.read(CHUNK, 16), self.data[CHUNK:CHUNK+16])
        self.ns['PROBE_WINDOWS'] = False
        read = self.ns['probe_reader'](KEY, self.data[:CHUNK])
        self.assertEqual(read(0, 16), self.data[:16])
        self.assertEqual(self.fallbacks, [(CHUNK, 16), (0, 16)])

    def test_missing_source_metadata_uses_existing_reader(self):
        for meta in [{}, {"size": None, "url": "http://fixture"}, {"size": len(self.data), "url": None}]:
            with self.subTest(meta=meta):
                self.ns['src_meta'][KEY] = meta
                read = self.ns['probe_reader'](KEY, self.data[:CHUNK])
                self.assertEqual(read(CHUNK, 16), self.data[CHUNK:CHUNK+16])
        self.assertFalse(self.calls)

    def test_range_unit_is_case_insensitive(self):
        self.ns['conns'] = types.SimpleNamespace(request=lambda *a, **k:
            (206, {'Content-Range': f'Bytes {CHUNK}-{2*CHUNK-1}/{len(self.data)}'}, self.data[CHUNK:2*CHUNK]))
        self.assertEqual(self.read(CHUNK, 16), self.data[CHUNK:CHUNK+16])
        self.assertFalse(self.fallbacks)

    def test_pool_refuses_oversized_response_before_reading_body(self):
        ns = dict(globals()); ns.update({'UA': 'fixture', 'POOL_IDLE': 50, 'readable': lambda s: False})
        node = next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'Pool')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)
        events = []
        class Response:
            def getheader(self, name): return str(CHUNK * 2)
            def read(self, *args): events.append('read'); return b''
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): events.append('closed')
        ns['http'] = types.SimpleNamespace(client=types.SimpleNamespace(HTTPConnection=Connection, HTTPSConnection=Connection,
                                                                        HTTPException=http.client.HTTPException))
        with self.assertRaises(http.client.HTTPException): ns['Pool']().request('GET', 'http://fixture/media', limit=CHUNK)
        self.assertEqual(events, ['closed'])
        events.clear()
        Response.getheader = lambda self, name: '²'  # isdigit(), but not accepted by int().
        with self.assertRaises(ValueError): ns['Pool']().request('GET', 'http://fixture/media', limit=CHUNK)
        self.assertEqual(events, ['closed'])


def measure(path):
    data = Path(path).read_bytes()
    results = []
    baseline = subprocess.check_output(['git', 'show', 'b88f8ef:runners/tor.py'], cwd=SOURCE.parents[1], text=True)
    baseline_find = next(n for n in ast.parse(baseline).body if isinstance(n, ast.FunctionDef) and n.name == 'src_find')
    for mode, progressive, windows in [("baseline", False, False), ("progressive_only", True, False), ("progressive_windows", True, True)]:
        with Harness(data, progressive=progressive, rate=8 * 2**20) as h:
            h.ns["PROBE_WINDOWS"] = windows
            if not progressive:
                # Full-probe comparison uses the exact prechange lookup path,
                # including the absence of an initial published span.
                exec(compile(ast.Module(body=[baseline_find], type_ignores=[]), str(SOURCE), 'exec'), h.ns)
                h.ns['PROBE_WINDOWS'] = False
            started = time.monotonic()
            h.start(); h.known.wait(10)
            # Start the real probe immediately, just as production does after
            # known is set. Prefix measurement must not serialize tail prefetch.
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                future = pool.submit(h.ns['probe_now'], KEY)
                prefix = h.ns['src_read'](KEY, 0, CHUNK)
                prefix_s = time.monotonic() - started
                # Exact deployed ffprobe command, Cues parser and prefetch policy.
                info = future.result(timeout=30)
                full_s = time.monotonic() - started
            bytes_at_probe = h.server.bytes
            h.finder.join(10)
            results.append({'mode': mode, 'prefix_ready_s': round(prefix_s, 4),
                            'probe_ready_s': round(full_s, 4), 'origin_bytes_at_probe': bytes_at_probe,
                            'duration': info['duration'], 'tracks': len(info['audios']), 'pieces': len(info['cuts']) - 1,
                            'prefix_sha256': hashlib.sha256(prefix).hexdigest(),
                            'metadata_sha256': hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest(), 'errors': h.errors})
    assert len({r['metadata_sha256'] for r in results}) == 1, 'metadata or cuts changed'
    print(json.dumps({'source_bytes': len(data), 'source_sha256': hashlib.sha256(data).hexdigest(),
                      'origin_limit_mib_s': 8, 'baseline_revision': 'b88f8ef',
                      'candidate_source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                      'reproduce': 'python3 tests/test_cold_source.py --measure /tmp/tor-cold-profile/fixture-tail.mkv',
                      'fixture_command': 'ffmpeg -v error -f lavfi -i testsrc2=size=960x540:rate=24 -f lavfi -i sine=frequency=440:sample_rate=48000 -t 26 -c:v libx264 -preset ultrafast -crf 0 -g 48 -sc_threshold 0 -c:a aac -y /tmp/tor-cold-profile/fixture-tail.mkv',
                      'results': results}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--measure': measure(sys.argv[2])
    else: unittest.main(verbosity=2)
