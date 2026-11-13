"""Temporary isolated Tor benchmark: fixture cache, authenticated ranges, paired Mac jobs.

Uses only synthetic media. No Tor user data, source tokens or playback sessions.
The before path is the unchanged live v91 MKV preparation functions.
"""
import argparse
import collections
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import benchmark_tor_helpers as old

ROOT = Path(os.environ.get("BENCH_FIXTURES", "/tmp/tor-benchmark-fixtures"))
SOURCE_KEY = os.environ.get("BENCH_SOURCE_KEY", "")
ADMIN_KEY = os.environ.get("BENCH_ADMIN_KEY", "")
MAC_KEY = os.environ.get("MAC_KEY", "")
PUBLIC_URL = os.environ.get("BENCH_PUBLIC_URL", "https://tor-mac-bench.billybishop4-workers.xyz")
FIXTURES = {"h264": "h264-sdr-720p.mkv", "hevc_hdr": "hevc-hdr-1080p.mkv", "hevc_hdr_long": "hevc-hdr-1080p-240s.mkv", "hevc_hdr_xlong": "hevc-hdr-1080p-960s.mkv"}
IDS = {name: hashlib.sha256(filename.encode()).hexdigest()[:32] for name, filename in FIXTURES.items()}
FILES = {IDS[name]: ROOT / filename for name, filename in FIXTURES.items()}
METRICS = collections.defaultdict(lambda: {"requests": 0, "bytes": 0, "ranges": []})
LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
STATE = {"ready": False, "error": None, "run": None, "rows": []}
HARNESS_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
PROFILES = ("baseline", "known-mkv", "probe-256k", "probe-64k", "decode-1")


def command(cmd, timeout=600):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace")[-600:])
    return p.stdout


def generate():
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, filename in FIXTURES.items():
        target = ROOT / filename
        if target.exists() and target.stat().st_size:
            continue
        if name in ("hevc_hdr_long", "hevc_hdr_xlong"):
            listing = ROOT / "concat-list.txt"
            listing.write_text("file 'hevc-hdr-1080p.mkv'\n" * (16 if name == "hevc_hdr_xlong" else 4))
            try:
                command(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                         "-c", "copy", str(target)])
            finally:
                listing.unlink(missing_ok=True)
            continue
        hdr = name == "hevc_hdr"
        size = "1920x1080" if hdr else "1280x720"
        codec = (["-c:v", "libx265", "-preset", "ultrafast", "-crf", "29", "-pix_fmt", "yuv420p10le",
                  "-x265-params", "pools=2:frame-threads=2:keyint=240:min-keyint=240:scenecut=0:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
                  "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"] if hdr else
                 ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-threads", "2", "-g", "240", "-keyint_min", "240", "-sc_threshold", "0"])
        command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "60"] + codec +
                ["-c:a", "aac", "-ac", "2", "-b:a", "128k", str(target)], timeout=900)


def source_read(key, pos, size, *_):
    with FILES[key].open("rb") as f:
        f.seek(pos)
        return f.read(size)


def prepare_helpers():
    old.PIECE_S = 1.0
    old.SRC_BLOCK = 8 * 2 ** 20
    old.AUDIO_PRE = .3
    old.MUXED = True
    old.src_meta = {}
    old.probed = {}
    old.src_read = source_read
    old.src_head = lambda key, end, piece: source_read(key, 0, end)
    old.src_lookup = lambda key: {"size": FILES[key].stat().st_size}
    old.land_now = lambda *args: None
    old.wanted_block = lambda *args: False
    old.threading = threading


def local_probe(key):
    t0, cpu0 = time.perf_counter(), time.process_time()
    result = json.loads(command(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(FILES[key])]))
    fmt, streams = result["format"], result["streams"]
    video = next(x for x in streams if x["codec_type"] == "video")
    audios = [x for x in streams if x["codec_type"] == "audio"]
    duration, start = float(fmt["duration"]), float(fmt.get("start_time", 0))
    old.src_meta[key] = {"size": FILES[key].stat().st_size}
    kf = old.mkv_keyframes(key)
    cuts, pos, aligned, pend = old.piece_cuts(kf, start, duration)
    fps = video.get("r_frame_rate", "24/1").split("/")
    info = {"start": start, "duration": duration, "pos": pos, "aligned": aligned, "pend": pend, "cuts": cuts,
            "a": audios[0]["index"] if audios else -1, "audios": [{"i": x["index"]} for x in audios],
            "hdr": int(video.get("color_transfer") in ("smpte2084", "arib-std-b67")), "w": video["width"],
            "vcodec": video["codec_name"], "fps": int(fps[0]) / max(1, int(fps[-1]))}
    old.probed[key] = info
    return {"wall_s": time.perf_counter() - t0, "python_cpu_s": time.process_time() - cpu0,
            "keyframes": len(kf), "source_bytes": FILES[key].stat().st_size, "source_duration_s": duration}


def post(url, body, content_type):
    req = urllib.request.Request(url, data=body, method="POST", headers={"User-Agent": "Tor-Paired-Benchmark/1.0",
            "X-Mac-Key": MAC_KEY, "Content-Type": content_type})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=150) as response:
            headers_at = time.perf_counter()
            data = response.read()
            finished = time.perf_counter()
            server = {}
            for item in response.headers.get("Server-Timing", "").split(","):
                match = re.fullmatch(r"\s*(upload|process|spawn);dur=([0-9.]+)\s*", item)
                if match:
                    server["server_" + match[1] + "_s"] = float(match[2]) / 1000
            return data, {"http_s": finished - started, "headers_s": headers_at - started,
                          "response_download_s": finished - headers_at,
                          "upload_bytes": len(body), "output_bytes": len(data), "http_status": response.status,
                          "response_profile": response.headers.get("X-Mac-Profile"), **server}
    except urllib.error.HTTPError as e:
        # Error content deliberately excluded: a remote error might echo a signed source URL.
        raise RuntimeError(f"Mac HTTP {e.code}") from None


def source_spec(key):
    # Isolate each benchmark's cold metadata/input cache from previous trial runs.
    return {"id": key + ":" + str(STATE.get("started_at", 0)), "url": PUBLIC_URL.rstrip("/") + "/mac-source/" + key,
            "headers": {"Authorization": "Bearer " + SOURCE_KEY}}


def counters(key):
    with LOCK:
        m = METRICS[key]
        return {"requests": m["requests"], "bytes": m["bytes"]}


def validate(data, pixels=False):
    p = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries", "stream=codec_name,width,height,start_time,duration,nb_read_frames:format=duration",
                        "-of", "json", "pipe:0"], input=data, capture_output=True, timeout=20)
    if p.returncode or not data.startswith(b"G"):
        raise RuntimeError("Output is not readable MPEG-TS")
    info = json.loads(p.stdout)
    video = next((s for s in info.get("streams", []) if s.get("codec_name") == "h264"), None)
    if not video:
        raise RuntimeError("Output has no H264 video")
    result = {"video": video, "duration_s": info.get("format", {}).get("duration")}
    if pixels:
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-map", "0:v:0", "-f", "framemd5", "pipe:1"],
                           input=data, capture_output=True, timeout=20)
        if p.returncode:
            raise RuntimeError("Output frames did not decode")
        hashes = [line.rsplit(b",", 1)[-1].strip() for line in p.stdout.splitlines() if line and not line.startswith(b"#")]
        result["decoded_frames_sha256"] = hashlib.sha256(b"\n".join(hashes)).hexdigest()
        result["decoded_frames"] = len(hashes)
    return result


def one(mac_url, fixture, mode, index, label, profile=None):
    key, info = IDS[fixture], old.probed[IDS[fixture]]
    requested_start, length, _, _ = old.piece_cut(key, index)
    old_t0, cpu0 = time.perf_counter(), time.process_time()
    before = counters(key)
    if mode == "binary":
        body, audio = old.mac_body(key, index)
        prep_s, prep_cpu = time.perf_counter() - old_t0, time.process_time() - cpu0
        query = {"t": info["start"] + requested_start, "l": length, "s": info["start"],
                 "a": audio, "h": info["hdr"], "w": info["w"], "p": 0}
        if profile is not None:
            query["profile"] = profile
        q = urllib.parse.urlencode(query)
        data, timings = post(mac_url.rstrip("/") + "/convert?" + q, body, "application/octet-stream")
        if profile not in (None, "baseline") and timings.get("response_profile") != profile:
            raise RuntimeError("Mac did not acknowledge requested benchmark profile")
    else:
        body = json.dumps({"source": source_spec(key), "start": info["start"] + requested_start, "duration": length,
                           "source_start": info["start"], "audio": old.muxed(info), "hdr": info["hdr"], "width": info["w"],
                           "segment_seconds": 0, "priority": 0}).encode()
        prep_s, prep_cpu = time.perf_counter() - old_t0, time.process_time() - cpu0
        data, timings = post(mac_url.rstrip("/") + "/convert", body, "application/json")
    total = time.perf_counter() - old_t0
    after = counters(key)
    mac_observed = {}
    try:
        req = urllib.request.Request(mac_url.rstrip("/") + "/healthz", headers={"User-Agent": "Tor-Paired-Benchmark/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            recent = json.load(response).get("recent", [])
        if recent:
            last = recent[-1]
            # Mac4 is isolated and this harness sends only one conversion at a time.
            if bool(last.get("mode", "").startswith("source")) == (mode == "source"):
                mac_observed = {"mac_received_mb": last.get("in_mb"), "mac_convert_s": last.get("convert_s"),
                                "mac_upload_s": last.get("upload_s"), "mac_spawn_s": last.get("spawn_s"),
                                "mac_response_s": last.get("response_s"), "mac_profile": last.get("profile"),
                                "mac_source_requests": last.get("source_requests", 0),
                                "mac_preparation": last.get("preparation"),
                                "mac_cache_hits": last.get("source_cache_hits", 0),
                                "mac_cached_mb": last.get("source_cached_mb", 0)}
    except (OSError, ValueError):
        pass
    return {"fixture": fixture, "mode": mode, "profile": profile, "case": label, "segment": index, "start_s": requested_start,
            "length_s": length, "total_s": round(total, 4), "runners_prep_s": round(prep_s, 4),
            "runners_python_cpu_s": round(prep_cpu, 4), "source_requests": after["requests"] - before["requests"],
            "source_bytes": after["bytes"] - before["bytes"], **{k: round(v, 4) if isinstance(v, float) else v for k, v in timings.items()},
            "payload_sha256": hashlib.sha256(body).hexdigest(), **mac_observed, "validation": validate(data, pixels=profile is not None)}


def benchmark(opts):
    if not RUN_LOCK.acquire(False):
        return False
    old.MUXED = bool(opts.get("muxed_audio", True))
    STATE.update(run="running", rows=[], error=None, started_at=time.time(), probes=[],
                 config={"source_block_bytes": old.SRC_BLOCK, "muxed_audio": old.MUXED,
                         "source_cache": "generated fixtures already stored on Runners",
                         "fleet_machine": os.environ.get("FLEET_MACHINE"), "profiles": opts.get("profiles"),
                         "unique_inputs": bool(opts.get("unique_inputs")), "harness_revision": HARNESS_REVISION})

    def work():
        try:
            mac_url = opts.get("mac_url", "https://mac-4.billybishop4-workers.xyz")
            if not re.fullmatch(r"https://mac-[1-4]\.billybishop4-workers\.xyz", mac_url):
                raise ValueError("unsupported benchmark worker")
            req = urllib.request.Request(mac_url + "/healthz", headers={"User-Agent": "Tor-Paired-Benchmark/1.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                health = json.load(response)
            STATE["worker_revision"] = health.get("revision")
            if opts.get("expected_revision") and health.get("revision") != opts["expected_revision"]:
                raise ValueError("benchmark worker revision changed")
            profiles = opts.get("profiles")
            if profiles is not None and (not isinstance(profiles, list) or not profiles or profiles[0] != "baseline"
                                         or len(set(profiles)) != len(profiles) or any(p not in PROFILES for p in profiles)):
                raise ValueError("invalid profiles")
            if profiles and any(p != "baseline" and p not in health.get("benchmark_profiles", []) for p in profiles):
                raise ValueError("benchmark profile is not enabled on worker")
            modes = ["binary"] if profiles else opts.get("modes", ["binary", "source"])
            if not modes or any(m not in ("binary", "source") for m in modes):
                raise ValueError("invalid modes")
            fixtures = opts.get("fixtures", list(FIXTURES))
            if not fixtures or any(f not in FIXTURES for f in fixtures):
                raise ValueError("invalid fixtures")
            repeats = min(3, max(0, int(opts.get("repeats", 2))))
            trials = min(5, max(1, int(opts.get("trials", 1))))
            for fixture in fixtures:
                key = IDS[fixture]
                probe = local_probe(key)
                STATE["probes"].append({"fixture": fixture, "mode": "binary", **probe})
                if "source" in modes:
                    before = counters(key)
                    _, timing = post(mac_url + "/probe", json.dumps({"source": source_spec(key)}).encode(), "application/json")
                    after = counters(key)
                    STATE["probes"].append({"fixture": fixture, "mode": "source", **timing,
                                            "source_bytes": after["bytes"] - before["bytes"],
                                            "source_requests": after["requests"] - before["requests"]})
                cases = ([("startup", n * 10 if opts.get("unique_inputs") else 0) for n in range(trials)]
                         + [("seek", 7 + n * 10 if opts.get("unique_inputs") else 37) for n in range(trials)]
                         + [("repeat", 37)] * repeats)
                for i, (label, segment) in enumerate(cases):
                    choices = [("binary", p) for p in profiles] if profiles else [(mode, None) for mode in modes]
                    # Rotate profiles (or alternate two modes), balancing which runs first.
                    offset = i % len(choices)
                    choices = choices[offset:] + choices[:offset]
                    for mode, profile in choices:
                        row = one(mac_url, fixture, mode, segment, label, profile)
                        row["pair_id"] = f"{fixture}:{i}:{segment}"
                        STATE["rows"].append(row)
                        print(json.dumps(row), flush=True)
            summaries = []
            for fixture in fixtures:
                for mode, profile in ([("binary", p) for p in profiles] if profiles else [(m, None) for m in modes]):
                    for case in ("startup", "seek", "repeat"):
                        rows = [r for r in STATE["rows"] if r["fixture"] == fixture and r["mode"] == mode and r["case"] == case
                                and r["profile"] == profile]
                        if rows:
                            summaries.append({"fixture": fixture, "mode": mode, "profile": profile, "case": case, "n": len(rows),
                                              "median_s": round(statistics.median(r["total_s"] for r in rows), 4),
                                              "min_s": min(r["total_s"] for r in rows), "max_s": max(r["total_s"] for r in rows)})
            STATE["summary"] = summaries
            if profiles:
                comparisons = []
                for pair in sorted(set(r["pair_id"] for r in STATE["rows"])):
                    rows = [r for r in STATE["rows"] if r["pair_id"] == pair]
                    baseline = next(r for r in rows if r["profile"] == "baseline")
                    for row in rows:
                        if row["profile"] == "baseline":
                            continue
                        comparisons.append({"pair_id": pair, "profile": row["profile"],
                                            "same_payload": row["payload_sha256"] == baseline["payload_sha256"],
                                            "same_decoded_output": row["validation"] == baseline["validation"],
                                            "latency_change_s": round(row["total_s"] - baseline["total_s"], 4)})
                STATE["quality_comparisons"] = comparisons
            for probe in STATE["probes"]:
                first = next(r for r in STATE["rows"] if r["fixture"] == probe["fixture"] and r["mode"] == probe["mode"] and r["case"] == "startup")
                probe["probe_plus_first_segment_s"] = round(probe.get("wall_s", probe.get("http_s", 0)) + first["total_s"], 4)
            STATE["run"] = "complete"
        except Exception as e:
            STATE.update(run="failed", error=f"{type(e).__name__}: {e}")
        finally:
            STATE["finished_at"] = time.time()
            RUN_LOCK.release()
    threading.Thread(target=work, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *_):
        pass

    def authenticated(self, key):
        return bool(key) and hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + key)

    def send_json(self, status, data):
        data = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz":
            return self.send_json(200, {"ok": True, "ready": STATE["ready"], "error": STATE["error"], "run": STATE["run"],
                                       "harness_revision": HARNESS_REVISION})
        if path == "/results":
            if not self.authenticated(ADMIN_KEY):
                return self.send_json(403, {"error": "auth"})
            return self.send_json(200, STATE)
        if path.startswith("/fixture/"):
            key = path.removeprefix("/fixture/")
            file = FILES.get(key)
            if not STATE["ready"] or file is None or not file.is_file():
                return self.send_json(404, {"error": "not found"})
            self.send_response(302)
            self.send_header("Location", "/fixture-data/" + key)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path.startswith("/mac-source/"):
            if not self.authenticated(SOURCE_KEY):
                return self.send_json(404, {"error": "not found"})
            key = path.removeprefix("/mac-source/")
        elif path.startswith("/fixture-data/"):
            # Public only for the explicit, generated test fixtures in FILES.
            # No path is resolved from untrusted request text.
            key = path.removeprefix("/fixture-data/")
        else:
            return self.send_json(404, {"error": "not found"})
        file = FILES.get(key)
        if not STATE["ready"] or file is None or not file.is_file():
            return self.send_json(404, {"error": "not found"})
        size = file.stat().st_size
        start, end, status = 0, size - 1, 200
        raw_range = self.headers.get("Range")
        if raw_range:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", raw_range)
            if not match or not any(match.groups()):
                return self.send_json(416, {"error": "range"})
            left, right = match.groups()
            if left:
                start, end = int(left), min(size - 1, int(right) if right else size - 1)
            else:
                start, end = max(0, size - int(right)), size - 1
            if start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "video/x-matroska")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "private, no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        sent = 0
        try:
            with file.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = f.read(min(65536, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    sent += len(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with LOCK:
                item = METRICS[key]
                item["requests"] += 1
                item["bytes"] += sent
                item["ranges"].append([start, end, sent])
                del item["ranges"][:-100]

    def do_POST(self):
        if self.path not in ("/run", "/cleanup") or not self.authenticated(ADMIN_KEY):
            return self.send_json(403, {"error": "auth"})
        if self.path == "/cleanup":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 1024 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("body")
                if len(self.rfile.read(length)) != length:
                    raise ValueError("body")
            except (ValueError, OSError):
                self.close_connection = True
                return self.send_json(400, {"error": "invalid cleanup request"})
            if not RUN_LOCK.acquire(False):
                return self.send_json(409, {"error": "benchmark running"})
            try:
                if not STATE["ready"] and STATE.get("run") not in ("cleaned", "cleanup_failed"):
                    return self.send_json(409, {"error": "fixtures still preparing"})
                STATE["ready"] = False
                removed, total = 0, 0
                # Delete only the explicit synthetic fixture paths. Never
                # traverse the volume or accept a filename from the request.
                for file in FILES.values():
                    try:
                        size = file.lstat().st_size
                    except FileNotFoundError:
                        continue
                    file.unlink()
                    removed += 1
                    total += size
                STATE["run"] = "cleaned"
                return self.send_json(200, {"removed_files": removed, "removed_bytes": total,
                                           "remaining_files": sum(file.exists() for file in FILES.values())})
            except OSError:
                STATE["run"] = "cleanup_failed"
                return self.send_json(500, {"error": "could not remove synthetic fixtures"})
            finally:
                RUN_LOCK.release()
        if not STATE["ready"]:
            return self.send_json(503, {"error": "fixtures preparing"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("body")
            opts = json.loads(self.rfile.read(length))
            accepted = benchmark(opts)
            return self.send_json(202 if accepted else 409, {"accepted": accepted})
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "invalid request"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    if args.generate_only:
        generate()
        return
    prepare_helpers()
    def init():
        try:
            generate()
            STATE["ready"] = True
        except Exception as e:
            STATE["error"] = f"fixture generation: {type(e).__name__}"
    threading.Thread(target=init, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
