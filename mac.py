"""tor-mac: a transcode service on Apple Silicon (GitHub's macOS runners), behind one Cloudflare tunnel address that
every running Mac is a connector of (a Mac that goes is skipped; the others answer).

POST /convert?t=&l=&s=&a=&h=&w=  (header X-Mac-Key)  body: a Matroska file's header and one piece's clusters
  -> that piece, 1080p H.264 + AAC stereo in MPEG-TS, timestamped as the tor app's pieces are (TS_BASE + its time in
     the film): t = its start and l = its length (seconds, in the source's timestamps from s, its first), a = the audio
     stream to keep (-1: none), h = HDR (tone-mapped to SDR), w = the source's width (wider than 1920: scaled down).
     Made on the Mac's media engine (VideoToolbox: decode, scale and HDR -> SDR, encode); elsewhere (tests) on the CPU.
GET /healthz -> {ok, jobs, max, engine, host}

MAX_JOBS conversions at once; past that, 503 (the caller tries another Mac, or its own)."""
import hmac, json, os, platform, socket, subprocess, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8095"))
KEY = os.environ.get("MAC_KEY", "")
MAX_JOBS = int(os.environ.get("MAX_JOBS", "3"))
MAX_BODY = 200 * 2**20
TS_BASE = 10  # (as tor.py's)
jobs = {"n": 0, "done": 0, "failed": 0}
lock = threading.Lock()
recent = []


def engine():
    """videotoolbox when this ffmpeg can use the Mac's media engine, else cpu."""
    if platform.system() != "Darwin":
        return "cpu"
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "videotoolbox" if "h264_videotoolbox" in out else "cpu"


ENGINE = engine()


def command(t, l, s, a, hdr, w):
    end = t + l
    if ENGINE == "videotoolbox":
        vf = [f"trim=start={t:.3f}:end={end:.3f}"]
        scale = ["w=1920", "h=trunc(ih*1920/iw/2)*2"] if w > 1920 else []
        color = ["color_matrix=bt709", "color_primaries=bt709", "color_transfer=bt709"] if hdr else []
        if scale or color:
            vf.append("scale_vt=" + ":".join(scale + color))
        inp = ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"]
        venc = ["-c:v", "h264_videotoolbox", "-profile:v", "high", "-level", "4.1", "-b:v", "9M", "-maxrate", "14M",
                "-bufsize", "18M", "-allow_sw", "0", "-prio_speed", "0"]
    else:
        tone = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv")
        vf = [f"trim=start={t:.3f}:end={end:.3f}"] + (["scale=1920:-2"] if w > 1920 else []) + ([tone] if hdr else []) + ["format=yuv420p"]
        inp = []
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-profile:v", "high", "-level", "4.1"]
    return (["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"] + inp + ["-copyts", "-i", "pipe:0", "-map", "0:v:0"] +
            (["-map", f"0:{a}", "-af", f"atrim=start={t:.3f}:end={end:.3f}", "-c:a", "aac", "-ac", "2", "-b:a", "192k"] if a >= 0 else []) +
            ["-vf", ",".join(vf)] + venc + ["-force_key_frames", "0", "-output_ts_offset", f"{TS_BASE - s:.3f}",
             "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", "pipe:1"])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if code == 503:
            self.send_header("Retry-After", "1")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/healthz":
            return self.send(200, {"ok": True, "slot": os.environ.get("SLOT"), "jobs": jobs["n"], "max": MAX_JOBS, "engine": ENGINE, "host": socket.gethostname(),
                                   "done": jobs["done"], "failed": jobs["failed"], "recent": recent[-8:]})
        self.send(404, {"error": "not here"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/convert":
            return self.send(404, {"error": "not here"})
        if not KEY or not hmac.compare_digest(self.headers.get("X-Mac-Key") or "", KEY):
            return self.send(403, {"error": "key"})
        q = urllib.parse.parse_qs(u.query)
        try:
            t, l, s = float(q["t"][0]), float(q["l"][0]), float((q.get("s") or ["0"])[0])
            a, hdr, w = int((q.get("a") or ["-1"])[0]), int((q.get("h") or ["0"])[0]), int((q.get("w") or ["0"])[0])
            size = int(self.headers.get("Content-Length") or 0)
        except (KeyError, ValueError):
            return self.send(400, {"error": "t, l (s, a, h, w) and a body"})
        if not 0 < size <= MAX_BODY:
            return self.send(400, {"error": "a body of up to 200 MB"})
        with lock:
            if jobs["n"] >= MAX_JOBS:
                busy = True
            else:
                busy = False
                jobs["n"] += 1
        if busy:
            self.rfile.read(size)  # (the connection stays usable)
            return self.send(503, {"error": "busy"})
        try:
            t0 = time.time()
            body = self.rfile.read(size)
            t1 = time.time()
            out = subprocess.run(command(t, l, s, a, hdr, w), input=body, capture_output=True, timeout=120)
            took = time.time() - t1
            if out.returncode or not out.stdout:
                jobs["failed"] += 1
                return self.send(500, {"error": f"ffmpeg (exit {out.returncode}): {out.stderr.decode(errors='replace')[-300:]}"})
            jobs["done"] += 1
            recent.append({"at": int(time.time()), "in_mb": round(size / 1e6, 1), "upload_s": round(t1 - t0, 2), "convert_s": round(took, 2),
                           "out_mb": round(len(out.stdout) / 1e6, 2)})
            del recent[:-50]
            self.send(200, out.stdout, "video/mp2t")
        except subprocess.TimeoutExpired:
            jobs["failed"] += 1
            self.send(500, {"error": "ffmpeg took over 120 s"})
        finally:
            with lock:
                jobs["n"] -= 1


if __name__ == "__main__":
    print(f"tor-mac on :{PORT}, {ENGINE}, {MAX_JOBS} at once", flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()
