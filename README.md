# Frame

Frame's media workers run on three GitHub-hosted Apple Silicon macOS jobs. Runners probes and prepares compact video segments, then the Macs convert them using VideoToolbox. Runners caches source bytes and finished segments and preserves separate language tracks.

Playback now uses a cancellable session for each browser playback attempt. Switching, closing or failing a stream releases its reads, queued work and running media processes. Explicitly closed sessions reject late requests; idle sessions release work after 45 seconds and can renew when a suspended native player resumes. Another viewer of the same source keeps their playback.

The browser list no longer starts conversions for unselected files, and playback no longer converts the whole film. Preparation follows a bounded 12-piece window around the playhead. Resumes prepare their saved position. Seek requests coalesce, and the server rejects older seek sequence numbers. Current clients report their playback position: downloaded-ahead video and late requests cannot move the preparation window or cancel the audio needed at the new position. A seek cancels obsolete background preparation while allowing an already requested fragment to finish, so native playback can finish its current read and follow the new target. Switching or closing still cancels the entire session. Finished source and media caches remain reusable. Original video and audio initialization fragments are restored after a restart and rebuilt if evicted while their media segments remain cached.

The probed codecs correct filename guesses. Compatible H.264 video uses the original video fragments with AAC transport-stream audio for broad browser playback; Original retains lossless audio. Actual incompatible video uses the existing compact Mac/VideoToolbox protocol. A browser decode failure can request full encoding while preserving position. Retry and fallback attempts are bounded. As-is files forward byte ranges in 64 KiB chunks with no speculative block downloads or mandatory media probe.

Healthy Mac requests get one initial worker; slow requests can hedge. Losing and abandoned requests are disconnected. If a small segment upload is already in progress, it finishes under a five-second upload deadline before disconnecting; this avoids leaving the tunneled Mac waiting for a partial request body. No new work is prepared after cancellation. Experimental JSON worker endpoints remain available for benchmarks but are not used by production playback.

The production workers are `https://frame-mac-1.billybishop4-workers.xyz` through `frame-mac-3`. Each slot has its own persistent Cloudflare Tunnel. The shared `https://frame-mac.billybishop4-workers.xyz` address also exists; Frame selects individual slots to distribute jobs. The previous `mac` and `mac-1` through `mac-4` hostnames remain aliases. The rename retained the existing tunnel UUIDs, tokens, and running workers; slot 4 remains available for isolated benchmarks. See [the tunnel mapping](docs/mac-tunnels.json).

## App and compatibility addresses

Open [Frame](https://frame.billybishop4-workers.xyz/watch). Its Runners app is `frame`; deployment defaults to that name. Runners manages its shared and numbered public tunnels, so it needs no app-specific cloudflared sidecar or tunnel token.

The previous `tor` app is a small streaming reverse proxy to Frame, defined in `runners/legacy-tor.compose.yaml`. This keeps existing bookmarks and installed web apps working on their original origin, where browser-saved library and playback progress remain. A new visit to the Frame domain has its own browser storage. The legacy internal `TOR_*` environment variables and Python filename remain compatible.

The [editable web sources](runners/web/README.md) cover the responsive UI, native Apple video controls, unified size-sorted source list, and PWA install/update behavior.

## Experimental worker source jobs

All processing endpoints require `X-Mac-Key`. JSON jobs contain a source descriptor:

```json
{
  "source": {
    "id": "stable-source-and-version",
    "url": "https://frame-1.example.com/mac-source/source-id",
    "headers": {"Authorization": "Bearer <short-lived-source-grant>"}
  },
  "start": 63,
  "duration": 4,
  "source_start": 0,
  "audio": 1,
  "hdr": 0,
  "width": 1920,
  "segment_seconds": 1,
  "priority": 0
}
```

- `POST /probe`: source descriptor only; returns FFprobe metadata and Matroska Cues. The small metadata cache lasts 10 minutes.
- `POST /convert`: probes/seeks/prepares on the Mac; returns H.264/AAC MPEG-TS. Indexed Matroska uses compact, keyframe-aware preparation. Other inputs use bounded HTTP seeking with FFmpeg. `audio: -1` omits muxed audio; `segment_seconds` requests periodic output keyframes for batches.
- `POST /audio`: source/timing/audio fields, plus `original: false` for AAC stereo MPEG-TS or `true` for FLAC fragmented MP4 preserving channels.
- The original binary `POST /convert?t=&l=&s=&a=&h=&w=&p=&k=` protocol remains available for old clients and comparison tests.
- `GET /healthz`: engine, capabilities, revision, capacity, draining status, and bounded recent timings/cache statistics. No access keys or source URLs are returned.

`start` is an absolute source timestamp; `source_start` is the file's timestamp origin. Output uses Frame's existing timestamp convention (10 seconds plus time within the film). `audio` is the original FFprobe stream index, and priority `0` means a player is waiting.

Source credentials are scoped to one source and replica for 10 minutes. Workers accept only HTTPS URLs under `SOURCE_HOST_SUFFIX` (default `billybishop4-workers.xyz`), on the `/mac-source/` route; upstream redirects are rejected. A loopback relay keeps origin credentials out of FFmpeg arguments. Each job authenticates with the origin before accessing the shared cache. Upstream reads are bounded to 512 KiB; the in-memory byte cache is capped at 128 MiB and expires after 10 minutes. Worker replacement discards these caches.

VideoToolbox accelerates video decoding, scaling, HDR conversion, and encoding. Container parsing and audio conversion use the Mac CPU. Jobs are bounded and cancelled when callers disconnect. SIGTERM drains existing jobs before shutdown. Default capacity is three jobs, with one extra urgent request allowed.

## Runners integration

`runners/tor.py` contains the streaming backend, the embedded lifecycle helpers from `runners/playback_lifecycle.py`, and the current Frame web bundle. Original video remuxing and audio preparation happen on Runners; converted video uses compact binary uploads to the Macs with bounded prefetch and delayed local fallback. The current UI offers Original, As is and Converted streams without separate Direct/External entries.

`runners/deploy.py` updates only the inline Python Compose config and code marker, retaining the app's existing Dockerfile, replicas, services, environment and volumes:

```sh
export RUNNERS_ADMIN_PASSWORD='<admin password, when required>'
python3 runners/deploy.py --expect-version <current-version>       # inspect
python3 runners/deploy.py --expect-version <current-version> --apply
```

The command checks the expected app version before preparing an update. If this Runners installation explicitly permits deployments without authentication and the app has no password, the password is optional. The former source-mode and cold-priority deployment options have been removed. Old `MAC_INPUT_MODE`, `TOR_COLD_PRIORITY_MS` and `TOR_PROBE_WINDOWS` environment values have no effect on the restored backend.

## Jobs and testing

`.github/workflows/macs.yml` keeps three Macs serving; the fourth Mac slot is available for other work. Each job requests a replacement before its six-hour limit and drains after a fixed handover wait. A scheduled keeper checks every 30 minutes for missing slots. Secrets: `MAC_KEY`, the shared `TUNNEL_TOKEN`, and `TUNNEL_TOKEN_1` through `_4` for individual slots. `SOURCE_REVISION` identifies the running commit.

`.github/workflows/benchmark.yml` starts an isolated, temporary slot-4 worker. It does not join the shared tunnel, take a production slot, or replace itself. Cancel it after measurement.

Install FFmpeg and run:

```sh
python3 -m unittest discover -s tests -v
```

Tests cover compact Mac request handling, worker source authorization and ranges, bounded reads and cancellation, byte-cache expiry and eviction, hardware conversion when available, timestamps, track selection, adjacent audio segments, and web bundle consistency. Tests specific to the reverted experimental backend were removed with that backend code.

## Health dashboard

Open `/health` (or **Health** in the Frame header) for the configured Mac workers and machines currently hosting Frame replicas. Placement changes automatically update the scope, totals and trends. Host CPU, RAM, disk and network are separate from each Frame replica's CPU cores, memory and I/O. Streaming details include job capacity, conversion times, queued work, source throughput, and recent server segment preparation times. These timings describe the serving replica, not browser first-frame or seek latency. Search, attention filters and per-machine details work on phones and desktops.

The Mac collector (`host_metrics.py`, installed with `requirements-worker.txt`) samples each CPU core, RAM, shared disk capacity, disk I/O and physical network traffic every five seconds on a background thread. `gpu_metrics.py` reads available GPU driver counters directly through IOKit without spawning monitoring commands. Health requests only copy the latest sample. The hosted AppleParavirtGPU driver exposes memory counters, but no utilization or per-GPU-core counters; missing readings remain unavailable. Physical Apple GPUs can additionally report total, renderer and tiler utilization.

Mac charts have CPU, GPU, RAM, disk, network and conversion tabs with 5-minute, 15-minute and one-hour windows. CPU lines identify individual logical cores. GPU memory in use and allocated memory remain separate; graphics activity is not an encoder-occupancy measurement. Missing samples and monitoring pauses remain gaps. Mac history is bounded and collected while the dashboard is viewed; replica-host CPU/RAM/network/disk I/O history uses Runners' existing one-hour feed. That host feed does not expose individual CPU cores. Worker rollout status and live verification are recorded in `docs/health-dashboard.json`.

The read-only `/api/health` endpoint shares a bounded refresh across visitors, at most once every 15 seconds. It uses up to five concurrent reads, four-second upstream timeouts and bounded in-memory Mac history; it never starts media work. Monitoring is demand-driven, and the page stops polling while hidden or paused. Failed refreshes retain last-known values with stale/offline labels; unavailable measurements never appear as zero. Runners data is reduced to an allowlist of health fields, excluding environment, configuration and tunnel details. Health collection does not change the playback scheduler.

Run dashboard checks with `python3 -m unittest discover -s tests -p 'test_health_dashboard.py' -v` and `node --test runners/web/tests/health.test.cjs`. `verification/health_server.py` uses live read-only feeds for the native iPhone dashboard check. The page sources are `runners/web/health.html`, `health.css` and `health.js`; collection and normalization are in `runners/health_dashboard.py`. `runners/web/build.py` embeds all of them into the single deployed Python file.

## Playback verification

Run the focused lifecycle and HTTP integration tests with `python3 -m unittest discover -s tests -p 'test_playback*.py' -v`, and the browser lifecycle tests with `node --test runners/web/tests/session.test.cjs`. The HTTP suite uses actual FFprobe, FFmpeg and the local VideoToolbox worker, plus a bounded local source fixture. It checks byte ranges, codec correction, decoding, resume/seek targets, stale requests, source failure and cancellation.

`verification/simulator_server.py` and `verification/ios/project.yml` run the Frame UI in native WebKit on an iPhone Simulator, exercising Original, copied video with AAC audio, As is, a wrongly labelled file, forced conversion, seeks and rapid switches. Telemetry is written to `verification/simulator-result.json`. These are local fixture measurements, not guarantees for remote source availability or network latency.

The `--planet` check uses a separate local playback session with Planet Earth II S01E01's 4K HDR remux bytes, through an independent production file lease. It tests three distant seeks and a rapid seek burst, including actual playback advancement. The simulator may require conversion for this format; results record the mode actually used. `verification/planet-seek-result.json` records the result and local work remaining after stop. This does not move an existing production viewer's playhead.
