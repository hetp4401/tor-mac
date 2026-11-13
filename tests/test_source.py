import contextlib
import http.client
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import mac
from prepared_media import PreparedSource, NotPrepared
from source_media import Source, SourceError, NoRedirect, Relay


class SourceValidation(unittest.TestCase):
    def source(self, url='https://tor-1.billybishop4-workers.xyz/mac-source/abc', **more):
        return {'id': 'fixture', 'url': url, 'headers': {'Authorization': 'Bearer source-token'}, **more}

    def test_rejects_untrusted_origin_routes_headers_and_redirects(self):
        for url in ['http://tor-1.billybishop4-workers.xyz/mac-source/abc',
                    'https://billybishop4-workers.xyz.evil.test/mac-source/abc',
                    'https://user@tor-1.billybishop4-workers.xyz/mac-source/abc',
                    'https://tor-1.billybishop4-workers.xyz:8443/mac-source/abc',
                    'https://tor-1.billybishop4-workers.xyz/internal',
                    'https://tor-1.billybishop4-workers.xyz/mac-source/abc?token=secret',
                    'https://tor-1.billybishop4-workers.xyz/mac-source/../secret']:
            with self.subTest(url=url), self.assertRaises(SourceError):
                Source(self.source(url))
        for headers in [{}, {'Cookie': 'secret'}, {'Authorization': 'Bearer a\r\nHost: evil'},
                        {'Authorization': 'Bearer token', 'X-Extra': 'no'}]:
            with self.subTest(headers=headers), self.assertRaises(SourceError):
                Source(self.source(headers=headers))
        with self.assertRaises(SourceError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'http://localhost/private')

    def test_rejects_nonfinite_conversion_settings(self):
        for bad in [float('nan'), float('inf'), -1, 121]:
            with self.subTest(duration=bad), self.assertRaises(ValueError):
                mac.source_options({'start': 0, 'duration': bad})

    def test_tokens_never_appear_in_process_command(self):
        cmd = mac.command(63, 5, 0, 1, 0, 1920, 1, 'http://127.0.0.1:1234/source')
        self.assertIn('-ss', cmd)
        self.assertIn('-t', cmd)
        self.assertNotIn('Authorization', ' '.join(cmd))
        self.assertNotIn('source-token', ' '.join(cmd))


class FixtureHandler(BaseHTTPRequestHandler):
    data = b''
    requests = []
    def log_message(self, *_): pass
    def do_GET(self): self.serve(False)
    def do_HEAD(self): self.serve(True)
    def serve(self, head):
        if self.path != '/mac-source/fixture' or self.headers.get('Authorization') != 'Bearer fixture-token':
            self.send_error(403); return
        size = len(self.data)
        start, end = 0, size - 1
        r = self.headers.get('Range')
        if r:
            a, b = r.removeprefix('bytes=').split('-')
            start, end = int(a or 0), min(int(b) if b else end, end)
        if start >= size:
            self.send_error(416); return
        self.requests.append((start, end, head))
        self.send_response(206 if r else 200)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Content-Type', 'video/x-matroska')
        self.send_header('Accept-Ranges', 'bytes')
        if r: self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if not head:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(self.data[start:end+1])


class FixtureOpener:
    def __init__(self, port): self.port = port
    def open(self, request, timeout):
        # Only tests redirect the validated production URL to a local fixture.
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/mac-source/fixture',
                                     headers=dict(request.header_items()), method=request.get_method())
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=timeout)


class SourceConversion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.file = Path(cls.tmp.name) / 'fixture.mkv'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '18', '-c:v', 'libx264',
                        '-preset', 'ultrafast', '-g', '96', '-keyint_min', '96', '-sc_threshold', '0', '-c:a', 'aac',
                        '-output_ts_offset', '1.5', '-y', str(cls.file)], check=True)
        FixtureHandler.data = cls.file.read_bytes()
        cls.source_server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        cls.source_server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.source_server.serve_forever, daemon=True)
        cls.thread.start()
        cls.fixture_info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(cls.file)]))
        cls.old_engine = mac.ENGINE
        mac.ENGINE = 'cpu'

    @classmethod
    def tearDownClass(cls):
        mac.ENGINE = cls.old_engine
        cls.source_server.shutdown(); cls.source_server.server_close()
        cls.tmp.cleanup()

    def source(self):
        source = Source({'id': hashlib.sha256(FixtureHandler.data).hexdigest(), 'url': 'https://tor-1.billybishop4-workers.xyz/mac-source/fixture',
                         'headers': {'Authorization': 'Bearer fixture-token'}})
        source.opener = FixtureOpener(self.source_server.server_port)
        return source

    def info(self, data):
        path = self.file.with_name('result.ts')
        path.write_bytes(data)
        return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))

    def test_probe_returns_original_stream_indexes_and_keyframes_and_caches(self):
        mac.probe_cache.clear()
        a, b = socket.socketpair()
        try:
            source = self.source()
            result = mac.probe(source, a)
            self.assertEqual(result['streams'][1]['index'], 1)
            self.assertEqual(result['streams'][0]['codec_name'], 'h264')
            self.assertGreaterEqual(len(result['keyframes']), 4)
            self.assertAlmostEqual(result['keyframes'][0][0], 1.5, places=1)
            self.assertNotIn('filename', result['format'])
            again = self.source()
            self.assertEqual(mac.probe(again, a), result)
            self.assertEqual(again.requests, 1)  # a fresh grant is checked even on cached metadata
        finally: a.close(); b.close()

    def test_remote_seek_preserves_requested_time_audio_and_duration(self):
        start, duration, source_start = 8.5, 2.0, 1.5
        source = self.source()
        with Relay(source) as url:
            cmd = mac.command(start, duration, source_start, 1, 0, 320, 1, input_url=url)
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        info = self.info(proc.stdout)
        streams = {x['codec_type']: x for x in info['streams']}
        self.assertEqual(set(streams), {'audio', 'video'})
        self.assertAlmostEqual(float(streams['video']['start_time']), 10 + start - source_start, delta=0.08)
        self.assertAlmostEqual(float(streams['video']['duration']), duration, delta=0.08)
        self.assertGreater(source.requests, 0)
        self.assertGreater(source.bytes + source.cached_bytes, 0)

    def test_legacy_upload_still_converts_same_interval(self):
        cmd = mac.command(8.5, 2, 1.5, 1, 0, 320, 1)
        proc = subprocess.run(cmd, input=FixtureHandler.data, capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        info = self.info(proc.stdout)
        video = next(x for x in info['streams'] if x['codec_type'] == 'video')
        self.assertAlmostEqual(float(video['start_time']), 17.0, delta=0.08)
        self.assertAlmostEqual(float(video['duration']), 2, delta=0.08)

    def test_client_disconnect_kills_processing_promptly(self):
        a, b = socket.socketpair()
        result = []
        thread = threading.Thread(target=lambda: result.append(mac.run(['python3', '-c', 'import time; time.sleep(30)'], None, a)))
        thread.start(); time.sleep(0.15); b.close(); thread.join(3); a.close()
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [None])

    def test_json_http_protocol_auth_health_probe_convert_and_busy(self):
        mac.probe_cache.clear()
        original_source = mac.Source
        original_key = mac.KEY
        mac.KEY = 'worker-test-key'
        mac.Source = lambda value: self.source()
        server = ThreadingHTTPServer(('127.0.0.1', 0), mac.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        payload = {'source': {'id': 'fixture'}, 'start': 8.5, 'duration': 2, 'source_start': 1.5,
                   'audio': 1, 'hdr': 0, 'width': 320, 'segment_seconds': 1, 'priority': 1}
        def call(path, key='worker-test-key'):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            connection.request('POST', path, json.dumps(payload), {'Content-Type': 'application/json', 'X-Mac-Key': key})
            response = connection.getresponse()
            status, data = response.status, response.read()
            connection.close()
            return status, data
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/healthz') as response:
                self.assertIn('source-v1', json.load(response)['capabilities'])
            self.assertEqual(call('/convert', key='wrong')[0], 403)
            status, data = call('/probe')
            self.assertEqual(status, 200, data)
            self.assertGreater(len(json.loads(data)['keyframes']), 1)
            status, data = call('/convert')
            self.assertEqual(status, 200, data)
            self.assertEqual(data[0], 0x47)
            status, data = call('/audio')
            self.assertEqual(status, 200, data)
            audio = self.info(data)['streams']
            self.assertEqual(len(audio), 1)
            self.assertEqual(audio[0]['codec_type'], 'audio')
            payload['original'] = True
            status, data = call('/audio')
            self.assertEqual(status, 200, data)
            self.assertIn(b'moof', data)
            self.assertEqual(self.info(data)['streams'][0]['codec_name'], 'flac')
            mac.jobs['draining'] = True
            self.assertEqual(call('/convert')[0], 503)
            mac.jobs['draining'] = False

            self.assertEqual(mac.jobs['n'], 0)
        finally:
            server.shutdown(); server.server_close()
            mac.Source, mac.KEY = original_source, original_key
            mac.jobs['draining'] = False

    def test_binary_http_reports_stage_timing_and_releases_its_slot(self):
        with mock.patch.object(mac, 'KEY', 'worker-timing-test'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), mac.Handler)
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
                connection.request('POST', '/convert?t=1.5&l=1&s=1.5&a=1&w=320', FixtureHandler.data,
                                   {'X-Mac-Key': 'worker-timing-test', 'Content-Type': 'application/octet-stream'})
                response = connection.getresponse()
                data = response.read()
                self.assertEqual(response.status, 200, data[:200])
                timings = dict(part.strip().split(';dur=') for part in response.getheader('Server-Timing').split(','))
                self.assertEqual(set(timings), {'upload', 'process', 'spawn'})
                self.assertTrue(all(float(value) >= 0 for value in timings.values()))
                self.assertGreater(float(timings['process']), float(timings['spawn']))
                self.assertEqual(data[0], 0x47)
                connection.close()
            finally:
                server.shutdown(); server.server_close()
            self.assertEqual(mac.jobs['n'], 0)

    def test_videotoolbox_remote_seek_when_available(self):
        if self.old_engine != 'videotoolbox':
            self.skipTest('VideoToolbox unavailable on this host')
        mac.ENGINE = 'videotoolbox'
        try:
            with Relay(self.source()) as url:
                proc = subprocess.run(mac.command(8.5, 2, 1.5, 1, 0, 320, 1, input_url=url), capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            video = next(x for x in self.info(proc.stdout)['streams'] if x['codec_type'] == 'video')
            self.assertAlmostEqual(float(video['start_time']), 17, delta=0.08)
            self.assertAlmostEqual(float(video['duration']), 2, delta=0.08)
        finally:
            mac.ENGINE = 'cpu'

    def test_audio_source_codecs_track_selection_and_adjacent_segments(self):
        old_data = FixtureHandler.data
        try:
            for codec in ['aac', 'ac3', 'truehd']:
                with self.subTest(codec=codec):
                    path = self.file.with_name(codec + '.mkv')
                    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=24',
                                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                                    '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
                                    '-t', '12', '-map', '0:v', '-map', '1:a', '-map', '2:a', '-c:v', 'libx264', '-preset', 'ultrafast',
                                    '-g', '96', '-c:a:0', 'aac', '-ac:a:0', '1', '-c:a:1', codec, '-ac:a:1', '6', '-strict', '-2',
                                    '-output_ts_offset', '1.5', '-y', str(path)], check=True)
                    FixtureHandler.data = path.read_bytes()
                    source_start = float(json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(path)]))['format']['start_time'])
                    outputs = []
                    for relative in [7, 8]:
                        with Relay(self.source()) as url:
                            proc = subprocess.run(mac.audio_command(source_start + relative, 1, source_start, 2, False, url), capture_output=True, timeout=30)
                        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
                        info = self.info(proc.stdout)['streams'][0]
                        self.assertEqual(info['codec_name'], 'aac')
                        self.assertEqual(info['channels'], 2)
                        self.assertAlmostEqual(float(info['start_time']), 10 + relative, delta=0.03)
                        self.assertAlmostEqual(float(info['duration']), 1, delta=0.05)
                        outputs.append((proc.stdout, info))
                    first, second = outputs[0][1], outputs[1][1]
                    self.assertAlmostEqual(float(second['start_time']) - float(first['start_time']), 1, delta=0.001)
                    gap = float(second['start_time']) - float(first['start_time']) - float(first['duration'])
                    self.assertLessEqual(gap, 0.001)
                    self.assertGreater(gap, -0.05)  # independent AAC encoder priming/padding
                    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', 'pipe:0', '-ac', '1', '-ar', '8000', '-f', 's16le', 'pipe:1'], input=outputs[0][0])
                    samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)[400:4400]
                    def strength(hz):
                        return abs(sum(v * complex(math.cos(2 * math.pi * hz * i / 8000), math.sin(2 * math.pi * hz * i / 8000)) for i, v in enumerate(samples)))
                    self.assertGreater(strength(880), 8 * strength(440), 'must select second language/track, not default')
                    with Relay(self.source()) as url:
                        proc = subprocess.run(mac.audio_command(source_start + 7, 1, source_start, 2, True, url), capture_output=True, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr.decode())
                    info = self.info(proc.stdout)['streams'][0]
                    self.assertEqual(info['codec_name'], 'flac')
                    self.assertEqual(info['channels'], 6)
                    self.assertAlmostEqual(float(info['duration']), 1, delta=1 / 48000)
                    self.assertIn(b'moof', proc.stdout)
        finally:
            FixtureHandler.data = old_data

    def test_mac_preparation_retains_decode_references_for_seek_and_batch(self):
        old_data = FixtureHandler.data
        a, b = socket.socketpair()
        try:
            for codec in ['h264', 'hevc']:
                path = self.file.with_name('prepared-' + codec + '.mkv')
                encoder = (['-c:v', 'libx264', '-preset', 'veryfast', '-g', '96', '-keyint_min', '96', '-sc_threshold', '0'] if codec == 'h264' else
                           ['-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le', '-x265-params',
                            'pools=2:frame-threads=2:keyint=96:min-keyint=96:scenecut=0:log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc',
                            '-color_primaries', 'bt2020', '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc'])
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '12'] + encoder +
                               ['-c:a', 'aac', '-output_ts_offset', '1.5', '-y', str(path)], check=True)
                FixtureHandler.data = path.read_bytes()
                metadata = mac.probe(self.source(), a)
                origin = float(metadata['format']['start_time'])
                for offset, length in [(0, 1), (7, 1), (7, 3)]:
                    with self.subTest(codec=codec, offset=offset, length=length):
                        prepared = PreparedSource(self.source(), metadata)
                        body, audio = prepared.body(origin + offset, length, origin, 1)
                        self.assertEqual(audio, 1)
                        self.assertLess(len(body), len(FixtureHandler.data))
                        cmd = ['ffmpeg', '-v', 'error', '-copyts', '-i', 'pipe:0', '-map', '0:v:0', '-vf',
                               f'trim=start={origin+offset:.6f}:end={origin+offset+length:.6f}',
                               '-fps_mode', 'passthrough', '-pix_fmt', 'yuv420p10le', '-f', 'rawvideo', 'pipe:1']
                        full = subprocess.run(cmd, input=FixtureHandler.data, capture_output=True, timeout=20)
                        part = subprocess.run(cmd, input=body, capture_output=True, timeout=20)
                        self.assertEqual(full.returncode, 0, full.stderr.decode())
                        self.assertEqual(part.returncode, 0, part.stderr.decode())
                        self.assertTrue(part.stdout)
                        self.assertEqual(part.stdout, full.stdout, 'keyframe/reference extraction must preserve every decoded frame')
                with self.assertRaises(NotPrepared):
                    PreparedSource(self.source(), metadata).body(origin + 7.123, 1, origin, -1)
        finally:
            FixtureHandler.data = old_data
            a.close(); b.close()


if __name__ == '__main__': unittest.main()
