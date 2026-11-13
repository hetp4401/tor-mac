# Frame web interface

These files are the editable source of the `/watch` interface embedded in
`runners/tor.py`. The deployed application remains a single Python file; no
frontend package manager or build service is required.

| File | Responsibility |
| --- | --- |
| `head.html` | Document metadata, theme, responsive layouts, player and PWA styles |
| `body.html` | Navigation, catalog, search, library, title page and episode sheet |
| `core.js` | Catalog loading, search, library, title and episode rendering |
| `versions.js` | Source classification, size ordering, source actions and sheet behavior |
| `player.html`, `player.js` | Playback, native Apple controls, fallback controls, routing and navigation |
| `pwa/` | Manifest, icons, service worker and installation/update client |
| `tests/` | Focused source, player lifecycle and service-worker tests |

`build.py` replaces the single `<!-- TOR_PLAYER -->` marker in `body.html`, then
concatenates scripts in this order: `core.js`, `versions.js`, `player.js`,
`pwa/client.js`. They share one script scope. Existing element IDs, hash routes
and `fb.*` local-storage keys are integration contracts. The Frame rename keeps
those keys and existing PWA identity/cache protocols so saved libraries and
installed-app identity survive the visual rename.

## Build and preview

Run commands from the repository root:

```sh
python3 runners/web/build.py
python3 runners/web/build.py --check
python3 runners/web/preview.py --port 8769 --upstream https://frame.billybishop4-workers.xyz
```

The build updates only the embedded `WATCH_PAGE` and `WEB_FILES` values in
`runners/tor.py`, then checks Python syntax. `--check` fails when those embedded
values are stale and does not write them. `--html /tmp/frame-preview.html` also
exports the generated page; normal browser testing should use the preview
server because the page needs same-origin APIs and media routes.

Open `http://127.0.0.1:8769/watch`. The preview reads local web sources on every
request and proxies other GET/HEAD requests to the upstream application. It
binds to loopback and does not provide write-method proxies. Playing an upstream
source still uses the live application's streaming resources.

For isolated media checks, add `--fixtures /absolute/path/to/media`. Fixture
files are served under `/test-media/` and `/hls/ui-test/`, including byte-range
responses. Fixture media is optional and is not deployed by the build.

The interface uses borderless charcoal surfaces and vivid violet primary actions.
Decorative outlines are removed; keyboard focus remains visible. Motion respects
the device’s reduced-motion preference.

## Playback and source selection

Available versions form one list, sorted numerically by source-file size;
unknown sizes come last. The saved ordering can be largest or smallest first. The sticky
count stays visible as more results load while scrolling; there is no Show more step.
Badges distinguish Original, As is and Converted sources.
Source size describes the source file, not the final converted stream size. A sticky toolbar shows loaded and total playback options. IntersectionObserver appends bounded batches near the bottom of a page or episode sheet; hidden lists stop observing. Keyboard navigation can load the next batch, and browsers without observers retain a button fallback.

Apple Safari uses the browser's native video controls and prefers native HLS
when supported. Custom gesture surfaces and controls are disabled in this mode
so they cannot intercept system video gestures. Playback attempts system
fullscreen during a user gesture; when the browser declines it, explicit Play
and Full screen controls remain available. PiP and AirPlay controls appear only
when their APIs are available. Other supported browsers retain the custom
player and hls.js fallback. Native fullscreen exits and PiP browsing preserve
the current playback session where supported. Touch playback requests fullscreen during the original tap and attempts landscape orientation only where the browser supports it; exiting, PiP and navigation release the lock. Safari may keep rotation under OS control.

Native HLS seeks send a throttled, cancellable time hint to the existing reader. The server maps cached metadata to the target piece and prioritizes that video/audio pair; it never starts a new source probe just for a hint. Unknown or inactive sources are skipped. This restores early seek preparation without downloading playlists twice; it is not a measured latency guarantee.

Chromium desktop/mobile viewport checks verify layout and application flows.
Automated tests verify lifecycle decisions using simulated browser APIs. Neither
proves iPhone/iPad system fullscreen, pinch behavior, real Safari PiP/AirPlay, or
installed-app behavior. Those require a smoke test on the relevant Apple device
and OS version, with a real playable source and, for AirPlay, a receiver.

## Installation, caching and updates

The Library install button uses a browser install prompt when available, or
shows Safari's Add to Home Screen / Add to Dock instructions. There is no
automatic install prompt. Its initial visibility uses the HTML `hidden`
attribute, which the PWA client controls.

The service worker uses network-first navigation with a five-second timeout and
a cached `/watch` shell as fallback. A bounded cache holds the manifest, icons
and pinned hls.js asset. API responses, catalog artwork, media, playlists,
signed source URLs and range requests are not service-worker cached. Offline
mode can display the interface and local library/progress; browsing and
streaming require a connection.

An installed update waits for an explicit Update action. Notices are hidden
during playback, including native fullscreen and PiP. If playback starts while
activation is completing, reload is deferred and offered later. Worker
activation removes only application-owned old caches (including legacy `fleet-*` caches), retaining a previously usable
shell when a new shell fetch failed. It does not clear library/progress data.

When changing cached PWA assets, bump `VERSION` in `pwa/sw.js` and keep icon URL
versions consistent across `head.html`, the manifest and worker precache list.
The current icon URLs use `?v=4`. Icons can be regenerated on macOS with:

```sh
swift runners/web/pwa/generate-icons.swift
```

## Verification

Python 3 and Node.js 18 or newer are sufficient for the build and focused tests:

```sh
node --check runners/web/core.js
node --check runners/web/versions.js
node --check runners/web/player.js
node --check runners/web/pwa/client.js
node --check runners/web/pwa/sw.js
node runners/web/tests/player.test.cjs
node --test runners/web/tests/versions.test.cjs runners/web/tests/pwa.test.cjs
python3 runners/web/build.py --check
```

Before release, also exercise catalog/search navigation, empty and populated
Library, source ordering and copy/download actions, episode sheets with keyboard
focus, playback start/seek/resume/back, update deferral during playback, and
offline recovery. Check desktop and narrow mobile widths for overflow. Run the
Apple-device checks above separately; emulation is not a substitute.

The November 13 refinement was also opened in Safari on an iPhone 17 Pro / iOS 27 CoreSimulator. Native HLS entered Apple’s fullscreen player successfully. With the simulated device in portrait, the system player stayed portrait with widescreen video; Safari did not enforce the requested landscape lock. Treat landscape orientation as browser/OS controlled, not a guaranteed site action.
