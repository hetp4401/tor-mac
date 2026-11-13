"""Local VideoToolbox A/B microbenchmark; synthetic compact MKV, no live sources.

Run from repository root:
  python3 benchmark/local_profiles.py --directory /tmp/tor-worker-profiles --trials 5
Profiles never alter encoder bitrate, quality preference or output dimensions.
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import socket
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mac
from media_index import keyframes
from prepared_media import PreparedSource


PROFILES = {
    "baseline": [],
    "known-mkv": ["-f", "matroska"],
    "probe-256k": ["-f", "matroska", "-probesize", "262144", "-analyzeduration", "250000", "-fpsprobesize", "0"],
    "probe-64k": ["-f", "matroska", "-probesize", "65536", "-analyzeduration", "50000", "-fpsprobesize", "0"],
    "decode-1": ["-f", "matroska", "-probesize", "262144", "-analyzeduration", "250000", "-fpsprobesize", "0", "-threads", "1"],
}



def profile_command(*args, profile="baseline", **kwargs):
    """Build experimental input-only flags locally; production has no profile API."""
    if profile not in PROFILES:
        raise ValueError("unknown experiment profile")
    if kwargs.get("input_url") or (len(args) > 7 and args[7] is not None):
        raise ValueError("profiles apply only to compact Matroska input")
    cmd = mac.command(*args, **kwargs)
    at = cmd.index("-copyts")
    return cmd[:at] + PROFILES[profile] + cmd[at:]


class MemorySource:
    def __init__(self, data): self.data = data
    def size(self): return len(self.data)
    def read_range(self, start, size): return self.data[start:start+size]


def execute(cmd, **kwargs):
    result = subprocess.run(cmd, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-800:])
    return result.stdout


def make_fixture(directory, codec):
    path = directory / (codec + '.mkv')
    if path.exists(): return path
    hdr = codec == 'hevc'
    size = '1920x1080' if hdr else '1280x720'
    encode = (['-c:v', 'libx265', '-preset', 'ultrafast', '-crf', '29', '-pix_fmt', 'yuv420p10le',
               '-x265-params', 'pools=2:frame-threads=2:keyint=240:min-keyint=240:scenecut=0:log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc',
               '-color_primaries', 'bt2020', '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc'] if hdr else
              ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24', '-threads', '2', '-g', '240', '-keyint_min', '240', '-sc_threshold', '0'])
    execute(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=24',
             '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '45'] + encode +
            ['-c:a', 'aac', '-b:a', '128k', '-output_ts_offset', '1.5', '-y', str(path)], timeout=600)
    return path


def examine(data, path):
    path.write_bytes(data)
    info = json.loads(execute(['ffprobe', '-v', 'error', '-count_frames', '-show_entries',
                              'stream=codec_type,codec_name,width,height,start_time,duration,nb_read_frames:format=duration', '-of', 'json', str(path)]))
    raw = execute(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-pix_fmt', 'yuv420p', '-f', 'rawvideo', 'pipe:1'])
    return {'streams': info['streams'], 'decoded_video_sha256': hashlib.sha256(raw).hexdigest(),
            'output_sha256': hashlib.sha256(data).hexdigest(), 'output_bytes': len(data)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--trials', type=int, default=5)
    parser.add_argument('--profiles', default=','.join(PROFILES))
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    profiles = args.profiles.split(',')
    report = {'engine': mac.ENGINE, 'trials': args.trials, 'cases': [], 'rows': []}
    if mac.ENGINE != 'videotoolbox': raise RuntimeError('This benchmark requires actual VideoToolbox')
    a, b = socket.socketpair()
    try:
        for codec in ['h264', 'hevc']:
            fixture = make_fixture(args.directory, codec)
            original = fixture.read_bytes()
            metadata = json.loads(execute(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(fixture)]))
            metadata['keyframes'] = keyframes(MemorySource(original))
            source_start = float(metadata['format']['start_time'])
            video = next(x for x in metadata['streams'] if x['codec_type'] == 'video')
            for offset in [0, 37]:
                for audio in [-1, 1]:
                    body, selected_audio = PreparedSource(MemorySource(original), metadata).body(source_start + offset, 1, source_start, audio)
                    case = {'codec': codec, 'offset': offset, 'audio': audio, 'input_bytes': len(body), 'validation': {}}
                    report['cases'].append(case)
                    # Alternate the profile order each trial to reduce thermal/order bias.
                    outputs = {}
                    for trial in range(args.trials):
                        order = profiles if trial % 2 == 0 else list(reversed(profiles))
                        for profile in order:
                            cmd = profile_command(source_start + offset, 1, source_start, selected_audio, int(codec == 'hevc'), video['width'], profile=profile)
                            timing = {}
                            before = resource.getrusage(resource.RUSAGE_CHILDREN)
                            result = mac.run(cmd, body, a, timing=timing)
                            after = resource.getrusage(resource.RUSAGE_CHILDREN)
                            if result is None: raise RuntimeError('Unexpected cancellation')
                            code, output, error, late = result
                            if code or late or not output: raise RuntimeError(f'{profile}: {error.decode(errors="replace")[-300:]}')
                            report['rows'].append({'codec': codec, 'offset': offset, 'audio': audio, 'trial': trial, 'profile': profile,
                                                   **timing, 'child_cpu_s': (after.ru_utime-before.ru_utime)+(after.ru_stime-before.ru_stime),
                                                   'output_bytes': len(output), 'output_sha256': hashlib.sha256(output).hexdigest()})
                            outputs.setdefault(profile, output)
                    for profile, output in outputs.items():
                        case['validation'][profile] = examine(output, args.directory / 'last-output.ts')
                    reference = case['validation']['baseline']
                    for profile, result in case['validation'].items():
                        result['decoded_equal_baseline'] = result['decoded_video_sha256'] == reference['decoded_video_sha256']
                        result['encoded_equal_baseline'] = result['output_sha256'] == reference['output_sha256']
                    summary = {p: round(statistics.median(r['process_s'] for r in report['rows'] if r['codec']==codec and r['offset']==offset and r['audio']==audio and r['profile']==p), 4) for p in profiles}
                    print(json.dumps({'codec': codec, 'offset': offset, 'audio': audio, 'median_process_s': summary,
                                      'decoded_equal': {p:v['decoded_equal_baseline'] for p,v in case['validation'].items()}}), flush=True)
                    (args.directory / 'result.json').write_text(json.dumps(report, indent=2))
    finally: a.close(); b.close()


if __name__ == '__main__': main()
