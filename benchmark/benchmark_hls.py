"""Public-player HLS latency measurement using only synthetic benchmark media.

No internal X-Tor-Key header is used. Tokens remain in memory and never enter
the result or error messages. A fresh file ID prevents metadata/output reuse;
the operator restarts the isolated Tor process to reset its Mac connection pool.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import threading
import time
from urllib.parse import urljoin, urlsplit
import urllib.request
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FIXTURES = {"h264": "1a586f2e091afe1e15a83aa50739fc40", "hevc_hdr": "7d324e1fe33b4f09fd6e8fb0492b580b", "hevc_hdr_long": "1e1647c8e04618d81578f1ed5b11cc36", "hevc_hdr_xlong": "dfa244d597c609abc70d8843f62db7d3"}


class SafeFailure(Exception):
    pass


class Pool:
    """Reuse HTTP/1.1 connections, with two independent concurrent media gets."""
    def __init__(self, timeout=120):
        self.timeout, self.idle, self.lock = timeout, {}, threading.Lock()

    def fetch(self, url, stage, media=False):
        started = time.perf_counter()
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise SafeFailure(stage + ": invalid endpoint")
        key = (parsed.scheme, parsed.hostname, parsed.port)
        with self.lock:
            conn = self.idle.get(key, []).pop() if self.idle.get(key) else None
        if conn is None:
            cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            conn = cls(parsed.hostname, parsed.port, timeout=self.timeout)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        try:
            conn.request("GET", path, headers={"User-Agent": "Tor-Cold-HLS-Benchmark/1.0", "Accept-Encoding": "identity",
                                               "Cache-Control": "no-cache"})
            response = conn.getresponse()
            headers_at = time.perf_counter()
            if response.status != 200:
                raise SafeFailure(stage + ": HTTP " + str(response.status))
            data, first_at, useful_at, scan = bytearray(), None, None, 0
            while True:
                chunk = response.read1(65536)
                if not chunk:
                    break
                now = time.perf_counter()
                first_at = first_at or now
                data.extend(chunk)
                if media and useful_at is None:
                    # Converted HLS starts with MPEG-TS null keepalive packets.
                    # Do not confuse those immediate bytes with available media.
                    while scan + 188 <= len(data):
                        if data[scan] != 0x47:
                            raise SafeFailure(stage + ": malformed transport stream")
                        pid = ((data[scan + 1] & 31) << 8) | data[scan + 2]
                        if pid != 8191:
                            useful_at = now
                            break
                        scan += 188
            finished = time.perf_counter()
            if not response.will_close:
                with self.lock:
                    self.idle.setdefault(key, []).append(conn)
                conn = None
            return bytes(data), {"stage": stage, "headers_s": round(headers_at - started, 4),
                                 "first_byte_s": round((first_at or finished) - started, 4),
                                 "first_non_null_ts_s": round(useful_at - started, 4) if useful_at else None,
                                 "complete_s": round(finished - started, 4), "bytes": len(data),
                                 "finished_at": finished}
        except SafeFailure:
            raise
        except Exception as error:
            # Exception strings may include the signed URL; never expose them.
            raise SafeFailure(stage + ": " + type(error).__name__) from None
        finally:
            if conn is not None:
                conn.close()

    def close(self):
        for connections in self.idle.values():
            for connection in connections:
                connection.close()
        self.idle.clear()


def master_links(body, origin):
    lines = body.decode().splitlines()
    video = next((line.strip() for line in lines if line.strip() and not line.startswith("#")), None)
    audios = [line for line in lines if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line]
    selected = next((line for line in audios if "DEFAULT=YES" in line), audios[0] if audios else None)
    match = re.search(r'URI="([^"]+)"', selected or "")
    if not video or not match:
        raise SafeFailure("master: missing video or audio playlist")
    return urljoin(origin, video), urljoin(origin, match[1])


def segments(body, origin):
    out, start, duration = [], 0.0, None
    for line in body.decode().splitlines():
        if line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line and not line.startswith("#") and duration is not None:
            out.append((start, duration, urljoin(origin, line.strip())))
            start += duration
            duration = None
    if not out:
        raise SafeFailure("playlist: no segments")
    return out


def public_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Tor-Cold-HLS-Benchmark/1.0"})
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except Exception as error:
        raise SafeFailure("metadata check: " + type(error).__name__) from None


def sanitize_status(status):
    converted = status.get("converted", {})
    macs = converted.get("macs", {})
    return {"code": status.get("code"), "replica": status.get("replica"), "mode": macs.get("input_mode"),
            "mac_jobs": macs.get("under_way"), "local_ffmpeg_jobs": len(converted.get("ffmpeg", [])),
            "mac_completed": {name: worker.get("done_here", 0) for name, worker in macs.get("each", {}).items()},
            "recent_mac_stages": macs.get("last", [])}


def inspect(data, kind):
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                                 "stream=codec_name,width,height,start_time,nb_read_frames", "-of", "json", "pipe:0"],
                                input=data, capture_output=True, timeout=25)
        streams = json.loads(result.stdout).get("streams", []) if result.returncode == 0 else []
        expected = "h264" if kind == "video" else "aac"
        stream = next((s for s in streams if s.get("codec_name") == expected), None)
        if stream is None:
            raise SafeFailure("media validation: missing " + expected)
        return stream
    except SafeFailure:
        raise
    except Exception as error:
        raise SafeFailure("media validation: " + type(error).__name__) from None


def trial(args):
    health = public_json(args.base + "/healthz")
    if health.get("code") != args.expected_code:
        raise SafeFailure("Tor code hash does not match expected revision")
    control = public_json(args.control + "/api/status")
    project = next((p for p in control.get("projects", []) if p.get("name") == args.app), None)
    if project is None or project.get("version") != args.expected_version:
        raise SafeFailure("Tor app version does not match expected revision")
    before = sanitize_status(public_json(args.base + "/status"))
    secrets = json.loads(Path(args.secrets).read_text())
    seal = hashlib.sha256(secrets.get("tor", secrets)["TOR_SEAL_KEY"].encode()).digest()
    file_id = "cold-" + args.fixture + "-" + uuid.uuid4().hex
    plain = json.dumps({"u": args.fixture_base + "/fixture/" + FIXTURES[args.fixture], "f": file_id}).encode()
    nonce = os.urandom(12)
    token = base64.urlsafe_b64encode(nonce + AESGCM(seal).encrypt(nonce, plain, None)).decode().rstrip("=")
    origin = args.base + "/hls/" + token + "/"
    client, rows, payloads = Pool(args.timeout), [], []
    started = time.perf_counter()
    try:
        master, timing = client.fetch(origin + "index.m3u8", "master")
        rows.append(timing)
        video_url, audio_url = master_links(master, origin)
        with ThreadPoolExecutor(max_workers=2) as executor:
            video_future = executor.submit(client.fetch, video_url, "video_playlist")
            audio_future = executor.submit(client.fetch, audio_url, "audio_playlist")
            video_data, video_timing = video_future.result()
            audio_data, audio_timing = audio_future.result()
            rows.extend((video_timing, audio_timing))
            video, audio = segments(video_data, video_url), segments(audio_data, audio_url)
            seek = max(i for i, (at, _, _) in enumerate(video) if at <= args.seek)
            pair_metrics, source_snapshots = [], []
            for label, index in [("startup", 0), ("seek", seek)] + [("next" + str(i), seek + i) for i in range(1, 4)]:
                if index >= len(video) or index >= len(audio):
                    raise SafeFailure("fixture too short for requested seek")
                pair_started = time.perf_counter()
                vf = executor.submit(client.fetch, video[index][2], label + "_video", True)
                af = executor.submit(client.fetch, audio[index][2], label + "_audio", True)
                vd, vt = vf.result()
                ad, at = af.result()
                rows.extend((vt, at))
                payloads.extend(((label + "_video", vd, "video"), (label + "_audio", ad, "audio")))
                pair_metrics.append({"case": label, "segment": index, "position_s": video[index][0],
                                     "pair_ready_s": round(max(vt["finished_at"], at["finished_at"]) - pair_started, 4),
                                     "from_master_start_s": round(max(vt["finished_at"], at["finished_at"]) - started, 4)})
        network_finished = time.perf_counter()
    finally:
        client.close()
    for row in rows:
        row["from_master_start_s"] = round(row.pop("finished_at") - started, 4)
    validation = [{"stage": stage, "metadata": inspect(data, kind), "bytes": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()} for stage, data, kind in payloads]
    final_status = public_json(args.base + "/status")
    source_snapshots = [{"stage": "first_seek_media_request_entry_before_playhead_update", "sources": final_status.get("benchmark_source", [])}]
    after = sanitize_status(final_status)
    if after["code"] != args.expected_code:
        raise SafeFailure("Tor code changed during measurement")
    return {"label": args.label, "fixture": args.fixture, "source_identity_sha256": hashlib.sha256(file_id.encode()).hexdigest(),
            "app": args.app, "version": project["version"], "code": health["code"], "replica": health.get("replica"),
            "machine": next((p.get("machine") for p in project.get("placed", []) if p.get("replica") == health.get("replica")), None),
            "public_player_requests": True, "internal_auth_header": False, "seek_hint": False,
            "connection_state": args.connection_state, "client_transport": "pooled HTTP/1.1, two parallel media requests",
            "source_condition": "fresh process and Tor file ID; upstream fixture is disk-cached; exact source cache state before seek recorded separately",
            "source_snapshots": source_snapshots,
            "network_sequence_s": round(network_finished - started, 4), "stages": rows, "pairs": pair_metrics,
            "validation": validation, "before": before, "after": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://tor-cold-bench-1.billybishop4-workers.xyz")
    parser.add_argument("--control", default="https://runners.billybishop4-workers.xyz")
    parser.add_argument("--app", default="tor-cold-bench")
    parser.add_argument("--fixture-base", default="https://tor-mac-bench.billybishop4-workers.xyz")
    parser.add_argument("--fixture", choices=FIXTURES, required=True)
    parser.add_argument("--secrets", default=".private/tor-cold-benchmark.json")
    parser.add_argument("--expected-code", required=True)
    parser.add_argument("--expected-version", type=int, required=True)
    parser.add_argument("--connection-state", choices=["fresh-process", "warm", "unknown"], default="unknown")
    parser.add_argument("--label", required=True)
    parser.add_argument("--seek", type=float, default=37)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = trial(args)
    except SafeFailure as error:
        failure = {"ok": False, "label": args.label, "fixture": args.fixture, "code": args.expected_code,
                   "version": args.expected_version, "error": str(error)}
        args.output.write_text(json.dumps(failure, indent=2) + "\n")
        print(json.dumps(failure), flush=True)
        raise SystemExit(1) from None
    except Exception as error:
        failure = {"ok": False, "label": args.label, "fixture": args.fixture, "code": args.expected_code,
                   "version": args.expected_version, "error": type(error).__name__}
        args.output.write_text(json.dumps(failure, indent=2) + "\n")
        print(json.dumps(failure), flush=True)
        raise SystemExit(1) from None
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"ok": True, "label": result["label"], "fixture": result["fixture"], "code": result["code"],
                      "version": result["version"], "machine": result["machine"], "pairs": result["pairs"]}), flush=True)


if __name__ == "__main__":
    main()
