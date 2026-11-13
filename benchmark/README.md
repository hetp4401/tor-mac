# Before/after measurements

Production uses compact uploads (`MAC_INPUT_MODE=legacy`) to prioritize cold starts and first seeks. Full Mac-owned source processing is available explicitly, but is not the default.

The final run compared 36 successful conversions on the same GitHub-hosted Mac (slot 4), called from the same Runners host through real Cloudflare tunnels. Each output was one second / 24 frames, with matching timestamps and dimensions. The source fixtures were generated 60-second videos: H264 SDR 720p and HEVC 10-bit HDR 1080p, both with 10-second keyframe spacing and AAC audio. Both paths muxed the same audio; production normally requests video and audio separately.

The source files were already cached on the Runners host. The old path ran the deployed v91 preparation algorithm using the production 8 MiB read-block setting, then uploaded compact Matroska. The new path sent an authenticated source descriptor and performed that preparation on the Mac. Protocol order alternated across three trials per case. A fresh per-run source identity kept previous benchmark runs from warming the new Mac cache.

Final worker revision: `a71659ee130a093afc09e234619b747785958c70`. Full results and validation metadata are in `results/final-mac4.json`.

| Media | Measure | Old | Mac-owned |
|---|---|---:|---:|
| H264 | Startup segment median | 0.710 s | 0.600 s |
| H264 | Seek segment median | 0.858 s | 0.940 s |
| H264 | Repeated seek median | 0.977 s | 0.898 s |
| HEVC HDR | Startup segment median | 0.769 s | 0.773 s |
| HEVC HDR | Seek segment median | 1.191 s | 1.056 s |
| HEVC HDR | Repeated seek median | 1.378 s | 1.117 s |

The first uncached seek was slower: H264 0.964 → 1.834 seconds, HDR 1.230 → 2.443 seconds. Cold probe plus first segment was H264 0.855 → 1.494 seconds and HDR 1.198 → 1.402 seconds. Mac-side cache reuse helps subsequent work; these results do not establish a universal playback speedup. Repeated conversion deliberately bypasses Tor's output cache; ordinary replay can use finished cached segments without another conversion.

The initial source-URL prototype used open-ended upstream reads and regressed latency and traffic. It was not enabled in Tor. Bounded reads and Mac-side compact preparation fixed that over-reading: final cold seeks received about 3.67/4.72 MB on the Mac, and warm source seeks required zero new media bytes. Every final source conversion reported indexed preparation. The initial prototype artifact is retained as evidence; its preparation harness used 1 MiB blocks, so it is not the final matched-configuration comparison.

The harness hosts only generated media. `/run` and `/results` require its admin bearer; `/mac-source/<id>` requires the source bearer. Public `/fixture/<id>` redirects expose only the two generated samples for Tor integration checks. The temporary benchmark app and isolated Mac workflow should be removed/stopped after verification.
