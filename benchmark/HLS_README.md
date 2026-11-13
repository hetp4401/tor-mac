# Public HLS cold-start measurements

Historical experiment: production playback was restored to Tor v91 on November 13 after reported regressions. Source delegation, metadata windows, cold-priority dispatch and native seek hints are not active. Results below describe the previous experiments.

These measurements exercise public HLS master, video/audio playlists, parallel first video/audio segments, a first seek, and the following three segment pairs. They use generated media only, through the real Runners and Mac Cloudflare tunnels. The player sends no internal `X-Tor-Key` header. MPEG-TS null keepalive packets do not count as the first useful media byte. The ordinary legacy compact-upload path and unchanged VideoToolbox encoder settings remain in use.

Each controlled trial restarts only the isolated `tor-cold-bench` Python service, checks its exact health code and Runners version, and uses a new source identity. Runners host 26 and Mac slot 4 are held constant. The upstream synthetic fixture is already on disk; this measures a cold Tor source/output cache, not upstream media generation. Mac worker revision is `b88f8ef8d90e697c9a10df23ff8bc910b0f1bcac` (isolated workflow run 43384884553). This controlled series does not target production playback or Mac slots 1–3; a separate deployment smoke check is recorded below.

Baseline Tor is `b88f8ef:runners/tor.py` (semantic code `91e49ece51f0`). The tested candidate source SHA256 is `a66b344073ec301448105bb65840a438a26f84d4e4c081db0225b25b4912537c`, with `TOR_PROBE_WINDOWS=1` and priority windows of 0, 1000 or 250 ms recorded per artifact. The selected production setting is `TOR_COLD_PRIORITY_MS=1000` to prioritize cold first frames. The generic default remains zero. The intermediate cleanup hash `0bcf53df…` removed the disabled feature; the selected version restores the tested bounded reservation.

## Cache conditions matter

The first 60-second H264/HDR fixtures are only 17–22 MB. Their baseline probe read-ahead warmed the entire source, so their 37-second seeks are **output-cold, source-warm**. A 240-second, 86,824,262-byte lossless HDR concatenation also warmed target blocks 5–6 before its 137-second seek. These results are preserved and must not be advertised as cold-source seek improvements.

The 960-second fixture is a lossless 16× concatenation of the same HDR clip, 347,287,742 bytes (42 blocks of 8 MiB). Its 617-second seek needs source block 26, away from the initial probe/read-ahead and final metadata block. Identical test-only instrumentation captures cache/tail/read/queue state at seek request entry, before playback work, and exports it through the isolated app's status response afterward. No diagnostic endpoint or patch is added to production. The first pair captured video entry; subsequent pairs capture the first audio or video request under a dedicated lock. A parallel audio request can already be fetching the block when video arrives: artifacts distinguish cached, absent and in-flight data rather than inferring coldness from a new ID.

The earlier candidate `9eac001a…` exposed a 10-second source-read priority inversion on one small H264 seek. That failed comparison is retained. The final candidate includes a tested promotion fix for queued/in-flight source demand; it preserves read concurrency limits and avoids duplicate fetches. The one-second first-frame priority reservation was disabled for the main three-pair comparison; separate post-fix priority trials were then measured before selecting the production setting.

## Verified cold-source results (three alternating pairs)

All six 617-second seek snapshots show target block 26 absent from cache and tails. The first baseline video snapshot already had a fetch in flight; the later first-media snapshots capture absence before either request initiates its read. The values below are medians in seconds from the 347 MB fixture, not the small source-warm clips.

| Measure | Baseline | Candidate, priority disabled |
|---|---:|---:|
| Master playlist | 0.852 | 0.693 |
| Master start to first complete video/audio pair | 2.273 | 2.306 |
| First cold-source seek pair | 2.094 | 1.702 |
| Following three pairs, total | 2.739 | 3.279 |
| Full measured playback sequence | 7.111 | 7.036 |
| Seek source preparation (worker telemetry) | 0.370 | 0.210 |
| Seek Mac stage (worker telemetry) | 1.420 | 1.420 |

Source preparation improved consistently. Overall startup was effectively flat, and subsequent-segment completion varied; these three pairs establish no universal end-to-end speedup. The slower second candidate seek spent 2.01 s in its Mac stage versus 1.24 s for the paired baseline, while source preparation improved from 0.69 to 0.21 s. Every video response remained 1920×1080 with 24 decoded frames; encoder quality flags were unchanged. Full trial data and request-entry cache evidence are in `results/benchmark-hls-*-hevc_hdr_xlong-*.json`; the numerical summary is `results/benchmark-hls-cold-summary.json`.

## Selected bounded priority: small-sample results

After fixing source demand promotion, two additional verified cold-source trials used a 1000 ms first-frame reservation on the same host and worker. Both improved cold first frames; this setting was selected because cold startup and seek latency were the requested priority. These are only two sequential exploratory trials, not a large randomized performance study.

| Measure (seconds) | Baseline median, n=3 | Priority 1000 ms median, n=2 |
|---|---:|---:|
| Cold startup to first complete video/audio pair | 2.273 | 1.596 |
| First cold-source seek pair | 2.094 | 1.595 |
| Following three pairs, total | 2.739 | 4.011 |
| Full measured sequence | 7.111 | 7.205 |

The cold medians improved approximately 30% and 24%, but the following segments took longer; the full sequence was effectively flat. This tradeoff must be disclosed alongside the first-frame numbers. The isolated Mac had **one job slot**. Production has **three workers with three slots each**. The harness also requests following segments immediately rather than at playback pace, so these completion times are not measurements of browser rebuffering on the production pool. All valid output retained 1080p resolution and 24 frames per one-second video response, with unchanged encoder quality settings.

A 250 ms reservation did not show a benefit in its one valid trial: startup 2.702 s, cold seek 1.837 s, following-three total 4.092 s. Near the worker's time limit, its second trial used local FFmpeg fallback: zero Mac completions/timing records and seven local FFmpeg jobs were observed. That artifact is retained with `performance_comparison_valid=false` and an exclusion reason. Its 8.387 s startup and 8.461 s seek are **excluded from all Mac latency medians**; they are not a valid comparison of the priority settings.

## Production deployment check

Tor version 95 uses the measured `a66b344073ec` source with legacy compact uploads and `TOR_COLD_PRIORITY_MS=1000`. One before/after smoke measurement used the same 347 MB fixture, fresh source identities, production host 29, and the existing three Mac workers. Startup was 2.454 → 1.755 s, the 617-second seek was 3.641 → 1.914 s, and the full request sequence was 7.457 → 6.572 s. The following three pairs took 1.360 → 2.901 s. Every video/audio response passed ffprobe validation; video retained 1080p and 24 frames.

These are single observations, with no production cache instrumentation and different process/connection state across deployment. They verify the deployed path and are not included in the controlled medians or presented as a general speedup. Raw results are `results/production-cold-before.json` and `results/production-cold-after.json`.

## Reproducing

Requirements: Python 3 with `cryptography`, FFmpeg/ffprobe, local fixture generation encoders `libx264` and `libx265`, a full Git history containing `b88f8ef`, and authorized credentials for **dedicated benchmark apps**. The fixture server's Dockerfile installs its own FFmpeg. Generate synthetic media before measurement:

```sh
mkdir -p /tmp/tor-benchmark-fixtures benchmark/results
BENCH_FIXTURES=/tmp/tor-benchmark-fixtures python3 benchmark/benchmark.py --generate-only
git show b88f8ef:runners/tor.py > /tmp/tor-baseline.py
cp runners/tor.py /tmp/tor-candidate.py
```

Deploy the fixture app using `benchmark/benchmark.compose.yaml` with its named fixture volume, the server files and Dockerfile. Its environment uses `BENCH_SOURCE_KEY`, `BENCH_ADMIN_KEY`, `MAC_KEY`, and `BENCH_PUBLIC_URL`. Wait for both `ready=true` and the expected harness hash. Clone the production Tor compose into the **dedicated** `tor-cold-bench` app with a fresh seal/KV key, only Mac slot 4, legacy input mode, one replica, and no embedded/shared cloudflared token. Normal Runners deployment assigns the app its own route. The restart driver intentionally targets only these named isolated apps and refuses placement changes.

Credentials are read from the operator workspace's `.private/tor-mac-upgrade.json` (`admin`) and `.private/tor-cold-benchmark.json` (`TOR_SEAL_KEY`). Signed HLS tokens remain in memory; result files omit source URLs, tokens and secret values. Set `--workspace` to that authorized workspace and supply an immutable candidate source file.

```sh
python3 benchmark/benchmark_cold_trials.py --phase baseline_return \
  --source /tmp/tor-baseline.py --machine 26 --trials 1 \
  --fixtures hevc_hdr_xlong --seek 617 --workspace /path/to/operator-workspace \
  --output-dir benchmark/results
python3 benchmark/benchmark_cold_trials.py --phase candidate_no_priority \
  --source /tmp/tor-candidate.py --machine 26 --trials 1 --priority-ms 0 \
  --fixtures hevc_hdr_xlong --seek 617 --workspace /path/to/operator-workspace \
  --output-dir benchmark/results
```

Alternate baseline/candidate phases; use `--trials N --skip phase-fixture-1,...` for later numbered trials without overwriting earlier results. Artifacts record exact deployed code/version, semantic source hash, worker revision, flags, source snapshots, first useful media/complete response timings, and ffprobe validation of every response. Client-side media validation runs after all measured network requests.

Afterward, POST the fixture server's authenticated `/cleanup` endpoint, verify all four explicit generated files are removed, delete only the two temporary apps, and cancel the isolated Mac workflow. The cleanup endpoint preserves unrelated volume files.
