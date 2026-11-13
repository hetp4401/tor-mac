"""Real HTTP, FFprobe, remux, encode, seek and cancellation integration."""
import json
import collections
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playback_harness import backend
import mac


class SourceHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    data = b''
    requests = []
    def log_message(self, *_): pass
    def do_GET(self):
        self.requests.append((self.path, self.headers.get('Range')))
        if self.path == '/bad':
            self.send_response(502); self.send_header('Content-Length', '0'); self.end_headers(); return
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location', '/media'); self.send_header('Content-Length', '0'); self.end_headers(); return
        size = len(self.data)
        r = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range') or '')
        a, b = (int(r[1]), min(int(r[2]) if r[2] else size - 1, size - 1)) if r else (0, size - 1)
        data = self.data[a:b + 1]
        self.send_response(206 if r else 200)
        self.send_header('Content-Length', str(len(data)))
        if r: self.send_header('Content-Range', f'bytes {a}-{b}/{size}')
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError): pass


class PlaybackHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='frame-http-test-')
        cls.fixture = Path(cls.tmp.name) / 'sample.mkv'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '36', '-c:v', 'libx264',
                        '-preset', 'ultrafast', '-profile:v', 'baseline', '-g', '48', '-sc_threshold', '0', '-c:a', 'ac3',
                        '-metadata:s:a:0', 'language=eng', str(cls.fixture)], check=True)
        SourceHandler.data = cls.fixture.read_bytes()
        cls.origin = ThreadingHTTPServer(('127.0.0.1', 0), SourceHandler)
        cls.origin.daemon_threads = True
        threading.Thread(target=cls.origin.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.origin.shutdown(); cls.origin.server_close(); cls.tmp.cleanup()

    def setUp(self):
        self.m = backend()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.m.Handler)
        self.server.daemon_threads = True
        self.m.PAGE_PORT = self.server.server_port
        self.m.url_of = lambda replica: self.base
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.token = self.m.hls_token(f'http://127.0.0.1:{self.origin.server_port}/redirect', 'http-test')
        self.key = self.m.open_token(self.token)[0]
        self.sid = 'http-test-session-123456'
        self.prefix = self.base + '/hls/' + self.token + '/'

    def tearDown(self):
        self.m.playback_release(self.key, self.sid)
        self.server.shutdown(); self.server.server_close()
        deadline = time.monotonic() + 3
        while (self.m.media_processes or self.m.cpu_jobs) and time.monotonic() < deadline:
            time.sleep(.02)
        shutil.rmtree(self.m.SEG_DIR, ignore_errors=True)

    def get(self, url, method='GET'):
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=20) as r:
            return r.read(), r.headers

    def control(self, **params):
        return self.get(self.prefix + 'session?' + urllib.parse.urlencode({'sid': self.sid, **params}), 'POST')

    def master(self, which='index', **extra):
        query = urllib.parse.urlencode({'sid': self.sid, **extra})
        at = time.monotonic()
        data, headers = self.get(self.prefix + which + '.m3u8?' + query)
        return data.decode(), headers, time.monotonic() - at

    def sample(self, url, n=0):
        data, _ = self.get(url)
        text = data.decode()
        init = re.search(r'#EXT-X-MAP:URI="([^"]+)"', text)
        pieces = [line for line in text.splitlines() if line and not line.startswith('#')]
        out = self.get(urllib.parse.urljoin(url, init[1]))[0] if init else b''
        out += self.get(urllib.parse.urljoin(url, pieces[n]))[0]
        return out

    def test_compatible_conversion_copies_video_and_emits_decodable_aac(self):
        master, headers, took = self.master()
        self.assertEqual(headers['X-Playback-Mode'], 'remux')
        self.assertIn('mp4a.40.2', master)
        video = next(l for l in master.splitlines() if l and not l.startswith('#'))
        audio = re.search(r'URI="([^"]+)"', master)[1]
        self.assertIn('sid=' + self.sid, video)
        self.assertIn('/ra', '/' + audio)
        for kind, uri in [('video', video), ('audio', audio)]:
            data = self.sample(urllib.parse.urljoin(self.prefix, uri))
            file = Path(self.tmp.name) / (kind + '.mp4'); file.write_bytes(data)
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(file)], capture_output=True, check=True)
            streams = json.loads(result.stdout)['streams']
            self.assertEqual(streams[0]['codec_name'], 'h264' if kind == 'video' else 'aac')
            decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(file), '-t', '0.5', '-f', 'null', '-'], capture_output=True)
            self.assertEqual(decoded.returncode, 0, decoded.stderr.decode())
        self.assertEqual(self.m.cpu_jobs, {})
        self.assertEqual(self.m.mac_jobs, {})
        self.assertLessEqual(self.m.films[self.key]['buffer_end'], self.m.HLS_AHEAD + 1)
        print(f'fixture remux master {took:.3f}s', flush=True)

    def test_resume_prepares_saved_position_and_stale_seek_cannot_move_it_back(self):
        master, _, took = self.master('orig', start=18)
        info = self.m.probed[self.key]
        expected = self.m.playback_position(info, 18, True)
        self.assertEqual(self.m.films[self.key]['head'], expected)
        self.assertIn('TIME-OFFSET=18.000', master)
        self.control(action='seek', at=28, seq=2)
        self.control(action='seek', at=4, seq=1)
        self.assertEqual(self.m.films[self.key]['head'], self.m.playback_position(info, 28, True))
        self.assertNotIn((self.key + self.m.ORIG, 0), self.m.pieces)

    def test_second_seek_cannot_be_overwritten_by_an_old_media_request(self):
        self.master('orig', managed=1)
        self.control(action='seek', at=6, seq=1)
        old = self.m.playback_position(self.m.probed[self.key], 6, True)
        uri = self.prefix + f'o{old}?r=1&sid={self.sid}&managed=1'
        first, _ = self.get(uri)
        self.control(action='seek', at=26, seq=2)
        wanted = self.m.playback_position(self.m.probed[self.key], 26, True)
        late, _ = self.get(uri)
        self.assertTrue(first.endswith(late), 'A cached response retains the media without early-response padding')
        self.assertEqual(self.m.films[self.key]['head'], wanted, 'Late media cannot undo the second seek')

    def test_prefetch_does_not_move_the_reported_playhead(self):
        self.master('orig', managed=1)
        data, _ = self.get(self.prefix + f'o8?r=1&sid={self.sid}&managed=1')
        self.assertTrue(data)
        self.assertEqual(self.m.films[self.key]['head'], 0, 'Downloaded-ahead video is not the current playhead')

    def test_progress_after_seek_ignores_reports_from_an_older_seek(self):
        self.master('orig', managed=1)
        self.control(action='seek', at=6, seq=1)
        self.control(action='seek', at=26, seq=2)
        self.control(action='keep', at=7, seq=1, managed=1)
        self.assertEqual(self.m.films[self.key]['head'], 13)
        self.control(action='keep', at=28, seq=2, managed=1)
        self.assertEqual(self.m.films[self.key]['head'], 14)

    def test_lost_seek_control_recovers_from_progress_and_playlist_reload(self):
        self.master('orig', managed=1)
        self.control(action='seek', at=26, seq=1)
        self.control(action='keep', at=8, seq=2, managed=1)
        self.assertEqual(self.m.films[self.key]['head'], 4)
        self.control(action='keep', at=28, seq=1, managed=1)
        self.master('orig', managed=1, start=0)
        self.assertEqual(self.m.films[self.key]['head'], 4)

    def test_unknown_media_token_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get(self.base + '/hls/not_a_token/o3')
        self.assertEqual(error.exception.code, 404)

    def test_restart_restores_video_and_lossless_audio_initialization(self):
        m = self.m
        m.playback_touch(self.key, self.sid)
        info = m.probe_source(self.key)
        m.orig_piece(self.key, 0)
        m.audio_piece(self.key, 0, info['a'], True)
        keys = [self.key + m.ORIG, f"{self.key}:oa{info['a']}"]
        before = {key:m.piece_get(key, -1) for key in keys}
        self.assertTrue(all(before.values()))
        with m.hls_lock:
            m.pieces.clear(); m.pieces_size['n'] = 0
            m.mem_pieces.clear(); m.mem_size['n'] = 0
        m.piece_load()
        for key in keys:
            self.assertEqual(m.piece_get(key, -1), before[key])
            self.assertIsNotNone(m.piece_get(key, 0))

    def test_evicted_initialization_is_rebuilt_even_when_media_is_cached(self):
        m = self.m
        m.playback_touch(self.key, self.sid)
        info = m.probe_source(self.key)
        for key, make, codec in [(self.key + m.ORIG, lambda n:m.orig_piece(self.key, n), 'h264'),
                                 (f"{self.key}:oa{info['a']}", lambda n:m.audio_piece(self.key, n, info['a'], True), 'flac')]:
            media = make(0)
            with m.hls_lock:
                cached = m.mem_pieces.pop((key, -1), None)
                m.mem_size['n'] -= len(cached) if cached is not None else 0
                m.pieces_size['n'] -= m.pieces.pop((key, -1), 0)
            Path(m.piece_path(key, -1)).unlink()
            self.assertEqual(m.piece_get(key, 0), media)
            init = m.init_piece(self.key, key, make)
            self.assertTrue(init, 'Cached media cannot replace a missing initialization fragment')
            file = Path(self.tmp.name) / ('restored-' + codec + '.mp4')
            file.write_bytes(init + media)
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(file)], capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)['streams'][0]['codec_name'], codec)

    def test_explicit_stop_rejects_late_media_and_reaches_zero_work(self):
        self.master('index', force=1)
        self.control(action='stop')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get(self.prefix + 'index.m3u8?sid=' + self.sid)
        self.assertEqual(error.exception.code, 410)
        deadline = time.monotonic() + 3
        while (self.m.cpu_jobs or self.m.media_processes or self.m.mac_jobs) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(self.m.cpu_jobs, {})
        self.assertEqual(self.m.media_processes, {})
        self.assertEqual(self.m.mac_jobs, {})
        self.assertEqual(self.m.films[self.key]['filling'], 0)

    def test_encoded_video_uses_real_mac_worker_and_seeks_to_requested_time(self):
        worker = ThreadingHTTPServer(('127.0.0.1', 0), mac.Handler)
        worker.daemon_threads = True
        threading.Thread(target=worker.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{worker.server_port}'
        old_key = mac.KEY
        mac.KEY = self.m.MAC_KEY = "local-playback-fixture"
        self.m.MAC_URLS = [url]
        self.m.macs[url] = {'up': True, 'health': None, 'inflight': 0, 'readied': 0, 'full_until': 0,
            'done': 0, 'misses': 0, 'fails': 0, 'suspect': False, 'out_until': 0, 'look_at': 0, 'looking': False,
            'calls': [], 'took': collections.deque(maxlen=30), 'answered': 0}
        try:
            master, headers, _ = self.master(force=1, start=20)
            self.assertEqual(headers['X-Playback-Mode'], 'converted')
            video = next(l for l in master.splitlines() if l and not l.startswith('#'))
            n = self.m.playback_position(self.m.probed[self.key], 20)
            start = time.monotonic()
            data = self.sample(urllib.parse.urljoin(self.prefix, video), n)
            file = Path(self.tmp.name) / 'converted.ts'; file.write_bytes(data)
            probed = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(file)], capture_output=True, check=True)
            v = json.loads(probed.stdout)['streams'][0]
            self.assertEqual(v['codec_name'], 'h264')
            self.assertAlmostEqual(float(v['start_time']), 30, delta=.1)
            self.assertGreater(self.m.macs[url]['done'], 0)
            print(f'fixture Mac seek segment {time.monotonic() - start:.3f}s; engine {mac.ENGINE}', flush=True)
        finally:
            self.control(action='stop')
            worker.shutdown(); worker.server_close()
            mac.KEY = old_key

    def test_unavailable_source_fails_once_instead_of_repeated_probes(self):
        token = self.m.hls_token(f'http://127.0.0.1:{self.origin.server_port}/bad', 'unavailable-test')
        before = len([x for x in SourceHandler.requests if x[0] == '/bad'])
        start = time.monotonic()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get(self.base + '/hls/' + token + '/orig.m3u8?sid=' + self.sid)
        self.assertEqual(error.exception.code, 503)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(len([x for x in SourceHandler.requests if x[0] == '/bad']) - before, 1)
        key = self.m.open_token(token)[0]
        self.m.playback_release(key, self.sid)
        self.sid = 'unused-session-123456789'

    def test_as_is_ranges_stream_without_probing_or_prefetching(self):
        url = self.base + '/file/' + self.token + '/fixture.mkv?sid=' + self.sid
        for requested, start, end in [('bytes=2-1025', 2, 1025), ('bytes=-512', len(SourceHandler.data)-512, len(SourceHandler.data)-1)]:
            request = urllib.request.Request(url, headers={'Range': requested})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), SourceHandler.data[start:end+1])
        self.assertNotIn(self.key, self.m.probed)
        self.assertEqual(self.m.src_blocks, {})
        self.assertEqual(self.m.media_processes, {})
        # Previously cached probe metadata must not turn direct-file progress
        # into speculative HLS conversion after a mode switch.
        self.m.probe_source(self.key)
        self.control(action='keep', at=8, seq=0, managed=1)
        self.control(action='seek', at=24, seq=1, managed=1)
        self.assertNotIn(self.key, self.m.films)
        self.assertEqual(self.m.mac_jobs, {})


if __name__ == '__main__':
    unittest.main()
