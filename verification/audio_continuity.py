#!/usr/bin/env python3
"""Compare decoded audio at joins using identical local media, with no live sessions."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import playback_harness
import test_playback_http as harness

BASELINE = 'f5d8b2e46757d7ddfbd19164df255d24b3c18f82'


def measure(path):
    with mock.patch.object(harness, 'backend', side_effect=lambda: playback_harness.backend(path)):
        test = harness.PlaybackHTTPTests()
        test.setUp()
    try:
        m = test.m
        m.MAC_URLS = []
        m.playback_touch(test.key, test.sid, 'converted')
        info = m.probe_source(test.key)
        parts, spans, costs = [], [], []
        for n in range(4):
            began = time.monotonic()
            data = m.audio_make(test.key, n, info['a'], False)[1]
            costs.append((time.monotonic() - began) * 1000)
            parts.append(data)
            packets = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_packets',
                '-select_streams', 'a', '-of', 'json', 'pipe:0'], input=data))['packets']
            spans.append([float(packets[0]['pts_time']), float(packets[-1]['pts_time']) + float(packets[-1]['duration_time'])])
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', 'pipe:0', '-ac', '1', '-ar', '48000',
                                  '-f', 'f32le', 'pipe:1'], input=b''.join(parts), capture_output=True, check=True)
        samples = struct.unpack('<' + 'f' * (len(decoded.stdout)//4), decoded.stdout)
        rms = [math.sqrt(sum(x*x for x in samples[i:i+240])/240) for i in range(2400, len(samples)-2400, 240)]
        median = statistics.median(rms)
        return {'segment_spans': spans,
                'join_gaps_ms': [round((b[0]-a[1])*1000, 6) for a,b in zip(spans, spans[1:])],
                'quiet_5ms_windows': sum(x < median*.4 for x in rms),
                'minimum_to_median_amplitude': round(min(rms)/median, 4),
                'maximum_sample_step': round(max(abs(x-y) for x,y in zip(samples[2400:-2400], samples[2399:-2401])), 5),
                'median_preparation_ms': round(statistics.median(costs), 1),
                'timestamp_warning': b'non monotonically' in decoded.stderr}
    finally:
        test.tearDown()


def main():
    harness.PlaybackHTTPTests.setUpClass()
    try:
        with tempfile.TemporaryDirectory(prefix='frame-audio-baseline-') as td:
            old = Path(td) / 'tor.py'
            old.write_bytes(subprocess.check_output(['git', 'show', BASELINE + ':runners/tor.py'], cwd=ROOT))
            result = {'baseline_commit': BASELINE,
                      'code': hashlib.sha256((ROOT/'runners/tor.py').read_bytes()).hexdigest()[:12],
                      'checked_at': time.time(), 'source': 'local AC-3 48 kHz steady tone',
                      'baseline': measure(old), 'fixed': measure(ROOT/'runners/tor.py'),
                      'timing_scope': 'Local audio preparation only; not browser startup or production seek latency.'}
            assert result['fixed']['quiet_5ms_windows'] == 0
            assert max(abs(x) for x in result['fixed']['join_gaps_ms']) <= .012
            (ROOT/'verification/audio-boundary-result.json').write_text(json.dumps(result, indent=2)+'\n')
            print(json.dumps(result))
    finally:
        harness.PlaybackHTTPTests.tearDownClass()


if __name__ == '__main__':
    main()
