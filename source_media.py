"""Authenticated, bounded, seekable media input without exposing credentials to ffmpeg.

The loopback response may be open-ended, but every upstream GET is a closed byte
range. A small shared LRU reuses headers, indexes and GOP bytes across jobs. Each
job authenticates with the origin before it may read that cache.
"""
import collections
import contextlib
import http.client
import os
import re
import select
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BLOCK_SIZE = 512 * 1024
CACHE_BYTES = 128 * 2**20
CACHE_TTL = 600


class SourceError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if fp is not None:
            fp.close()
        raise SourceError("source redirects are not allowed")


class BlockCache:
    """Bounded byte LRU; concurrent readers share one fetch for a block."""
    def __init__(self, max_bytes=CACHE_BYTES, ttl=CACHE_TTL):
        self.max_bytes, self.ttl = max_bytes, ttl
        self.entries = collections.OrderedDict()
        self.pending = {}
        self.bytes = 0
        self.lock = threading.Lock()

    def get(self, key, load, cancelled):
        while True:
            if cancelled.is_set():
                raise SourceError("source request cancelled")
            with self.lock:
                now = time.monotonic()
                expired = [k for k, (at, _) in self.entries.items() if now - at >= self.ttl]
                for k in expired:
                    self.bytes -= len(self.entries.pop(k)[1])
                item = self.entries.get(key)
                if item is not None:
                    self.entries.move_to_end(key)
                    return item[1], True
                event = self.pending.get(key)
                owner = event is None
                if owner:
                    event = self.pending[key] = threading.Event()
            if not owner:
                event.wait(.1)
                continue
            try:
                data = load()
                if cancelled.is_set():
                    raise SourceError("source request cancelled")
                if len(data) <= self.max_bytes:
                    with self.lock:
                        while self.entries and self.bytes + len(data) > self.max_bytes:
                            _, (_, old) = self.entries.popitem(last=False)
                            self.bytes -= len(old)
                        self.entries[key] = (time.monotonic(), data)
                        self.bytes += len(data)
                return data, False
            finally:
                with self.lock:
                    self.pending.pop(key, None)
                    event.set()


CACHE = BlockCache()


class OpenedResponse:
    """Track active origin sockets so cancelling a job unblocks body reads."""
    def __init__(self, source, response):
        self.source, self.response = source, response

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        try:
            self.response.close()
        finally:
            with self.source.lock:
                self.source.active.discard(self)

    def interrupt(self):
        # close() alone can wait for BufferedReader's lock held by a blocked read.
        raw = getattr(getattr(self.response, 'fp', None), 'raw', None)
        sock = getattr(raw, '_sock', None)
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
        self.close()


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
        self.auth_lock = threading.Lock()
        self.active = set()
        self.bytes = 0
        self.requests = 0
        self.cache_hits = 0
        self.cached_bytes = 0
        self._size = None
        self._authorized = False
        self._version = None
        self.content_type = "application/octet-stream"

    def open(self, method="GET", byte_range=None):
        if self.stop.is_set():
            raise SourceError("source request cancelled")
        headers = dict(self.headers)
        if method == "GET":
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", byte_range or "")
            if not match or not 0 <= int(match[2]) - int(match[1]) < BLOCK_SIZE:
                raise SourceError("origin reads must use a bounded byte range")
            headers["Range"] = byte_range
        elif method != "HEAD" or byte_range is not None:
            raise SourceError("invalid source request")
        try:
            result = self.opener.open(urllib.request.Request(self.url, headers=headers, method=method), timeout=15)
        except urllib.error.HTTPError as error:
            error.close()
            raise SourceError(f"source returned HTTP {error.code}") from None
        except (urllib.error.URLError, OSError):
            raise SourceError("source connection failed") from None
        if result.status not in (200, 206):
            result.close()
            raise SourceError("unexpected source response")
        response = OpenedResponse(self, result)
        with self.lock:
            self.requests += 1
            self.active.add(response)
        if self.stop.is_set():
            response.interrupt()
            raise SourceError("source request cancelled")
        return response

    def authorize(self):
        """Authenticate each job before any shared cached bytes or metadata are used."""
        with self.auth_lock:
            if self.stop.is_set():
                raise SourceError("source request cancelled")
            if self._authorized:
                return
            with self.open(method="HEAD") as response:
                length = response.headers.get("Content-Length", "")
                if response.status != 200 or not length.isdigit() or not 0 < int(length) < 2**63:
                    raise SourceError("source length is unavailable")
                self._size = int(length)
                self._version = (self.cache_key, self._size,
                                 response.headers.get("ETag") or response.headers.get("Last-Modified"))
                self.content_type = response.headers.get("Content-Type") or "application/octet-stream"
            self._authorized = True

    def cancel(self):
        self.stop.set()
        with self.lock:
            active = list(self.active)
        for response in active:
            with contextlib.suppress(OSError, ValueError):
                response.interrupt()

    def received(self, count):
        with self.lock:
            self.bytes += count

    def _block(self, number):
        start, end = number * BLOCK_SIZE, min((number + 1) * BLOCK_SIZE, self._size) - 1
        length = end - start + 1

        def fetch():
            with self.open(byte_range=f"bytes={start}-{end}") as response:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                if (response.status != 206 or not match
                        or tuple(map(int, match.groups())) != (start, end, self._size)
                        or response.headers.get("Content-Length") != str(length)):
                    raise SourceError("source does not support consistent byte ranges")
                try:
                    data = response.read(length)
                except http.client.IncompleteRead as error:
                    self.received(len(error.partial))
                    raise SourceError("source read was interrupted") from None
                except (OSError, ValueError):
                    raise SourceError("source read was interrupted") from None
                self.received(len(data))
                if len(data) != length:
                    raise SourceError("source range was cut short")
                return data

        data, hit = CACHE.get((self._version, number), fetch, self.stop)
        if hit:
            with self.lock:
                self.cache_hits += 1
                self.cached_bytes += len(data)
        return data

    def read_range(self, start, length):
        if type(start) is not int or type(length) is not int or start < 0 or not 0 < length <= 32 * 2**20:
            raise SourceError("invalid source read")
        self.authorize()
        end = min(start + length, self._size)
        if start >= end:
            return b""
        chunks = []
        for number in range(start // BLOCK_SIZE, (end - 1) // BLOCK_SIZE + 1):
            data = self._block(number)
            offset = number * BLOCK_SIZE
            chunks.append(data[max(0, start - offset):min(len(data), end - offset)])
        return b"".join(chunks)

    def size(self):
        self.authorize()
        return self._size


def peer_closed(conn):
    try:
        return bool(select.select([conn], [], [], 0)[0]) and not conn.recv(1, socket.MSG_PEEK)
    except OSError:
        return True


class Relay:
    def __init__(self, source):
        self.source = source
        self.server = None
        self.thread = None

    def __enter__(self):
        source = self.source
        source.authorize()

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
                    size = source.size()
                    start, end = 0, size - 1
                    requested = self.headers.get("Range")
                    if requested:
                        match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                        if not match or not any(match.groups()):
                            raise ValueError("invalid range")
                        first, last = match.groups()
                        if first:
                            start = int(first)
                            end = min(int(last), end) if last else end
                        elif int(last) > 0:
                            start = max(0, size - int(last))
                        else:
                            raise ValueError("invalid range")
                        if start > end or start >= size:
                            raise ValueError("range outside source")
                    self.send_response(206 if requested else 200)
                    self.send_header("Content-Type", source.content_type)
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "private, no-store")
                    if requested:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                    self.close_connection = True
                    if method == "HEAD":
                        return
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
                    position = start
                    while position <= end and not source.stop.is_set() and not peer_closed(self.connection):
                        # Fetch one block only after the preceding block was consumed
                        # by the loopback socket. No origin prefetch or open-ended GET.
                        amount = min(end - position + 1, BLOCK_SIZE - position % BLOCK_SIZE)
                        chunk = source.read_range(position, amount)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        position += len(chunk)
                except SourceError:
                    if not started:
                        with contextlib.suppress(OSError):
                            self.send_error(502, "media source unavailable")
                except ValueError:
                    if not started:
                        with contextlib.suppress(OSError):
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{source._size or 0}")
                            self.send_header("Content-Length", "0")
                            self.send_header("Connection", "close")
                            self.end_headers()
                except OSError:
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
        self.source.cancel()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
