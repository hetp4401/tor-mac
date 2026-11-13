"""Opt-in first-frame priority remains bounded and preserves foreground work."""
import ast
import itertools
import os
from pathlib import Path
import threading
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'runners' / 'tor.py'
KEY = 'fixture'

class ColdDispatchPriorityTests(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0, 10.0]
        self.cached, self.requests = set(), []
        outer = self
        class Clock:
            def time(inner): return outer.clock[0]
            def monotonic(inner): return outer.clock[1]
            def sleep(inner, seconds): outer.advance(seconds)
        self.ns = dict(globals())
        self.ns.update({'time': Clock(), 'COLD_PRIORITY_S': .250, 'REPLICA': 1,
            'hls_lock': threading.Lock(), 'turns': threading.Condition(),
            'piece_has': lambda key, n: int(n in self.cached), 'FILM_IDLE': 600,
            'HLS_AHEAD': 12, 'FILL_FROM': 24, 'FILL_RUNS': 1, 'FILL_CHUNK': 64 * 2**20,
            'FILL_STALE': 90, 'RUN_MAX': 12, 'MUXED': False,
            'pieces': {}, 'mem_pieces': {}, 'mac_jobs': {}, 'where_keep': lambda *a: None,
            'watched': lambda key: True, 'piece_q': lambda *a: '', 'url_of': lambda k: 'http://fixture',
            'CHUNK_KEY': 'fixture', 'fill_audio': lambda *a: None, 'log': lambda *a: None,
            'src_meta': {KEY: {}}, 'converters': lambda *a: [1], 'drop_far': lambda *a: None})
        self.info = {'cuts': list(range(11)), 'pos': [0] * 10, 'pend': [100] * 10, 'size': 10 * 2**20}
        self.ns['probed'] = {KEY: self.info}
        self.ns['probe_source'] = lambda key: self.info
        self.film = {'head': 0, 'landing': 0, 'jumped': 1000.0, 'seen': 1000.0, 'buffer_end': 0,
            'total': 10, 'done': set(), 'filling': 1, 'token': 'fixture',
            'fill': {'taken': {}, 'claim': {}, 'where': {}}}
        self.ns['films'] = {KEY: self.film}
        tree = ast.parse(SOURCE.read_text())
        names = {'cold_priority_delay', 'fill_next', 'fill_film', 'film_heard'}
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        assert {n.name for n in selected} == names
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def advance(self, seconds):
        self.clock[0] += seconds
        self.clock[1] += seconds

    def test_env_defaults_off_and_clamps_to_one_second(self):
        node = next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'COLD_PRIORITY_S' for t in n.targets))
        for values, expected in [({}, 0), ({'TOR_COLD_PRIORITY_MS': '250'}, .25),
                                 ({'TOR_COLD_PRIORITY_MS': '-20'}, 0), ({'TOR_COLD_PRIORITY_MS': '5000'}, 1)]:
            ns = {'os': types.SimpleNamespace(environ=values)}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)
            self.assertEqual(ns['COLD_PRIORITY_S'], expected)

    def test_disabled_cached_and_remote_ready_heads_never_defer(self):
        self.ns['COLD_PRIORITY_S'] = 0
        self.assertEqual(self.ns['cold_priority_delay'](KEY), 0)
        self.ns['COLD_PRIORITY_S'] = .25
        self.cached.add(0)
        self.assertEqual(self.ns['cold_priority_delay'](KEY), 0)
        self.cached.clear(); self.film['done'].add(0)
        self.assertEqual(self.ns['cold_priority_delay'](KEY), 0)

    def test_no_head_request_expires_despite_wall_clock_rollback(self):
        for duration in (.25, 1.0):
            with self.subTest(duration=duration):
                self.setUp()
                self.ns['COLD_PRIORITY_S'] = duration
                self.assertEqual(self.ns['cold_priority_delay'](KEY), duration)
                self.clock[0] -= 3600
                self.clock[1] += duration + .001
                self.assertEqual(self.ns['cold_priority_delay'](KEY), 0)
                self.assertEqual(self.ns['cold_priority_delay'](KEY), 0)

    def test_new_seek_gets_one_fresh_bounded_deadline(self):
        self.ns['cold_priority_delay'](KEY)
        self.advance(.10)
        self.film.update(head=4, landing=4, jumped=self.clock[0])
        self.assertEqual(self.ns['cold_priority_delay'](KEY), .25)
        self.advance(.251)
        self.assertEqual(self.ns['cold_priority_delay'](KEY), 0)
        self.film['landing'] = None
        self.assertEqual(self.ns['cold_priority_delay'](KEY), 0)

    def run_fill(self, target, on_wait=None):
        outer = self
        self.ns['fill_next'] = lambda f, key, owner, group: [target] if target not in f['done'] else None
        class Condition:
            def __enter__(inner): return inner
            def __exit__(inner, *_): pass
            def wait(inner, seconds):
                outer.assertFalse(outer.film['fill']['taken'])
                outer.advance(seconds)
                if on_wait: on_wait()
        class Thread:
            def __init__(inner, target, args=(), **kwargs): inner.target, inner.args = target, args
            def start(inner): inner.target(*inner.args)
            def join(inner): pass
        class Timer:
            def __init__(inner, *args, **kwargs): pass
            def start(inner): pass
            def cancel(inner): pass
        class Connections:
            def request(inner, *args, **kwargs):
                outer.requests.append((target, outer.clock[1] - 10))
                return 200, {}, b'{}'
        self.ns.update({'made': Condition(), 'conns': Connections(),
            'threading': types.SimpleNamespace(Thread=Thread, Timer=Timer, Event=threading.Event),
            'quietly': lambda fn, *args: fn(*args)})
        failures = []
        def run():
            try: self.ns['fill_film'](KEY, 0, 1, 1)
            except BaseException as error: failures.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start(); thread.join(1)
        self.assertFalse(thread.is_alive(), 'no deadlock or starvation')
        if failures: raise failures[0]

    def test_current_piece_is_not_deferred(self):
        self.run_fill(0)
        self.assertEqual(self.requests, [(0, 0)])

    def test_future_dispatch_resumes_after_bounded_window_without_head_request(self):
        for duration in (.25, 1.0):
            with self.subTest(duration=duration):
                self.setUp()
                self.ns['COLD_PRIORITY_S'] = duration
                self.run_fill(1)
                self.assertEqual(len(self.requests), 1)
                self.assertAlmostEqual(self.requests[0][1], duration, places=6)

    def test_cached_head_releases_future_dispatch_immediately(self):
        self.cached.add(0)
        self.run_fill(1)
        self.assertEqual(self.requests, [(1, 0)])

    def test_seek_to_deferred_piece_releases_it_as_current(self):
        def seek(): self.film.update(head=1, landing=1, jumped=self.clock[0])
        self.run_fill(1, seek)
        self.assertEqual(self.requests[0][0], 1)
        self.assertLess(self.requests[0][1], .1)

    def test_head_reserved_before_background_and_excluded_from_cpu_batch(self):
        events = []
        self.ns['films'] = {}
        def reserve(key, head, cls):
            events.append('reserve')
            self.ns['mac_jobs'][(key, head)] = {'cls': cls}
        def claim_reader(*args): pass
        def buffer_film(*args): pass
        def quietly(fn, *args): return fn(*args)
        class Thread:
            def __init__(inner, target, args=(), **kwargs): inner.target, inner.args = target, args
            def start(inner): events.append(inner.args[0].__name__ if inner.target is quietly else inner.target.__name__)
        self.ns.update({'mac_start': reserve, 'claim_reader': claim_reader, 'buffer_film': buffer_film,
            'quietly': quietly, 'threading': types.SimpleNamespace(Thread=Thread, Event=threading.Event)})
        self.ns['film_heard'](KEY, 'fixture', 0, 'conv')
        self.assertLess(events.index('reserve'), events.index('buffer_film'))
        self.assertLess(events.index('reserve'), events.index('fill_film'))
        film = self.ns['films'][KEY]
        film['fill'] = {'taken': {}, 'claim': {}, 'where': {}}
        batch = self.ns['fill_next'](film, KEY, 1, True)
        self.assertTrue(batch); self.assertNotIn(0, batch)
        self.assertEqual(self.ns['mac_jobs'][(KEY, 0)]['cls'], 0)
        events.clear(); film['done'].add(0)
        self.ns['film_heard'](KEY, 'fixture', 0, 'conv')
        self.assertNotIn('reserve', events)

if __name__ == '__main__': unittest.main(verbosity=2)
