# tor-mac

A transcode service on GitHub's Apple Silicon runners (macOS 15, M1): 4K HDR → 1080p H.264 SDR on the Mac's media
engine (VideoToolbox), for the fleet's tor app (Fleet Browser). Each Mac is reachable at its slot's address
(`https://mac-1.billybishop4-workers.xyz` … `mac-4`), so a caller can spread its work over them, and all of them at
`https://mac.billybishop4-workers.xyz`, which reaches whichever are up (Cloudflare picks the one nearest the caller).

- `mac.py`: the service. `POST /convert?t=&l=&s=&a=&h=&w=&p=` with a Matroska header + one piece's clusters as the body
  (and the `X-Mac-Key` header) answers with that piece as MPEG-TS (`p=0`: a player waits on it, it may take a place past
  `MAX_JOBS`; a caller that hangs up has its conversion ended); `GET /healthz`. On SIGTERM it drains: no new pieces
  (503 "draining", `"draining": true` in `/healthz`), those under way finished, then it stops.
- `.github/workflows/macs.yml`: keeps 4 Macs serving; each hands over to its replacement before the 6-hour limit,
  and a keeper (every 30 minutes) starts any that isn't running.

Secrets: `MAC_KEY` (shared with the tor app), `TUNNEL_TOKEN` (the shared tunnel's connector token), `TUNNEL_TOKEN_1` … `_4` (each slot's).
