# tor-mac

Tor's media workers run on three GitHub-hosted Apple Silicon macOS jobs. Runners hosts the app, caches source bytes and finished segments, and assigns work to the Macs. The Mac workers probe media, read its keyframe index, prepare segments, and convert video using VideoToolbox. Audio conversion also runs on the Macs, with separate language tracks preserved.

The production workers are `https://mac-1.billybishop4-workers.xyz` through `mac-3`. Each slot has its own persistent Cloudflare Tunnel. The shared `https://mac.billybishop4-workers.xyz` address also exists; Tor selects individual slots to distribute jobs.

## Source jobs

All processing endpoints require `X-Mac-Key`. JSON jobs contain a source descriptor:

```json
{
  "source": {
    "id": "stable-source-and-version",
    "url": "https://tor-1.example.com/mac-source/source-id",
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

`start` is an absolute source timestamp; `source_start` is the file's timestamp origin. Output uses Tor's existing timestamp convention (10 seconds plus time within the film). `audio` is the original FFprobe stream index, and priority `0` means a player is waiting.

Source credentials are scoped to one source and replica for 10 minutes. Workers accept only HTTPS URLs under `SOURCE_HOST_SUFFIX` (default `billybishop4-workers.xyz`), on the `/mac-source/` route; upstream redirects are rejected. A loopback relay keeps origin credentials out of FFmpeg arguments. Each job authenticates with the origin before accessing the shared cache. Upstream reads are bounded to 512 KiB; the in-memory byte cache is capped at 128 MiB and expires after 10 minutes. Worker replacement discards these caches.

VideoToolbox accelerates video decoding, scaling, HDR conversion, and encoding. Container parsing and audio conversion use the Mac CPU. Jobs are bounded and cancelled when callers disconnect. SIGTERM drains existing jobs before shutdown. Default capacity is three jobs, with one extra urgent request allowed.

## Runners integration

`runners/tor.py` is the Tor application source, recovered from deployed version 91 and updated with source jobs. It retains the existing UI, source/cache storage, language playlists, and original-quality video remuxing. Original video remuxing remains on Runners; unavailable Macs also permit the existing local fallback. Source mode avoids racing local video conversion against a healthy Mac and cancels losing hedged requests.

`MAC_INPUT_MODE=source` selects Mac-side processing. `legacy` restores the old binary upload path. `runners/deploy.py` updates only the inline Python Compose config and code marker, retaining the app's existing Dockerfile, replicas, services, and volumes:

```sh
export RUNNERS_ADMIN_PASSWORD='<admin password>'
python3 runners/deploy.py --expect-version <current-version>       # inspect
python3 runners/deploy.py --expect-version <current-version> --apply
```

A concurrent app version change causes the command to stop. To select the previous protocol, use `--mode legacy` with the current version. Deployment credentials are never printed.

## Jobs and testing

`.github/workflows/macs.yml` keeps three Macs serving; the fourth Mac slot is available for other work. Each job requests a replacement before its six-hour limit and drains after a fixed handover wait. A scheduled keeper checks every 30 minutes for missing slots. Secrets: `MAC_KEY`, the shared `TUNNEL_TOKEN`, and `TUNNEL_TOKEN_1` through `_4` for individual slots. `SOURCE_REVISION` identifies the running commit.

`.github/workflows/benchmark.yml` starts an isolated, temporary slot-4 worker. It does not join the shared tunnel, take a production slot, or replace itself. Cancel it after measurement.

Install FFmpeg and run:

```sh
python3 -m unittest discover -s tests -v
```

Tests cover source authorization and ranges, bounded reads and cancellation, byte-cache expiry and eviction, hardware conversion when available, timestamps, track selection, adjacent audio segments, legacy compatibility, and Runners request contracts.
