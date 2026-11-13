#!/usr/bin/env python3
"""Local UI preview; only read requests are proxied to the existing Tor app."""
import argparse
import mimetypes
from pathlib import Path
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import build


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_HEAD(self):
        self.do_GET()

    def send_bytes(self, data, content_type, status=200, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path in ('/', '/watch'):
                return self.send_bytes(build.page().encode(), 'text/html; charset=utf-8')
            name = path.removeprefix('/pwa/').lstrip('/')
            if name in build.ASSETS:
                kind = {'sw.js': 'application/javascript', 'manifest.webmanifest': 'application/manifest+json'}.get(name, 'image/png')
                return self.send_bytes((build.ROOT / 'pwa' / name).read_bytes(), kind)
            if (path.startswith('/test-media/') or path.startswith('/hls/ui-test/')) and self.server.fixtures:
                name = path.removeprefix('/test-media/').removeprefix('/hls/ui-test/')
                if '/' in name or not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
                    return self.send_bytes(b'Not found', 'text/plain', 404)
                target = self.server.fixtures / name
                if not target.is_file():
                    return self.send_bytes(b'Not found', 'text/plain', 404)
                data = target.read_bytes()
                kind = {'m3u8': 'application/vnd.apple.mpegurl', 'ts': 'video/mp2t'}.get(target.suffix[1:], mimetypes.guess_type(name)[0] or 'application/octet-stream')
                requested = self.headers.get('Range')
                if requested:
                    match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
                    if not match or int(match[1]) >= len(data):
                        return self.send_bytes(b'', kind, 416, {'Content-Range': f'bytes */{len(data)}'})
                    start = int(match[1]); end = min(len(data) - 1, int(match[2]) if match[2] else len(data) - 1)
                    return self.send_bytes(data[start:end + 1], kind, 206,
                                           {'Accept-Ranges': 'bytes', 'Content-Range': f'bytes {start}-{end}/{len(data)}'})
                return self.send_bytes(data, kind, headers={'Accept-Ranges': 'bytes'})
            req = urllib.request.Request(self.server.upstream + self.path,
                                         headers={'User-Agent': 'Tor-UI-Preview/1.0'}, method=self.command)
            try:
                response = urllib.request.urlopen(req, timeout=25)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return self.send_bytes(response.read(), response.headers.get('Content-Type', 'application/octet-stream'), response.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send_bytes(b'Preview upstream unavailable', 'text/plain', 502)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--upstream', default='https://frame.billybishop4-workers.xyz')
    parser.add_argument('--fixtures', type=Path)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.upstream = args.upstream.rstrip('/')
    server.fixtures = args.fixtures
    print(f'Preview: http://127.0.0.1:{args.port}/watch', flush=True)
    server.serve_forever()
