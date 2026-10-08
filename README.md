# tor-mac

A transcode service on GitHub's Apple Silicon runners (macOS 15, M1): 4K HDR → 1080p H.264 SDR on the Mac's media
engine (VideoToolbox), for the fleet's tor app (Fleet Browser). Every running Mac is a connector of one Cloudflare
tunnel, so `https://mac.billybishop4-workers.xyz` reaches whichever are up.

- `mac.py`: the service. `POST /convert?t=&l=&s=&a=&h=&w=` with a Matroska header + one piece's clusters as the body
  (and the `X-Mac-Key` header) answers with that piece as MPEG-TS; `GET /healthz`.
- `.github/workflows/macs.yml`: keeps 4 Macs serving; each hands over to its replacement before the 6-hour limit,
  and a keeper (every 30 minutes) starts any that isn't running.

Secrets: `MAC_KEY` (shared with the tor app), `TUNNEL_TOKEN` (the tunnel's connector token).
