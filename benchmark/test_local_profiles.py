"""Check local-only codec experiments against unchanged production output."""
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest

import mac
from benchmark.local_profiles import MemorySource, execute, PROFILES, profile_command
from media_index import keyframes
from prepared_media import PreparedSource


class ProfileMediaTests(unittest.TestCase):
    def test_profiles_keep_sparse_gop_nonzero_timestamps_audio_and_video(self):
        a, b = socket.socketpair()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for video_codec, audio_codec in [('h264', 'aac'), ('h264', 'ac3'), ('h264', 'truehd'), ('hevc', 'aac')]:
                    path = root / f'{video_codec}-{audio_codec}.mkv'
                    video_flags = (['-c:v', 'libx264', '-preset', 'veryfast', '-g', '96', '-keyint_min', '96', '-sc_threshold', '0']
                                   if video_codec == 'h264' else ['-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le',
                                     '-x265-params', 'pools=2:frame-threads=2:keyint=96:min-keyint=96:scenecut=0:log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc',
                                     '-color_primaries', 'bt2020', '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc'])
                    execute(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                             '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000', '-t', '12'] + video_flags +
                            ['-c:a', audio_codec, '-ac', '6', '-strict', '-2', '-output_ts_offset', '1.5', str(path)])
                    raw = path.read_bytes()
                    metadata = json.loads(execute(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))
                    metadata['keyframes'] = keyframes(MemorySource(raw))
                    origin = float(metadata['format']['start_time'])
                    for offset, audio in [(0, -1), (7, -1), (7, 1)]:
                        body, selected = PreparedSource(MemorySource(raw), metadata).body(origin + offset, 1, origin, audio)
                        baseline = None
                        for profile in PROFILES:
                            with self.subTest(video=video_codec, audio=audio_codec, offset=offset, selected=audio, profile=profile):
                                timing = {}
                                result = mac.run(profile_command(origin + offset, 1, origin, selected, int(video_codec=='hevc'), 320, profile=profile), body, a, timing=timing)
                                code, data, errors, late = result
                                self.assertEqual(code, 0, errors.decode(errors='replace'))
                                self.assertFalse(late)
                                self.assertGreater(timing['process_s'], timing['spawn_s'])
                                output = root / 'output.ts'; output.write_bytes(data)
                                info = json.loads(execute(['ffprobe', '-v', 'error', '-count_frames', '-show_entries',
                                                          'stream=codec_type,codec_name,width,height,start_time,duration,nb_read_frames', '-of', 'json', str(output)]))
                                decoded_video = execute(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:v:0', '-f', 'rawvideo', '-pix_fmt', 'yuv420p', 'pipe:1'])
                                summary = {'streams': info['streams'], 'video': hashlib.sha256(decoded_video).hexdigest()}
                                if audio >= 0:
                                    pcm = execute(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:a:0', '-f', 's16le', 'pipe:1'])
                                    summary['audio'] = hashlib.sha256(pcm).hexdigest()
                                if baseline is None: baseline = summary
                                self.assertEqual(summary, baseline)
                                video = next(x for x in info['streams'] if x['codec_type']=='video')
                                self.assertEqual(int(video['nb_read_frames']), 24)
                                self.assertAlmostEqual(float(video['start_time']), 10 + offset, delta=0.05)
        finally:
            a.close(); b.close()


if __name__ == '__main__': unittest.main()
