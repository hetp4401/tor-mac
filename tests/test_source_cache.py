"""Origin traffic, authorization and cancellation tests for the shared byte cache."""
import concurrent.futures
import contextlib
import http.client
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import source_media as media


class Origin(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    data = bytes(range(251)) * (12 * 2**20 // 251)
    requests = []
    delay = 0
    slow_body = False
    body_started = threading.Event()
    release_body = threading.Event()
    lock = threading.Lock()

    def log_message(self, *_): pass
    def do_HEAD(self): self.serve(True)
    def do_GET(self): self.serve(False)

    def serve(self, head):
        auth = self.headers.get('Authorization') == 'Bearer valid-source-token'
        requested = self.headers.get('Range')
        with self.lock:
            self.requests.append((self.command, requested, auth))
        if not auth:
            self.send_response(403); self.send_header('Content-Length', '0'); self.end_headers(); return
        size = len(self.data)
        start, end = 0, size - 1
        if requested:
            first, last = requested.removeprefix('bytes=').split('-')
            start, end = int(first), min(int(last), size - 1)
        self.send_response(206 if requested else 200)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Content-Type', 'video/x-matroska')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', '"fixture-v1"')
        if requested: self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if not head:
            if self.delay: time.sleep(self.delay)
            if self.slow_body:
                self.body_started.set(); self.release_body.wait(5)
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(self.data[start:end + 1])


class FixtureOpener:
    def __init__(self, port): self.port = port
    def open(self, request, timeout):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/mac-source/cache-fixture',
                                     headers=dict(request.header_items()), method=request.get_method())
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), media.NoRedirect()).open(req, timeout=timeout)


class SourceCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(2)

    def setUp(self):
        self.old_cache = media.CACHE
        media.CACHE = media.BlockCache()
        Origin.requests.clear(); Origin.delay = 0; Origin.slow_body = False
        Origin.body_started.clear(); Origin.release_body.clear()

    def tearDown(self):
        Origin.release_body.set()
        media.CACHE = self.old_cache

    def source(self, token='valid-source-token', stable='fixture-v1'):
        source = media.Source({'id': stable, 'url': 'https://tor-1.billybishop4-workers.xyz/mac-source/cache-fixture',
                               'headers': {'Authorization': 'Bearer ' + token}})
        source.opener = FixtureOpener(self.server.server_port)
        return source

    def get(self, url, byte_range=None, method='GET'):
        request = urllib.request.Request(url, headers={'Range': byte_range} if byte_range else {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as response:
            with response: return response.code, dict(response.headers), response.read()

    def test_small_read_fetches_one_bounded_block_and_next_job_reuses_it(self):
        first = self.source()
        self.assertEqual(first.read_range(100, 200), Origin.data[100:300])
        self.assertEqual(first.bytes, media.BLOCK_SIZE)
        self.assertEqual(Origin.requests, [('HEAD', None, True), ('GET', f'bytes=0-{media.BLOCK_SIZE-1}', True)])
        second = self.source()
        self.assertEqual(second.read_range(150, 50), Origin.data[150:200])
        self.assertEqual(second.bytes, 0)
        self.assertEqual(second.requests, 1)
        self.assertEqual(second.cache_hits, 1)
        self.assertEqual(Origin.requests[-1], ('HEAD', None, True))

    def test_cached_bytes_are_never_served_to_wrong_or_expired_authorization(self):
        self.source().read_range(0, 8)
        for token in ['wrong', 'expired']:
            bad = self.source(token)
            with self.assertRaisesRegex(media.SourceError, 'HTTP 403'): bad.read_range(0, 8)
            self.assertEqual(bad.cache_hits, 0); self.assertEqual(bad.bytes, 0)
        self.assertEqual(len([r for r in Origin.requests if r[0] == 'GET']), 1)

    def test_source_identity_separates_equal_length_cache_entries(self):
        self.source(stable='a').read_range(0, 8)
        different = self.source(stable='b'); different.read_range(0, 8)
        self.assertEqual(different.bytes, media.BLOCK_SIZE)

    def test_lru_evicts_bytes_and_ttl_expires(self):
        media.CACHE = media.BlockCache(max_bytes=2 * media.BLOCK_SIZE, ttl=600)
        source = self.source()
        source.read_range(0, 1); source.read_range(media.BLOCK_SIZE, 1)
        source.read_range(0, 1); source.read_range(2 * media.BLOCK_SIZE, 1)
        self.assertEqual(media.CACHE.bytes, 2 * media.BLOCK_SIZE)
        before = source.bytes; source.read_range(media.BLOCK_SIZE, 1)
        self.assertEqual(source.bytes - before, media.BLOCK_SIZE)
        with patch('source_media.time.monotonic', return_value=time.monotonic() + 601):
            source.read_range(media.BLOCK_SIZE, 1)
        self.assertLessEqual(media.CACHE.bytes, 2 * media.BLOCK_SIZE)
        self.assertEqual(source.bytes - before, 2 * media.BLOCK_SIZE)

    def test_concurrent_jobs_share_one_block_fetch_after_individual_auth(self):
        Origin.delay = .15
        sources = [self.source() for _ in range(6)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda source: source.read_range(100, 1000), sources))
        self.assertTrue(all(result == Origin.data[100:1100] for result in results))
        self.assertEqual(len([r for r in Origin.requests if r[0] == 'HEAD']), 6)
        self.assertEqual(len([r for r in Origin.requests if r[0] == 'GET']), 1)
        self.assertEqual(sum(source.bytes for source in sources), media.BLOCK_SIZE)

    def test_relay_range_suffix_head_and_eof(self):
        source = self.source()
        with media.Relay(source) as url:
            for requested, expected in [('bytes=10-19', Origin.data[10:20]), ('bytes=-10', Origin.data[-10:]),
                                        (f'bytes={len(Origin.data)-9}-', Origin.data[-9:])]:
                status, headers, data = self.get(url, requested)
                self.assertEqual((status, data), (206, expected))
                self.assertEqual(headers['Content-Length'], str(len(expected)))
            before = source.bytes
            status, headers, data = self.get(url, 'bytes=10-19', 'HEAD')
            self.assertEqual((status, data), (206, b'')); self.assertEqual(headers['Content-Length'], '10')
            self.assertEqual(source.bytes, before)
            for requested in ['bytes=9-2', 'bytes=-0', 'bytes=-', f'bytes={len(Origin.data)}-', 'bytes=0-2,7-9', 'nonsense']:
                self.assertEqual(self.get(url, requested)[0], 416)
        for method, requested, _ in Origin.requests:
            if method == 'GET':
                first, last = map(int, requested.removeprefix('bytes=').split('-'))
                self.assertLessEqual(last - first + 1, media.BLOCK_SIZE)

    def test_open_ended_client_disconnect_has_only_bounded_origin_read_ahead(self):
        source = self.source()
        with media.Relay(source) as url:
            port = urllib.parse.urlsplit(url).port
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            connection.request('GET', '/source', headers={'Range': 'bytes=0-'})
            response = connection.getresponse()
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(1024), Origin.data[:1024])
            response.close(); connection.close(); time.sleep(.1)
        self.assertLessEqual(source.bytes, 2 * media.BLOCK_SIZE)
        self.assertLess(source.bytes, len(Origin.data) // 4)
        self.assertTrue(all(r[1] and not r[1].endswith('-') for r in Origin.requests if r[0] == 'GET'))

    def test_cancellation_interrupts_active_origin_body_read(self):
        Origin.slow_body = True
        source, failures = self.source(), []
        def read():
            try: source.read_range(0, 100)
            except media.SourceError as error: failures.append(str(error))
        thread = threading.Thread(target=read); thread.start()
        self.assertTrue(Origin.body_started.wait(2))
        start = time.monotonic(); source.cancel(); thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(failures); self.assertFalse(source.active); self.assertEqual(media.CACHE.bytes, 0)

    def test_origin_api_refuses_open_ended_or_oversized_get(self):
        source = self.source()
        for byte_range in [None, 'bytes=0-', 'bytes=-5', f'bytes=0-{media.BLOCK_SIZE}', 'bytes=2-1']:
            with self.assertRaises(media.SourceError): source.open(byte_range=byte_range)
        self.assertFalse(Origin.requests)


if __name__ == '__main__': unittest.main(verbosity=2)
