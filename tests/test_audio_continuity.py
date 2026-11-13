"""Decode adjacent real AAC pieces: timestamp checks alone miss silent priming."""
import json
import math
from pathlib import Path
import statistics
import struct
import subprocess
import concurrent.futures
import sys
import threading
import unittest
from unittest import mock

import test_playback_http as harness


class AudioContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.PlaybackHTTPTests.setUpClass()
        cls.sources = {}
        for codec, rate in [('ac3', 48000), ('aac', 48000), ('eac3', 48000), ('truehd', 48000), ('aac', 44100)]:
            file = Path(harness.PlaybackHTTPTests.tmp.name) / f'{codec}-{rate}.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=24',
                            '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate={rate}',
                            '-f', 'lavfi', '-i', f'sine=frequency=880:sample_rate={rate}',
                            '-t', '14', '-map', '0:v', '-map', '1:a', '-map', '2:a',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '48', '-sc_threshold', '0',
                            '-c:a:0', 'aac', '-c:a:1', codec, '-ac:a:1', '2', '-strict', '-2',
                            '-output_ts_offset', '1.5', str(file)], check=True)
            cls.sources[codec, rate] = file.read_bytes()

    @classmethod
    def tearDownClass(cls):
        harness.PlaybackHTTPTests.tearDownClass()

    def setUp(self):
        self.t = harness.PlaybackHTTPTests()
        self.t.setUp()
        self.m = self.t.m
        self.m.MAC_URLS = []
        self.m.playback_touch(self.t.key, self.t.sid, 'converted')

    def tearDown(self):
        self.t.tearDown()

    def probe(self, codec='ac3', rate=48000):
        harness.SourceHandler.data = self.sources[codec, rate]
        info = self.m.probe_source(self.t.key)
        self.assertEqual(len(info['audios']), 2)
        return info

    def assert_continuous(self, pieces, starts, end):
        previous = None
        for data, at in zip(pieces, starts):
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_packets', '-show_streams',
                                     '-select_streams', 'a', '-of', 'json', 'pipe:0'], input=data, capture_output=True, check=True)
            decoded = json.loads(result.stdout)
            self.assertEqual(decoded['streams'][0]['sample_rate'], '48000')
            self.assertEqual(decoded['streams'][0]['channels'], 2)
            packets = decoded['packets']
            expected = 10 + math.ceil(at * 48000 / 1024 - 1e-8) * 1024 / 48000
            self.assertAlmostEqual(float(packets[0]['pts_time']), expected, delta=1/90000)
            for packet in packets:
                pts = float(packet['pts_time'])
                if previous is not None:
                    self.assertAlmostEqual(pts, previous, delta=1/90000,
                                           msg='AAC packets must neither overlap nor leave a hole')
                previous = pts + float(packet['duration_time'])
        expected_end = 10 + math.ceil(end * 48000 / 1024 - 1e-8) * 1024 / 48000
        self.assertAlmostEqual(previous, expected_end, delta=1/90000)
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', 'pipe:0', '-map', '0:a:0',
                                  '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1'],
                                 input=b''.join(pieces), capture_output=True, check=True)
        self.assertNotIn(b'non monotonically', decoded.stderr)
        samples = struct.unpack('<' + 'f' * (len(decoded.stdout)//4), decoded.stdout)
        # A steady tone must stay audible through every encoder boundary. The
        # old implementation produced ~30 ms silent dips and a large click.
        size = 240  # 5 ms, deliberately much smaller than an AAC frame.
        rms = [math.sqrt(sum(x*x for x in samples[i:i+size])/size)
               for i in range(2400, len(samples)-2400, size)]
        median = statistics.median(rms)
        self.assertGreater(min(rms), median * .75, 'silent/attenuated audio at an internal cut')
        changes = [abs(x-y) for x,y in zip(samples[2400:-2400], samples[2399:-2401])]
        self.assertLess(max(changes), median * .4, 'audible click at an internal cut')
        # The requested second track is 880 Hz, not the default 440 Hz.
        window = samples[4800:9600]
        def strength(hz):
            return abs(sum(x * complex(math.cos(2*math.pi*hz*i/48000), math.sin(2*math.pi*hz*i/48000))
                           for i,x in enumerate(window)))
        self.assertGreater(strength(880), strength(440)*8)

    def test_indexed_codec_and_sample_rate_boundaries(self):
        # Each subtest has a fresh backend/cache so no previous piece can hide
        # a codec-specific failure. Test both normal playback and a cold seek.
        for index, (codec, rate) in enumerate(self.sources):
            if index:
                self.t.tearDown()
                self.setUp()
            with self.subTest(codec=codec, rate=rate):
                info = self.probe(codec, rate)
                ns = [2, 3, 4, 5]
                pieces = [self.m.audio_make(self.t.key, n, 2, False)[1] for n in ns]
                self.assert_continuous(pieces, [info['cuts'][n] for n in ns], info['cuts'][ns[-1]+1])

    def test_batch_and_single_requests_join_without_padding(self):
        info = self.probe()
        pieces = [self.m.audio_make(self.t.key, 0, 2, False)[1]]
        batch = self.m.audio_run(self.t.key, [1, 2, 3], 2, keep=False)
        pieces += [batch[n] for n in [1, 2, 3]]
        pieces.append(self.m.audio_make(self.t.key, 4, 2, False)[1])
        self.assert_continuous(pieces, info['cuts'][:5], info['cuts'][5])
        self.assertEqual(self.m.media_processes, {})

    def test_compatible_original_video_uses_the_same_continuous_aac(self):
        info = self.probe('aac', 44100)
        pieces = [self.m.audio_make(self.t.key, n, 2, 'aac')[1] for n in [1, 2, 3]]
        self.assert_continuous(pieces, info['ocuts'][1:4], info['ocuts'][4])

    def test_unindexed_http_source_keeps_exact_boundaries(self):
        info = self.probe('aac', 44100)
        # Exercise the seekable HTTP fallback with the same reference source.
        info['pos'] = None
        pieces = [self.m.audio_make(self.t.key, n, 2, False)[1] for n in [1, 2, 3]]
        self.assert_continuous(pieces, info['cuts'][1:4], info['cuts'][4])

    def test_old_cached_aac_is_not_reused(self):
        self.probe()
        for mode, prefix in [(False, 'ca'), ('aac', 'ra')]:
            self.m.piece_put(f'{self.t.key}:{prefix}2', 0, b'obsolete audio')
            current = self.m.audio_piece(self.t.key, 0, 2, mode)
            self.assertNotEqual(current, b'obsolete audio')
            self.assertEqual(current, self.m.piece_get(self.m.audio_key(self.t.key, 2, mode), 0))

    def test_stopping_playback_cancels_an_audio_batch(self):
        self.probe()
        launched = threading.Event()
        spawn = subprocess.Popen
        def held_encoder(cmd, **options):
            process = spawn([sys.executable, '-c', 'import time; time.sleep(30)'], **options)
            launched.set()
            return process
        with mock.patch.object(self.m.subprocess, 'Popen', side_effect=held_encoder), concurrent.futures.ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.m.audio_run, self.t.key, [0, 1], 2, False)
            self.assertTrue(launched.wait(5))
            self.m.playback_release(self.t.key, self.t.sid)
            with self.assertRaises(self.m.Dropped):
                pending.result(timeout=5)
        self.assertEqual(self.m.media_processes, {})


if __name__ == '__main__':
    unittest.main()
