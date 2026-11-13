"""Restart only the isolated tor-cold-bench Python service before each HLS trial."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

APP = "tor-cold-bench"
CONTROL = "https://runners.billybishop4-workers.xyz"
BASE = "https://tor-cold-bench-1.billybishop4-workers.xyz"
WORKER = "https://mac-4.billybishop4-workers.xyz"
WORKER_REVISION = "b88f8ef8d90e697c9a10df23ff8bc910b0f1bcac"


def request(url, method="GET", body=None, admin=None):
    headers = {"User-Agent": "Tor-Cold-Benchmark/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if admin:
        headers["X-Admin-Password"] = admin
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def instrument(source, seek):
    """Identical read-only cache telemetry for baseline/candidate, isolated app only."""
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "status")
    ret = next(n for n in reversed(node.body) if isinstance(n, ast.Return))
    lines = source.decode().splitlines(keepends=True)
    edits = [(ret.lineno - 1, '    out["benchmark_source"] = list(_benchmark_seek_snapshots)\n')]
    handler = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Handler")
    for method in ("hls_piece", "hls_audio"):
        piece = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == method)
        opened_line = next(n for n in piece.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Tuple) for t in n.targets))
        edits.append((opened_line.end_lineno, '        _benchmark_capture_seek(key, n, ' + repr(method) + ')\n'))
    for at, text in sorted(edits, reverse=True):
        lines.insert(at, text)
    helper = r'''
_benchmark_seek_snapshots = []
_benchmark_snapshot_lock = threading.Lock()

def _benchmark_capture_seek(key, n, method):
    if n != int(BENCH_SEEK):
        return
    with _benchmark_snapshot_lock:
        if _benchmark_seek_snapshots:
            return
        rows = _benchmark_source_snapshot()
        for row in rows:
            row["captured_at_entry"] = method
            row["captured_monotonic"] = time.monotonic()
        _benchmark_seek_snapshots.extend(rows)

def _benchmark_source_snapshot():
    out = []
    with hls_lock:
        for key, info in list(probed.items()):
            size = src_meta.get(key, {}).get("size")
            if not size or not info.get("pos"):
                continue
            cuts = info.get("cuts", [])
            n = max((i for i, at in enumerate(cuts[:-1]) if at - info.get("start", 0) <= BENCH_SEEK), default=0)
            a, z, _ = piece_span(key, n)
            blocks = list(range(a // SRC_BLOCK, (z - 1) // SRC_BLOCK + 1))
            state = []
            for b in blocks:
                ev = reading.get((key, b))
                state.append({"block": b, "cached": (key, b) in src_blocks,
                              "has_tail": (key, b) in src_tails, "reading": ev is not None,
                              "busy": ("src", key, b) in busy, "waiting": (key, b) in up_wait,
                              "going": (key, b) in up_going})
            out.append({"source_hash": hashlib.sha256(key.encode()).hexdigest(), "size": size,
                        "block_bytes": SRC_BLOCK, "seek_seconds": BENCH_SEEK, "piece": n,
                        "span": [a, z], "target_blocks": state,
                        "cached_blocks": sorted(b for k, b in list(src_blocks) if k == key),
                        "reading_blocks": sorted(b for k, b in list(reading) if k == key)})
    return out

'''.replace("BENCH_SEEK", repr(seek))
    lines.insert(node.lineno - 1, helper)
    result = "".join(lines).encode()
    compile(result, "instrumented-isolated-benchmark", "exec")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["baseline", "candidate", "candidate_no_priority", "candidate_short_priority", "baseline_return"], required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--machine", type=int, required=True)
    parser.add_argument("--skip", default="")
    parser.add_argument("--priority-ms", type=int, default=1000)
    parser.add_argument("--fixtures", default="h264,hevc_hdr")
    parser.add_argument("--seek", type=float, default=37)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    from benchmark_hls import FIXTURES
    root = Path(__file__).resolve().parent
    workspace = args.workspace.resolve() if args.workspace else next(p for p in root.parents if (p / ".private/tor-mac-upgrade.json").is_file())
    output_dir = args.output_dir.resolve() if args.output_dir else root
    output_dir.mkdir(parents=True, exist_ok=True)
    admin = json.loads((workspace / ".private/tor-mac-upgrade.json").read_text())["admin"]
    repo = root.parent if (root.parent / "runners/deploy.py").is_file() else workspace / "tor-mac"
    spec = importlib.util.spec_from_file_location("tor_deployer", repo / "runners/deploy.py")
    deployer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deployer)
    original = args.source.read_bytes()
    compile(original, "benchmark-source", "exec")
    skip = set(filter(None, args.skip.split(",")))
    results = []
    for trial in range(1, args.trials + 1):
        for fixture in args.fixtures.split(","):
            label = f"{args.phase}-{fixture}-{trial}"
            if label in skip:
                continue
            current = request(CONTROL + "/api/projects/" + APP)
            assert current["name"] == APP and current["replicas"] == 1
            placements = current.get("placed", [])
            if placements and placements[0].get("machine") != args.machine:
                raise RuntimeError("isolated benchmark placement changed")
            marker = label + "-" + uuid.uuid4().hex[:12]
            source = instrument(original, args.seek) + ("\n# Isolated cold benchmark process marker: " + marker + "\n").encode()
            compose, code = deployer.compose_source(current["compose"], source)
            flags = ({"TOR_COLD_PRIORITY_MS": str(args.priority_ms), "TOR_PROBE_WINDOWS": "1"} if args.phase.startswith("candidate")
                     else {"TOR_COLD_PRIORITY_MS": None, "TOR_PROBE_WINDOWS": None})
            updated = request(CONTROL + "/api/projects/" + APP, "PUT",
                              {"compose": compose, "files": current["files"], "port": 8080, "replicas": 1,
                               "env": {"BENCHMARK_RUN_ID": marker, "MAC_INPUT_MODE": "legacy", **flags}}, admin)
            version = updated["version"]
            print(json.dumps({"stage": "restart", "label": label, "version": version, "code": code}), flush=True)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    health = request(BASE + "/healthz")
                    worker = request(WORKER + "/healthz")
                    if health.get("code") == code and worker.get("revision") == WORKER_REVISION and worker.get("jobs") == 0 and not worker.get("draining"):
                        break
                except Exception:
                    pass
                time.sleep(2)
            else:
                raise RuntimeError("isolated Tor/worker revision did not become ready")
            fixture_url = "https://tor-mac-bench.billybishop4-workers.xyz/fixture-data/" + FIXTURES[fixture]
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    req = urllib.request.Request(fixture_url, method="HEAD", headers={"User-Agent": "Tor-Cold-Benchmark/1.0"})
                    with urllib.request.urlopen(req, timeout=15) as response:
                        if int(response.headers.get("Content-Length", "0")) > 0:
                            break
                except Exception:
                    time.sleep(2)
            else:
                raise RuntimeError("synthetic fixture did not become ready")
            output = output_dir / ("benchmark-hls-" + label + ".json")
            completed = subprocess.run([sys.executable, "-u", str(root / "benchmark_hls.py"), "--fixture", fixture,
                                        "--expected-code", code, "--expected-version", str(version), "--label", label,
                                        "--connection-state", "fresh-process", "--seek", str(args.seek), "--output", str(output)], cwd=workspace)
            if completed.returncode:
                raise RuntimeError("HLS trial failed")
            result = json.loads(output.read_text())
            if result["machine"] != args.machine:
                raise RuntimeError("isolated benchmark placement changed during trial")
            result["semantic_source_sha256"] = hashlib.sha256(original).hexdigest()
            result["worker_revision"] = WORKER_REVISION
            result["benchmark_env"] = {"MAC_INPUT_MODE": "legacy", **flags}
            output.write_text(json.dumps(result, indent=2) + "\n")
            results.append({"label": label, "version": version, "code": code, "output": output.name})
    (output_dir / ("benchmark-hls-" + args.phase + "-manifest.json")).write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Deployment/auth exceptions can carry sensitive request details.
        print(json.dumps({"ok": False, "error": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
