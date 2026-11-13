"""Authenticated, seekable media input for ffmpeg without exposing credentials to it.

Only the configured source domain and the source-serving route are reachable. The
loopback relay deliberately refuses upstream redirects; ffmpeg sees no origin URL
or bearer token. A relay lives for one probe/conversion and closes with that job.
"""
import contextlib
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SourceError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceError("source redirects are not allowed")


class Source:
    def __init__(self, value):
        if not isinstance(value, dict):
            raise SourceError("source is required")
        url = value.get("url")
        if not isinstance(url, str) or len(url) > 4096:
            raise SourceError("invalid source URL")
        try:
            parsed = urllib.parse.urlsplit(url)
            port = parsed.port
        except ValueError:
            raise SourceError("invalid source URL") from None
        suffix = os.environ.get("SOURCE_HOST_SUFFIX", "billybishop4-workers.xyz").lower().strip(".")
        host = (parsed.hostname or "").lower()
        allowed = bool(suffix) and (host == suffix or host.endswith("." + suffix))
        if (parsed.scheme != "https" or not allowed or port not in (None, 443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not re.fullmatch(r"/mac-source/[A-Za-z0-9_.~-]{1,256}", parsed.path)):
            raise SourceError("source must use the configured HTTPS media route")
        headers = value.get("headers", {})
        if (not isinstance(headers, dict) or len(headers) != 1
                or set(k.lower() for k in headers) != {"authorization"}):
            raise SourceError("source needs only an Authorization header")
        auth = next(iter(headers.values()))
        if (not isinstance(auth, str) or not auth.startswith("Bearer ")
                or not 8 <= len(auth) <= 4096 or not auth.isascii()
                or any(ord(c) < 32 or ord(c) == 127 for c in auth)):
            raise SourceError("invalid source authorization")
        stable = value.get("id")
        if not isinstance(stable, str) or not 1 <= len(stable) <= 256:
            raise SourceError("source needs a stable id")
        self.url = url
        self.headers = {"Authorization": auth, "User-Agent": "tor-mac/source-v1", "Accept-Encoding": "identity"}
        self.cache_key = (host, parsed.path, stable)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.bytes = 0
        self.requests = 0
        self._size = None

    def open(self, method="GET", byte_range=None):
        if self.stop.is_set():
            raise SourceError("source request cancelled")
        headers = dict(self.headers)
        if byte_range:
            if not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", byte_range):
                raise SourceError("invalid byte range")
            headers["Range"] = byte_range
        try:
            result = self.opener.open(urllib.request.Request(self.url, headers=headers, method=method), timeout=15)
        except urllib.error.HTTPError as error:
            raise SourceError(f"source returned HTTP {error.code}") from None
        except (urllib.error.URLError, OSError):
            raise SourceError("source connection failed") from None
        if result.status not in (200, 206):
            result.close()
            raise SourceError("unexpected source response")
        with self.lock:
            self.requests += 1
            content_range = result.headers.get("Content-Range", "")
            match = re.fullmatch(r"bytes \d+-\d+/(\d+)", content_range)
            length = result.headers.get("Content-Length", "")
            if match:
                self._size = int(match.group(1))
            elif result.status == 200 and length.isdigit():
                self._size = int(length)
        return result

    def received(self, count):
        with self.lock:
            self.bytes += count

    def read_range(self, start, length):
        if start < 0 or not 0 < length <= 32 * 2**20:
            raise SourceError("invalid source read")
        with self.open(byte_range=f"bytes={start}-{start + length - 1}") as response:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if response.status != 206 or not match or int(match.group(1)) != start:
                raise SourceError("source does not support byte ranges")
            data = response.read(length)
            self.received(len(data))
            return data

    def size(self):
        if self._size is None:
            with self.open(method="HEAD"):
                pass
        if self._size is None or self._size <= 0:
            raise SourceError("source length is unavailable")
        return self._size


class Relay:
    def __init__(self, source):
        self.source = source
        self.server = None
        self.thread = None

    def __enter__(self):
        source = self.source

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            timeout = 20

            def log_message(self, *_):
                pass

            def do_HEAD(self):
                self.serve("HEAD")

            def do_GET(self):
                self.serve("GET")

            def serve(self, method):
                if self.path != "/source":
                    self.send_error(404)
                    return
                started = False
                try:
                    with source.open(method, self.headers.get("Range")) as response:
                        self.send_response(response.status)
                        for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                            if response.headers.get(name):
                                self.send_header(name, response.headers[name])
                        self.send_header("Connection", "close")
                        self.end_headers()
                        started = True
                        self.close_connection = True
                        if method == "HEAD":
                            return
                        while not source.stop.is_set():
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            source.received(len(chunk))
                            self.wfile.write(chunk)
                except (SourceError, OSError, ValueError):
                    if not started:
                        with contextlib.suppress(OSError):
                            self.send_error(502, "media source unavailable")
                finally:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/source"

    def __exit__(self, *_):
        self.source.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
