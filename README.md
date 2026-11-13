# Frame

Frame's media workers run on three GitHub-hosted Apple Silicon macOS jobs. Runners probes and prepares compact video segments, then the Macs convert them using VideoToolbox. Runners caches source bytes and finished segments and preserves separate language tracks.

Playback was restored to the saved Tor v91 backend on November 13 after reported regressions. This restores the earlier probing, buffering, Mac dispatch, fallback, cancellation and seek behavior. Frame's UI, PWA, native Apple player and renamed domains remain. The additional native seek-hint requests and experimental cold-start priority/window changes have been removed.

The Mac workers retain experimental JSON source-processing endpoints for the standalone benchmark harness. Production Frame no longer invokes them. The worker's compact binary conversion path is compatible with Tor v91, so this rollback does not require restarting the Macs. See `benchmark/README.md` for historical measurements; they do not override the reported playback regression.

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

`runners/tor.py` combines the restored Tor v91 backend with the current Frame web bundle and source labels. Original video remuxing and audio preparation happen on Runners; converted video uses compact binary uploads to the Macs with the original local fallback and buffering rules. The current UI offers Original, As is and Converted streams without separate Direct/External entries.

`runners/deploy.py` updates only the inline Python Compose config and code marker, retaining the app's existing Dockerfile, replicas, services, environment and volumes:

```sh
export RUNNERS_ADMIN_PASSWORD='<admin password>'
python3 runners/deploy.py --expect-version <current-version>       # inspect
python3 runners/deploy.py --expect-version <current-version> --apply
```

A concurrent app version change causes the command to stop. The former source-mode and cold-priority deployment options have been removed. Old `MAC_INPUT_MODE`, `TOR_COLD_PRIORITY_MS` and `TOR_PROBE_WINDOWS` environment values have no effect on the restored backend.

## Jobs and testing

`.github/workflows/macs.yml` keeps three Macs serving; the fourth Mac slot is available for other work. Each job requests a replacement before its six-hour limit and drains after a fixed handover wait. A scheduled keeper checks every 30 minutes for missing slots. Secrets: `MAC_KEY`, the shared `TUNNEL_TOKEN`, and `TUNNEL_TOKEN_1` through `_4` for individual slots. `SOURCE_REVISION` identifies the running commit.

`.github/workflows/benchmark.yml` starts an isolated, temporary slot-4 worker. It does not join the shared tunnel, take a production slot, or replace itself. Cancel it after measurement.

Install FFmpeg and run:

```sh
python3 -m unittest discover -s tests -v
```

Tests cover compact Mac request handling, worker source authorization and ranges, bounded reads and cancellation, byte-cache expiry and eviction, hardware conversion when available, timestamps, track selection, adjacent audio segments, and web bundle consistency. Tests specific to the reverted experimental backend were removed with that backend code.
