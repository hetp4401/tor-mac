"""tor-mac: a transcode service on Apple Silicon (GitHub's macOS runners), behind one Cloudflare tunnel address that
every running Mac is a connector of (a Mac that goes is skipped; the others answer).

POST /convert?t=&l=&s=&a=&h=&w=&p=  (header X-Mac-Key)  body: a Matroska file's header and one piece's clusters
  -> that piece, 1080p H.264 + AAC stereo in MPEG-TS, timestamped as the tor app's pieces are (TS_BASE + its time in
     the film): t = its start and l = its length (seconds, in the source's timestamps from s, its first), a = the audio
     stream to keep (-1: none), h = HDR (tone-mapped to SDR), w = the source's width (wider than 1920: scaled down),
     p = 0 when a player waits on it (it may take a place past MAX_JOBS).
     Made on the Mac's media engine (VideoToolbox: decode, scale and HDR -> SDR, encode); elsewhere (tests) on the CPU.
     A caller that goes meanwhile (its player went elsewhere, another Mac answered first) has its conversion ended.
GET /healthz -> {ok, jobs, max, engine, host, draining}

MAX_JOBS conversions at once; past that, 503 (the caller tries another Mac, or its own). On SIGTERM (the job hands over
to the next Mac for its slot) it takes no more (503 "draining", said in /healthz too), finishes those under way, and
stops."""
import hmac, json, os, platform, select, signal, socket, subprocess, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8095"))
KEY = os.environ.get("MAC_KEY", "")
MAX_JOBS = int(os.environ.get("MAX_JOBS", "3"))
MAX_BODY = 200 * 2**20
DRAIN_MAX = 120  # seconds the conversions under way are given to finish on SIGTERM
TS_BASE = 10  # (as tor.py's)
jobs = {"n": 0, "done": 0, "failed": 0, "ended": 0, "draining": False}
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


def gone(conn):
    """Whether the caller on conn has hung up."""
    try:
        return bool(select.select([conn], [], [], 0)[0]) and not conn.recv(1, socket.MSG_PEEK)
    except OSError:
        return True


def run(cmd, body, conn, timeout=120):
    """cmd run on body: (its exit code, its output, its errors, whether it ran out of time), or None when the caller on
    conn hung up meanwhile (it's ended then)."""
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    got = {}

    def talk():
        try:
            got["out"], got["err"] = p.communicate(body, timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            got["out"], got["err"] = p.communicate()
            got["late"] = True
        except Exception as err:
            p.kill()
            got["err"] = str(err).encode()
    talker = threading.Thread(target=talk, daemon=True)
    talker.start()
    while talker.is_alive():
        talker.join(0.2)
        if talker.is_alive() and gone(conn):
            p.kill()
            talker.join()
            return None
    p.wait()
    return p.returncode, got.get("out") or b"", got.get("err") or b"", bool(got.get("late"))


class Handler(BaseHTTPRequestHandler):
    timeout = 60  # (a body that stops coming (its caller went; the tunnel kept the connection open): the read ends, the place is freed)
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
                                   "draining": jobs["draining"], "done": jobs["done"], "failed": jobs["failed"], "ended": jobs["ended"], "recent": recent[-8:]})
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
            waits = (q.get("p") or ["1"])[0] == "0"
            size = int(self.headers.get("Content-Length") or 0)
        except (KeyError, ValueError):
            return self.send(400, {"error": "t, l (s, a, h, w, p) and a body"})
        if not 0 < size <= MAX_BODY:
            return self.send(400, {"error": "a body of up to 200 MB"})
        with lock:
            busy = "draining" if jobs["draining"] else "busy" if jobs["n"] >= MAX_JOBS + (1 if waits else 0) else None
            if not busy:
                jobs["n"] += 1
        if busy:
            self.close_connection = True  # (its body unread: the connection isn't used again)
            return self.send(503, {"error": busy})
        try:
            t0 = time.time()
            try:
                body = self.rfile.read(size)
            except OSError:
                body = b""
            if len(body) < size:  # (the caller went while sending: nothing to make)
                jobs["ended"] += 1
                self.close_connection = True
                return
            t1 = time.time()
            out = run(command(t, l, s, a, hdr, w), body, self.connection)
            took = time.time() - t1
            if out is None:  # (the caller went: no one to answer)
                jobs["ended"] += 1
                self.close_connection = True
                return
            code, data, err, late = out
            if late:
                jobs["failed"] += 1
                return self.send(500, {"error": "ffmpeg took over 120 s"})
            if code or not data:
                jobs["failed"] += 1
                return self.send(500, {"error": f"ffmpeg (exit {code}): {err.decode(errors='replace')[-300:]}"})
            jobs["done"] += 1
            recent.append({"at": int(time.time()), "in_mb": round(size / 1e6, 1), "upload_s": round(t1 - t0, 2), "convert_s": round(took, 2),
                           "out_mb": round(len(data) / 1e6, 2), "waits": waits})
            del recent[:-50]
            self.send(200, data, "video/mp2t")
        finally:
            with lock:
                jobs["n"] -= 1


def drain(*_):
    """SIGTERM (its job hands over to the next Mac for its slot): no more conversions taken (503 "draining", said in
    /healthz too), those under way finished (DRAIN_MAX s at most), then gone."""
    if jobs["draining"]:
        return
    jobs["draining"] = True
    print(f"draining: {jobs['n']} under way", flush=True)

    def end():
        t = time.time()
        while jobs["n"] and time.time() - t < DRAIN_MAX:
            time.sleep(0.2)
        time.sleep(0.5)  # (the last answers sent)
        os._exit(0)
    threading.Thread(target=end, daemon=True).start()


if __name__ == "__main__":
    print(f"tor-mac on :{PORT}, {ENGINE}, {MAX_JOBS} at once", flush=True)
    signal.signal(signal.SIGTERM, drain)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()
