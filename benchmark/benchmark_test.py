import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
if __package__:
    from . import benchmark as b
else:
    import benchmark as b


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "sample.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-t", "3",
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", "24", "-sc_threshold", "0", str(cls.path)], check=True)
        cls.key = b.IDS["h264"]
        b.FILES[cls.key] = cls.path
        b.SOURCE_KEY = "test-source"
        b.ADMIN_KEY = "test-admin"
        b.STATE["ready"] = True
        b.prepare_helpers()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), b.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/mac-source/{cls.key}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, byte_range=None, method="GET", auth="test-source"):
        headers = {"Authorization": "Bearer " + auth}
        if byte_range:
            headers["Range"] = byte_range
        return urllib.request.urlopen(urllib.request.Request(self.base, headers=headers, method=method), timeout=3)

    def test_range_roundtrip(self):
        with self.request("bytes=17-101") as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.read(), self.path.read_bytes()[17:102])
            self.assertEqual(r.headers["Content-Range"], f"bytes 17-101/{self.path.stat().st_size}")
        with self.request("bytes=-37") as r:
            self.assertEqual(r.read(), self.path.read_bytes()[-37:])

    def test_head_and_auth(self):
        with self.request(method="HEAD") as r:
            self.assertEqual(int(r.headers["Content-Length"]), self.path.stat().st_size)
            self.assertEqual(r.read(), b"")
        with self.assertRaises(urllib.error.HTTPError) as got:
            self.request(auth="wrong")
        self.assertEqual(got.exception.code, 404)
        got.exception.close()

    def test_invalid_range(self):
        with self.assertRaises(urllib.error.HTTPError) as got:
            self.request("bytes=9999999999-")
        self.assertEqual(got.exception.code, 416)
        got.exception.close()

    def test_original_preparation_is_readable(self):
        meta = b.local_probe(self.key)
        self.assertGreaterEqual(meta["keyframes"], 2)
        data, audio = b.old.mac_body(self.key, 1)
        self.assertEqual(audio, -1)
        p = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", "pipe:0"],
                           input=data, capture_output=True, check=True)
        self.assertEqual(json.loads(p.stdout)["streams"][0]["codec_name"], "h264")

    def test_public_fixture_redirect_and_range(self):
        address = self.base.replace("/mac-source/", "/fixture/")
        req = urllib.request.Request(address, headers={"Range": "bytes=17-101"})
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual(r.status, 206)
            self.assertIn("/fixture-data/", r.url)
            self.assertEqual(r.read(), self.path.read_bytes()[17:102])
        req = urllib.request.Request(address.replace("/fixture/", "/fixture-data/"), method="HEAD")
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual(int(r.headers["Content-Length"]), self.path.stat().st_size)
            self.assertEqual(r.read(), b"")
        with self.assertRaises(urllib.error.HTTPError) as got:
            urllib.request.urlopen(address + "/../../etc/passwd", timeout=3)
        self.assertEqual(got.exception.code, 404)
        got.exception.close()

    def test_cleanup_is_authenticated_scoped_and_idempotent(self):
        address = self.base.rsplit("/mac-source/", 1)[0] + "/cleanup"
        def request(auth):
            req = urllib.request.Request(address, data=b"{}", headers={"Authorization": "Bearer " + auth})
            return urllib.request.urlopen(req, timeout=3)
        with self.assertRaises(urllib.error.HTTPError) as got:
            request("wrong")
        self.assertEqual(got.exception.code, 403)
        got.exception.close()
        original = b.FILES
        fixture = Path(self.tmp.name) / "cleanup-generated.mkv"
        unrelated = Path(self.tmp.name) / "keep.txt"
        fixture.write_bytes(b"synthetic")
        unrelated.write_bytes(b"keep")
        b.FILES = {"generated": fixture}
        try:
            b.RUN_LOCK.acquire()
            try:
                with self.assertRaises(urllib.error.HTTPError) as got:
                    request("test-admin")
                self.assertEqual(got.exception.code, 409)
                got.exception.close()
            finally:
                b.RUN_LOCK.release()
            with request("test-admin") as r:
                self.assertEqual(json.load(r)["removed_files"], 1)
            self.assertFalse(fixture.exists())
            self.assertTrue(unrelated.exists())
            with request("test-admin") as r:
                self.assertEqual(json.load(r)["removed_files"], 0)
        finally:
            b.FILES = original
            b.STATE["ready"] = True
            b.STATE["run"] = None


if __name__ == "__main__":
    unittest.main()
