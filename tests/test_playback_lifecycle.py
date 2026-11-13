import collections
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import shutil
import subprocess
import sys
import threading
import time
import unittest

from playback_harness import backend


class PlaybackLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.m = backend()
        self.key = 'a' * 16
        self.sid = 'session-a-1234567890'
        self.m.src_meta[self.key] = {}

    def tearDown(self):
        shutil.rmtree(self.m.SEG_DIR, ignore_errors=True)

    def test_close_before_start_and_delayed_requests_never_revive(self):
        m = self.m
        m.playback_release(self.key, self.sid)
        with self.assertRaises(m.Dropped):
            m.playback_touch(self.key, self.sid)
        m.playback_touch(self.key, 'new-session-123456789')
        self.assertNotIn(self.key, m.playback_stopped)
        with self.assertRaises(m.Dropped):
            m.playback_touch(self.key, self.sid)

    def test_active_fragment_survives_a_seek_but_stopping_its_session_cancels_it(self):
        m = self.m
        s = m.playback_touch(self.key, self.sid)
        s.update(managed=True, seek_seq=1, seek_head=0)
        m.films[self.key] = {'head':0, 'buffer_end':12}
        entered, resume = threading.Event(), threading.Event()
        results = []
        def request():
            try:
                with m.playback_request(self.key, 30, {'sid':[self.sid]}):
                    results.append(m.far(self.key, 30))
                    entered.set()
                    resume.wait(2)
                    m.work_check(self.key)
                    results.append('finished')
            except m.Dropped:
                results.append('cancelled')
        t = threading.Thread(target=request)
        t.start()
        self.assertTrue(entered.wait(2))
        self.assertTrue(m.playback_requested(self.key, 30))
        s.update(seek_seq=2, seek_head=80)
        m.cpu_jobs[(self.key, 30)] = {'dropped':False, 'p':None}
        m.cpu_jobs[(self.key, 31)] = {'dropped':False, 'p':None}
        m.drop_far(self.key, 80, 12)
        self.assertFalse(m.cpu_jobs[(self.key, 30)]['dropped'])
        self.assertTrue(m.cpu_jobs[(self.key, 31)]['dropped'])
        resume.set(); t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(results, [False, 'finished'])
        self.assertEqual(m.playback_demands, {})
        with m.playback_request(self.key, 80, {'sid':[self.sid]}):
            s['closed'] = True
            with self.assertRaises(m.Dropped):
                m.work_check(self.key)

    def test_switch_preserves_another_viewer_of_the_same_source(self):
        m = self.m
        m.playback_touch(self.key, self.sid)
        m.playback_touch(self.key, 'second-viewer-123456789')
        m.playback_release(self.key, self.sid)
        self.assertNotIn(self.key, m.playback_stopped)
        self.assertEqual(m.playback_users(self.key), 1)
        m.playback_release(self.key, 'second-viewer-123456789')
        self.assertIn(self.key, m.playback_stopped)

    def test_missing_close_expires_without_leaking_sessions(self):
        m = self.m
        s = m.playback_touch(self.key, self.sid)
        s['seen'] -= m.PLAYBACK_TTL + 1
        m.playback_sweep()
        self.assertIn(self.key, m.playback_stopped)
        m.playback_sessions[self.sid]['seen'] -= m.PLAYBACK_TOMBSTONE + 1
        m.playback_sweep()
        self.assertNotIn(self.sid, m.playback_sessions)

    def test_release_kills_real_processing_and_releases_its_slot(self):
        m = self.m
        m.playback_touch(self.key, self.sid)
        errors = []
        def process():
            try:
                m.media_run(self.key, [sys.executable, '-c', 'import time; time.sleep(60)'])
            except m.Dropped:
                errors.append('cancelled')
        t = threading.Thread(target=process)
        t.start()
        deadline = time.monotonic() + 2
        while not m.media_processes and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(m.media_processes)
        start = time.monotonic()
        m.playback_release(self.key, self.sid)
        t.join(1)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(errors, ['cancelled'])
        self.assertEqual(m.media_processes, {})

    def test_suspended_pip_can_renew_an_idle_lease_but_not_an_explicit_stop(self):
        m = self.m
        s = m.playback_touch(self.key, self.sid)
        s['seen'] -= m.PLAYBACK_TTL + 1
        m.playback_sweep()
        self.assertIn(self.key, m.playback_stopped)
        m.playback_touch(self.key, self.sid)
        self.assertNotIn(self.key, m.playback_stopped)
        m.playback_release(self.key, self.sid)
        with self.assertRaises(m.Dropped):
            m.playback_touch(self.key, self.sid)

    def test_release_cancels_mac_and_source_connections_without_affecting_others(self):
        m = self.m
        m.playback_touch(self.key, self.sid)
        call, read, other = {}, {'key': self.key}, {'key': 'b' * 16}
        job = {'dropped': False, 'news': queue.Queue(), 'calls': [call]}
        m.mac_jobs[(self.key, 3)] = job
        m.runs.update({1: read, 2: other})
        ended = []
        m.end_call = ended.append
        m.playback_release(self.key, self.sid)
        self.assertTrue(job['dropped'])
        self.assertIn(call, ended)
        self.assertIn(read, ended)
        self.assertNotIn(other, ended)

    def test_stop_during_mac_upload_finishes_body_then_closes_without_waiting(self):
        m = self.m
        began, resume, finished = threading.Event(), threading.Event(), threading.Event()
        received, errors = [], []
        body = b'x' * (8 * 2**20)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                size = int(self.headers['Content-Length'])
                first = self.rfile.read(65536)
                began.set()
                resume.wait(3)
                received.append(len(first) + len(self.rfile.read(size - len(first))))
                # No response: cancellation must release the caller immediately
                # after the body, without waiting for a worker's conversion.
                finished.wait(3)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        hold = {'finish_upload_on_stop': True}
        def send():
            try:
                m.conns.request('POST', f'http://127.0.0.1:{server.server_port}/convert', body=body, hold=hold, timeout=10)
            except (OSError, m.http.client.HTTPException):
                errors.append('cancelled')
        caller = threading.Thread(target=send)
        try:
            caller.start()
            self.assertTrue(began.wait(2))
            self.assertNotIn('sent', hold, 'Fixture must block a partial body upload')
            m.end_call(hold)
            resume.set()
            caller.join(2)
            self.assertFalse(caller.is_alive(), 'Cancellation must not await a response')
            deadline = time.monotonic() + 1
            while not received and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(received, [len(body)], 'A partial request must not strand a Mac slot')
            self.assertEqual(errors, ['cancelled'])
        finally:
            resume.set(); finished.set()
            server.shutdown(); server.server_close(); caller.join(3)

    def test_old_generation_cannot_publish_after_restarting_same_source(self):
        m = self.m
        m.playback_touch(self.key, self.sid)
        entered, resume = threading.Event(), threading.Event()
        results = []
        @m.media_task
        def old_work(key):
            entered.set(); resume.wait(2)
            try:
                m.work_check(key)
                results.append('stale')
            except m.Dropped:
                results.append('cancelled')
        t = threading.Thread(target=old_work, args=(self.key,)); t.start()
        self.assertTrue(entered.wait(1))
        m.playback_release(self.key, self.sid)
        m.playback_touch(self.key, 'new-session-123456789')
        resume.set(); t.join(1)
        self.assertEqual(results, ['cancelled'])

    def test_format_plan_copies_compatible_video_and_encodes_only_when_needed(self):
        m = self.m
        info = {'opos': [0, 10], 'vcodec': 'h264', 'vprofile': 'High', 'w': 1920, 'hdr': 0, 'dv': None}
        self.assertEqual(m.playback_mode(info, 'converted'), 'remux')
        self.assertEqual(m.playback_mode(info, 'original'), 'original')
        self.assertEqual(m.playback_mode(info, 'converted', force=True), 'converted')
        self.assertEqual(m.playback_mode({**info, 'vcodec': 'vc1'}, 'original'), 'converted')
        self.assertEqual(m.playback_mode({**info, 'vprofile': 'High 10'}, 'converted'), 'converted')
        self.assertEqual(m.playback_mode({**info, 'hdr': 1}, 'converted'), 'converted')

    def test_resume_and_seek_select_the_containing_keyframe(self):
        info = {'cuts': [0, 1, 2, 3, 4], 'ocuts': [0, 2, 4]}
        self.assertEqual(self.m.playback_position(info, 3.1), 3)
        self.assertEqual(self.m.playback_position(info, 3.1, True), 1)
        self.assertEqual(self.m.playback_position(info, 999, True), 1)


if __name__ == '__main__':
    unittest.main()
