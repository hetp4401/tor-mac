"""tor-mac: a transcode service on Apple Silicon (GitHub's macOS runners), behind one Cloudflare tunnel address that
every running Mac is a connector of (a Mac that goes is skipped; the others answer).

POST /convert?t=&l=&s=&a=&h=&w=&p=&k=  (header X-Mac-Key)  body: a Matroska file's header and one piece's clusters
  -> that piece, 1080p H.264 + AAC stereo in MPEG-TS, timestamped as the tor app's pieces are (TS_BASE + its time in
     the film): t = its start and l = its length (seconds, in the source's timestamps from s, its first), a = the audio
     stream to keep (-1: none), h = HDR (tone-mapped to SDR), w = the source's width (wider than 1920: scaled down),
     p = 0 when a player waits on it (it may take a place past MAX_JOBS).
     k = 1: a keyframe every second of it (a run of one-second pieces made at once, split by the caller at those).
     Made on the Mac's media engine (VideoToolbox: decode, scale and HDR -> SDR, encode); elsewhere (tests) on the CPU.
     A caller that goes meanwhile (its player went elsewhere, another Mac answered first) has its conversion ended.
GET /healthz -> {ok, jobs, max, engine, host, draining}

JSON POST /probe and /convert accept a scoped seekable source; /audio produces a
selected audio rendition as AAC MPEG-TS, or FLAC fragmented MP4 when original=true.
All three require X-Mac-Key. Media credentials stay inside the source relay.

MAX_JOBS conversions at once; past that, 503 (the caller tries another Mac, or its own). On SIGTERM (the job hands over
to the next Mac for its slot) it takes no more (503 "draining", said in /healthz too), finishes those under way, and
stops."""
import hmac, json, math, os, platform, select, signal, socket, subprocess, threading, time, urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from media_index import keyframes, NoIndex, SourceBroken
from source_media import Source, SourceError, Relay
from prepared_media import PreparedSource, NotPrepared

PORT = int(os.environ.get("PORT", "8095"))
KEY = os.environ.get("MAC_KEY", "")
MAX_JOBS = int(os.environ.get("MAX_JOBS", "3"))
MAX_BODY = 200 * 2**20
MAX_JSON = 16 * 1024
REVISION = os.environ.get("SOURCE_REVISION", "source-v1")
PROBE_TTL = 600
probe_cache = OrderedDict()
probe_lock = threading.Lock()
DRAIN_MAX = 120  # seconds the conversions under way are given to finish on SIGTERM
TS_BASE = 10  # (as tor.py's)
jobs = {"n": 0, "done": 0, "failed": 0, "ended": 0, "stuck": 0, "draining": False}
lock = threading.Lock()
recent = []


def engine():
    """videotoolbox when this ffmpeg can use the Mac's media engine, else cpu."""
    if platform.system() != "Darwin":
        return "cpu"
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "videotoolbox" if "h264_videotoolbox" in out else "cpu"


ENGINE = engine()


def command(t, l, s, a, hdr, w, k=0, input_url=None):
    end = t + l
    keys = f"expr:gte(t,n_forced*{k})" if k else "0"  # (the first frame's, and every k seconds of the piece)
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
    # Input seeking is relative to the container start; -copyts then preserves
    # original packet timestamps for the same trim/output offset as binary jobs.
    # Input -t bounds demuxing too: a trim filter alone would read the whole film.
    source_input = (["-ss", f"{max(0, t - s):.6f}", "-t", f"{l:.6f}", "-rw_timeout", "15000000", "-seekable", "1",
                     "-protocol_whitelist", "http,tcp"] if input_url else [])
    return (["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"] + inp + source_input + ["-copyts", "-i", input_url or "pipe:0", "-map", "0:v:0"] +
            (["-map", f"0:{a}", "-af", f"atrim=start={t:.3f}:end={end:.3f}", "-c:a", "aac", "-ac", "2", "-b:a", "192k"] if a >= 0 else []) +
            ["-vf", ",".join(vf)] + venc + ["-force_key_frames", keys, "-output_ts_offset", f"{TS_BASE - s:.3f}",
             "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", "pipe:1"])


def source_options(value):
    """Validate conversion options before constructing an ffmpeg command."""
    try:
        t, length = float(value["start"]), float(value["duration"])
        s = float(value.get("source_start", 0))
        audio, hdr, width = int(value.get("audio", -1)), int(value.get("hdr", 0)), int(value.get("width", 0))
        spacing = float(value.get("segment_seconds", 0))
    except (KeyError, ValueError, TypeError, OverflowError):
        raise ValueError("invalid conversion settings") from None
    if (not all(math.isfinite(v) for v in (t, length, s, spacing))
            or abs(t) > 1e9 or abs(s) > 1e9 or not 0 < length <= 120
            or not -1 <= audio <= 128 or hdr not in (0, 1) or not 0 <= width <= 32768
            or not 0 <= spacing <= 120):
        raise ValueError("conversion settings out of range")
    return t, length, s, audio, hdr, width, spacing


def audio_command(t, length, source_start, audio, original, input_url):
    """Decode a little before the cut (notably TrueHD), then trim by source time.

FLAC keeps the source channel count. Its fragmented MP4 timeline is placed by
Tor's existing orig_place helper after splitting the init and media boxes.
"""
    seek = max(0, t - source_start - 2)
    preroll = max(0, t - source_start - seek)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{seek:.6f}",
           "-t", f"{length + preroll:.6f}", "-rw_timeout", "15000000", "-seekable", "1", "-protocol_whitelist", "http,tcp",
           "-copyts", "-i", input_url, "-map", f"0:{audio}", "-vn", "-sn", "-dn",
           "-af", f"atrim=start={t:.6f}:end={t + length:.6f},asetpts=STARTPTS+N/SR/TB",
           "-map_metadata", "-1", "-map_chapters", "-1"]
    if original:
        cmd += ["-c:a", "flac", "-fflags", "+bitexact", "-use_editlist", "0",
                "-movflags", "+frag_keyframe+empty_moov+default_base_moof+frag_discont", "-frag_duration", "100000000", "-f", "mp4"]
    else:
        cmd += ["-c:a", "aac", "-ac", "2", "-b:a", "192k", "-output_ts_offset", f"{TS_BASE - source_start:.6f}",
                "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts"]
    return cmd + ["pipe:1"]


def cached_probe(source):
    with probe_lock:
        item = probe_cache.get(source.cache_key)
        if item and time.monotonic() - item[0] < PROBE_TTL:
            probe_cache.move_to_end(source.cache_key)
            return item[1]
        probe_cache.pop(source.cache_key, None)


def probe(source, conn):
    source.authorize()
    hit = cached_probe(source)
    if hit is not None:
        return hit
    index = {}

    def read_index():
        try:
            index["keyframes"] = keyframes(source)
        except NoIndex:
            index["keyframes"] = None
        except SourceBroken:
            index["keyframes"] = None
            index["index_error"] = "source index exceeds the available file"
        except (SourceError, IndexError, ValueError, OSError):
            index["keyframes"] = None
            index["index_error"] = "source keyframe index unavailable"

    with Relay(source) as url:
        index_thread = threading.Thread(target=read_index, daemon=True)
        index_thread.start()
        cmd = ["ffprobe", "-v", "error", "-rw_timeout", "15000000", "-protocol_whitelist", "http,tcp",
               "-seekable", "1", "-show_format", "-show_streams", "-of", "json", url]
        result = run(cmd, None, conn, timeout=90)
        if result is None:
            return None
        code, data, _, late = result
        if late or code or not data or len(data) > 2**20:
            raise SourceError("source probe timed out" if late else "source probe failed")
        try:
            info = json.loads(data)
        except ValueError:
            raise SourceError("invalid source probe") from None
        if not isinstance(info.get("streams"), list) or not isinstance(info.get("format"), dict):
            raise SourceError("invalid source probe")
        # FFprobe's filename is the short-lived loopback URL, never useful to a caller.
        info["format"].pop("filename", None)
        index_thread.join(30)
        info.update(index if not index_thread.is_alive() else {"keyframes": None, "index_error": "source keyframe index timed out"})
    with probe_lock:
        probe_cache[source.cache_key] = (time.monotonic(), info)
        probe_cache.move_to_end(source.cache_key)
        while len(probe_cache) > 64:
            probe_cache.popitem(last=False)
    return info


def convert_source(source, descriptor, options, conn):
    """Indexed Matroska uses the same compact input as Tor, prepared on this Mac.

Other formats and non-indexed intervals use ffmpeg's native seekable input. The
metadata belongs to a separate probe job because its relay closes after probing.
"""
    source.authorize()
    metadata = cached_probe(source)
    if metadata is None:
        probe_source = Source(descriptor)
        metadata = probe(probe_source, conn)
        source.bytes += probe_source.bytes
        source.requests += probe_source.requests
        if metadata is None:
            return None, "cancelled"
    try:
        prepared = PreparedSource(source, metadata)
        body, audio = prepared.body(*options[:4])
    except SourceError:
        raise
    except (NotPrepared, IndexError, ValueError, KeyError, RuntimeError, StopIteration):
        body = None
    if body:
        t, length, s, _, hdr, width, spacing = options
        return run(command(t, length, s, audio, hdr, width, spacing), body, conn), "indexed"
    with Relay(source) as url:
        return run(command(*options, input_url=url), None, conn), "native"


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
            try:
                got["out"], got["err"] = p.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # (one that won't die: left behind, see below)
                got["err"] = b"ffmpeg would not die"
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
            talker.join(10)
            if talker.is_alive():
                stuck()
            return None
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:  # (killed, and still there: a hung encoder; its place isn't kept for it)
        stuck()
    return p.returncode, got.get("out") or b"", got.get("err") or b"", bool(got.get("late"))


def stuck():
    """An ffmpeg that was killed and won't go: counted (see /healthz), its job's place freed all the same."""
    with lock:
        jobs["stuck"] += 1
    print(f"an ffmpeg won't die: {jobs['stuck']} left behind", flush=True)


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
                                   "draining": jobs["draining"], "done": jobs["done"], "failed": jobs["failed"], "ended": jobs["ended"], "stuck": jobs["stuck"],
                                   "recent": recent[-8:], "capabilities": ["source-v1", "source-audio-v1", "prepared-v1"], "revision": REVISION})
        self.send(404, {"error": "not here"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path not in ("/convert", "/probe", "/audio"):
            self.close_connection = True
            return self.send(404, {"error": "not here"})
        if not KEY or not hmac.compare_digest(self.headers.get("X-Mac-Key") or "", KEY):
            self.close_connection = True
            return self.send(403, {"error": "key"})
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json":
            return self.source_job(u.path)
        if u.path in ("/probe", "/audio"):
            self.close_connection = True
            return self.send(415, {"error": "this media job requires application/json"})
        q = urllib.parse.parse_qs(u.query)
        try:
            t, l, s = float(q["t"][0]), float(q["l"][0]), float((q.get("s") or ["0"])[0])
            a, hdr, w = int((q.get("a") or ["-1"])[0]), int((q.get("h") or ["0"])[0]), int((q.get("w") or ["0"])[0])
            waits = (q.get("p") or ["1"])[0] == "0"
            k = int((q.get("k") or ["0"])[0])
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
            out = run(command(t, l, s, a, hdr, w, k), body, self.connection)
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

    def source_job(self, path):
        """A source stays on Runners; this Mac probes, seeks and converts it."""
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= MAX_JSON or self.headers.get("Transfer-Encoding"):
                raise ValueError("a JSON body of up to 16 KB is required")
            raw = self.rfile.read(size)
            if len(raw) != size:
                self.close_connection = True
                return
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("a JSON object is required")
            source = Source(value.get("source"))
            options = source_options(value) if path in ("/convert", "/audio") else None
            original = value.get("original", False)
            if path == "/audio" and (not isinstance(original, bool) or options[3] < 0):
                raise ValueError("audio requires a selected track and boolean original")
            waits = value.get("priority", 1) == 0
        except (ValueError, TypeError, OSError) as err:
            self.close_connection = True
            # JSON/Unicode parsers can quote input. Validation messages are fixed;
            # the response never includes raw JSON or a bearer token.
            message = str(err) if isinstance(err, SourceError) else "invalid media job"
            return self.send(400, {"error": message})
        with lock:
            busy = "draining" if jobs["draining"] else "busy" if jobs["n"] >= MAX_JOBS + (1 if waits else 0) else None
            if not busy:
                jobs["n"] += 1
        if busy:
            return self.send(503, {"error": busy})
        t0 = time.monotonic()
        finished = threading.Event()

        def cancel_if_gone():
            while not finished.wait(0.2):
                if gone(self.connection):
                    source.cancel()
                    return

        threading.Thread(target=cancel_if_gone, daemon=True).start()
        try:
            if path == "/probe":
                info = probe(source, self.connection)
                if info is None:
                    jobs["ended"] += 1
                    self.close_connection = True
                    return
                self.send(200, info)
                return
            if path == "/audio":
                with Relay(source) as url:
                    out = run(audio_command(*options[:4], original, url), None, self.connection)
                preparation = "native"
            else:
                out, preparation = convert_source(source, value["source"], options, self.connection)
            if out is None:
                jobs["ended"] += 1
                self.close_connection = True
                return
            code, data, _, late = out
            if late:
                raise SourceError("conversion exceeded 120 seconds")
            if code or not data:
                raise SourceError("source conversion failed")
            jobs["done"] += 1
            recent.append({"at": int(time.time()), "mode": "source-audio-v1" if path == "/audio" else "source-v1", "in_mb": round(source.bytes / 1e6, 2),
                           "source_requests": source.requests, "upload_s": 0, "convert_s": round(time.monotonic() - t0, 2),
                           "out_mb": round(len(data) / 1e6, 2), "waits": waits, "preparation": preparation,
                           "source_cache_hits": source.cache_hits, "source_cached_mb": round(source.cached_bytes / 1e6, 2)})
            del recent[:-50]
            self.send(200, data, "audio/mp4" if path == "/audio" and original else "video/mp2t")
        except SourceError as err:
            if gone(self.connection):
                jobs["ended"] += 1
                self.close_connection = True
                return
            jobs["failed"] += 1
            self.send(502, {"error": str(err)})
        except (OSError, ValueError, KeyError):
            jobs["failed"] += 1
            self.close_connection = True
            # Sources and subprocess diagnostics are intentionally not echoed.
            with __import__("contextlib").suppress(OSError):
                self.send(500, {"error": "media processing failed"})
        finally:
            finished.set()
            source.cancel()
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
