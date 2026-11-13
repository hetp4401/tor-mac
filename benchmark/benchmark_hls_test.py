import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest

if __package__:
    from .benchmark_hls import Pool, SafeFailure, master_links, segments
else:
    from benchmark_hls import Pool, SafeFailure, master_links, segments


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    internal_headers = []
    def log_message(self, *_):
        pass
    def do_GET(self):
        type(self).internal_headers.append(self.headers.get("X-Tor-Key"))
        if self.path.startswith("/fail"):
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "376")
        self.end_headers()
        self.wfile.write(b"\x47\x1f\xff\x10" + b"\xff" * 184)
        self.wfile.flush()
        time.sleep(.04)
        self.wfile.write(b"\x47\x00\x00\x10" + b"\x00" * 184)
        self.wfile.flush()


class HlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_keepalive_bytes_are_not_media_latency(self):
        pool = Pool(3)
        try:
            data, timing = pool.fetch(self.base + "/hls/sensitive-token/s0?r=1", "video", True)
            self.assertEqual(len(data), 376)
            self.assertGreater(timing["first_non_null_ts_s"] - timing["first_byte_s"], .02)
            self.assertNotIn("sensitive-token", json.dumps(timing))
            self.assertIsNone(Handler.internal_headers[-1])
        finally:
            pool.close()

    def test_errors_do_not_expose_signed_url(self):
        pool = Pool(3)
        try:
            with self.assertRaises(SafeFailure) as got:
                pool.fetch(self.base + "/fail/sensitive-token", "video")
            self.assertEqual(str(got.exception), "video: HTTP 503")
        finally:
            pool.close()

    def test_default_audio_and_relative_segment_resolution(self):
        master = b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,DEFAULT=NO,URI="ca2.m3u8?r=1"\n#EXT-X-MEDIA:TYPE=AUDIO,DEFAULT=YES,URI="ca1.m3u8?r=1"\n#EXT-X-STREAM-INF:BANDWIDTH=100\ncmedia.m3u8?r=1\n'
        video, audio = master_links(master, "https://example.test/hls/token/")
        self.assertEqual(video, "https://example.test/hls/token/cmedia.m3u8?r=1")
        self.assertEqual(audio, "https://example.test/hls/token/ca1.m3u8?r=1")
        media = b"#EXTM3U\n#EXTINF:1.5,\ns0?r=1\n#EXTINF:1,\ns1?r=1\n"
        parsed = segments(media, video)
        self.assertEqual(parsed[1], (1.5, 1.0, "https://example.test/hls/token/s1?r=1"))


if __name__ == "__main__":
    unittest.main()
