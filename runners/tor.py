






















import base64
import collections
import concurrent.futures
import contextlib
import ctypes
import mimetypes
import select
import gzip
import hashlib
import hmac
import itertools
import http.client
import json
import math
import os
import queue
import random
import re
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import libtorrent as lt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:

    ctypes.CDLL(None).mallopt(-3, 2**20)
except Exception:
    pass

APP = os.environ.get("FLEET_APP", "tor")
REPLICA = int(os.environ.get("FLEET_REPLICA", "1"))
DOMAIN = os.environ.get("FLEET_DOMAIN", "")
PAGE_PORT = int(os.environ.get("PAGE_PORT", "8080"))
TS_URL = os.environ.get("TS_URL", "http://127.0.0.1:8090")
TS_PEER_PORT = int(os.environ.get("TS_PEER_PORT", "42069"))
HELPER_PORT = int(os.environ.get("HELPER_PORT", "6881"))
CACHE = os.environ.get("CACHE_DIR", "/cache")
CACHE_BYTES = int(float(os.environ.get("CACHE_GB", "6")) * 2**30)
CHUNK = int(float(os.environ.get("CHUNK_MB", "64")) * 2**20)
TS_CACHE = int(float(os.environ.get("TS_CACHE_GB", "2")) * 2**30)
SEG = 4 * 2**20
FIRST_SEG = 2**20
AHEAD = 6
HOT = 512 * 2**20
WARM_TTL = 10 * 60
PROBE, PROBE_WANT, PROBE_WAIT = 8, 5, 30

PROBED_TTL = 10 * 60
IDLE = 180
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")
KV_PASSWORD = os.environ.get("TOR_KV_PASSWORD", "")
SEAL = hashlib.sha256(os.environ.get("TOR_SEAL_KEY", "").encode()).digest()
CHUNK_KEY = hmac.new(SEAL, b"chunks", "sha256").hexdigest()
KV_PAUSE = 5
KEEP_SYNC = 2
KEEP_BACKUPS = 3
TOMB_TTL = 600
PEERS = [p for p in os.environ.get("PEERS", "").split(",") if p]
TORRENTIO = os.environ.get("TORRENTIO", "https://torrentio.strem.fun")
SCRAPERS = [("tracker.opentrackr.org", 1337), ("open.stealth.si", 80), ("tracker.torrent.eu.org", 451), ("exodus.desync.com", 6969)]

CODE = hashlib.sha256(open(__file__, "rb").read()).hexdigest()[:12]
BOOT = os.urandom(6).hex()
JOB_TTL = 20 * 60
LEASE = 30
NEW = 999_999_999
JOB, META, HUNT, PLAY = "tor.job.", "tor.meta.", "tor.hunt.", "tor.play."
COPY = f"{APP}.copy."
SCAN_COPIES = 24
HUNT_TTL = 6 * 60
WAIT, WAITS = 50, 6


TRACKER_LIST = os.environ.get("TRACKER_LIST", "https://cdn.jsdelivr.net/gh/ngosang/trackerslist@master/trackers_all.txt")
UA = "fleet-tor/1"
GW_HOSTS = ("cinemeta-catalogs.strem.io", "v3-cinemeta.strem.io", "images.metahub.space", "live.metahub.space", "episodes.metahub.space",
            "image.tmdb.org", "m.media-amazon.com", "cdn.jsdelivr.net")
GW_RAM = 192 << 20
TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.skynetcloud.site:6969/announce",
    "udp://tracker.qu.ax:6969/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "udp://tracker.tryhackx.org:6969/announce",
    "udp://tracker.nyaa.vc:6969/announce",
    "udp://tracker.corpscorp.online:80/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.theoks.net:6969/announce",
    "udp://tracker-udp.gbitt.info:80/announce",
    "udp://tracker.ducks.party:1984/announce",
    "udp://tracker.gmi.gd:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://tracker2.dler.org:80/announce",
    "http://tracker.dler.com:6969/announce",
    "udp://retracker01-msk-virt.corbina.net:80/announce",
]
ALL_TRACKERS = list(TRACKERS)

recent = collections.deque(maxlen=400)


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    recent.append(line)
    print(line, flush=True)




POOL_IDLE = 50


def readable(sock, wait=0):


    p = select.poll()
    p.register(sock, select.POLLIN)
    return bool(p.poll(wait * 1000))


class Pool:




    def __init__(self):
        self.idle = collections.defaultdict(list)
        self.lock = threading.Lock()

    def request(self, method, url, body=None, headers=None, timeout=10, hold=None):




        u = urllib.parse.urlsplit(url)
        key, path = (u.scheme, u.netloc), (u.path or "/") + (f"?{u.query}" if u.query else "")
        headers = {"User-Agent": UA, **(headers or {})}
        for attempt in (0, 1):
            c, now = None, time.time()
            with self.lock:
                while not attempt and c is None and self.idle[key]:
                    c, since = self.idle[key].pop()
                    if now - since > POOL_IDLE or c.sock is None or readable(c.sock):

                        c.close()
                        c = None
            fresh = c is None
            if fresh:
                c = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=timeout)
            elif c.sock:
                c.sock.settimeout(timeout)
            try:
                if hold is not None:
                    if c.sock is None:
                        c.connect()
                    hold["conn"] = c
                    if hold.get("ended"):
                        raise ConnectionAbortedError("ended")
                if hold is not None:
                    hold["sending"] = True
                c.request(method, path, body=body, headers=headers)
                if hold is not None:
                    hold["sent"] = time.time()
                    if hold.get("ended"):
                        end_call(hold)
                r = c.getresponse()
                if hold is not None:
                    hold["headers"] = time.time()
                data = r.read()
                if hold is not None:
                    hold["read"] = time.time()
            except (http.client.HTTPException, OSError) as err:
                c.close()
                if fresh or attempt or (hold is not None and (hold.get("ended") or isinstance(err, TimeoutError))):
                    raise
                continue
            if r.will_close:
                c.close()
            else:
                with self.lock:
                    if hold is not None:
                        hold["conn"] = None
                    self.idle[key].append((c, time.time()))
            return r.status, r.headers, data


conns = Pool()


def end_call(call):




    call["ended"] = True
    with conns.lock:
        c = call.get("conn")
        if c is None or (call.get("sending") and call.get("sent") is None):
            return
        try:
            if c.sock is not None:
                socket.socket.shutdown(c.sock, socket.SHUT_RDWR)
        except OSError:
            pass


def url_of(k):

    for p in PEERS:
        r, addr = p.split("@")
        if int(r) == k:
            return f"http://{addr}"
    return f"https://{APP}-{k}.{DOMAIN}"


live = {"at": 0, "copies": {}, "scanned": {}, "scanning": False, "told": {}}


def hint(k):

    live["told"][k] = time.time()
    live["scanned"][k] = {"url": url_of(k)}


def copies():



    if kv.down:
        return live["scanned"] or live["copies"] or {REPLICA: {"url": url_of(REPLICA)}}
    if time.time() - live["at"] > 5:
        live["at"] = time.time()
        try:
            now = time.time() * 1000
            up = {int(it["key"][len(COPY):]) for it in kv.listing() if it["key"].startswith(COPY)
                  and it["key"][len(COPY):].isdigit() and now - (it.get("modified") or 0) < 45_000}
            live["copies"] = {k: {"url": url_of(k)} for k in sorted(up | {REPLICA})}
        except Exception as err:
            if not kv.down:
                log(f"copies: {err}")
    return live["copies"] or live["scanned"] or {REPLICA: {"url": url_of(REPLICA)}}


def scan_copies(wait=False):





    while True:
        with hls_lock:
            if not live["scanning"]:
                live["scanning"] = True
                break
        if not wait:
            return
        time.sleep(0.2)
    began = time.time()
    try:
        top = max([SCAN_COPIES, REPLICA] + list(live["scanned"])) + 4
        found = {}

        def ask(k):
            try:
                st, _, body = conns.request("GET", f"{url_of(k)}/healthz", timeout=4)
                ans = json.loads(body) if st == 200 else {}
                if ans.get("ok") and ans.get("app", APP) == APP and ans.get("replica", k) == k:
                    found[k] = {"url": url_of(k)}
            except Exception:
                pass
        threads = [threading.Thread(target=ask, args=(k,), daemon=True) for k in range(1, top + 1) if k != REPLICA]
        for t in threads:
            t.start()
        for t in threads:
            t.join(6)
        found[REPLICA] = {"url": url_of(REPLICA)}
        for k, t in list(live["told"].items()):
            if t > began:
                found[k] = {"url": url_of(k)}
        found = dict(sorted(found.items()))
        if set(found) != set(live["scanned"]):
            log(f"copies: {len(found)} found at their addresses: {sorted(found)}")
        live["scanned"] = dict(sorted(found.items()))
    finally:
        live["scanning"] = False


def heartbeat_loop():



    warmed = 0
    while True:
        if not kv.down:
            try:
                kv.put(COPY + str(REPLICA), seal(json.dumps({"replica": REPLICA, "id": BOOT, "t": time.time()}).encode()))
            except Exception as err:
                if not kv.down:
                    log(f"heartbeat: {err}")
        if time.time() - warmed > 30:
            warmed = time.time()
            threading.Thread(target=scan_copies, daemon=True).start()
        time.sleep(10)




class Store:







    def __init__(self):
        self.items = {}
        self.seq = 0
        self.at = 0
        self.lineage = BOOT
        self.vers = 0
        self.purged = 0
        self.lock = threading.Lock()
        self.serving = False
        self.ready = threading.Event()
        self.taking = False
        self.gen = 0
        self.retired = False
        self.keeper_id = None

    def changed(self, key, data, version):
        self.seq += 1
        self.at = time.time()
        self.items[key] = [data, version, int(self.at * 1000), self.seq]

    def answer(self, method, key, body=None, version=None):

        with self.lock:
            if not key:
                if method != "GET":
                    return 405, b"", {}
                return 200, json.dumps([{"key": k, "size": len(v[0]), "version": v[1], "modified": v[2]} for k, v in self.items.items()
                                        if v[0] is not None]).encode(), {}
            got = self.items.get(key)
            if got is not None and got[0] is None:
                got = None
            if method == "GET":
                return (404, b"", {}) if got is None else (200, got[0], {"X-Version": str(got[1])})
            if version is not None and (got is None if version != NEW else got is not None):
                return 409, b"", {}
            if version is not None and version != NEW and got[1] != version:
                return 409, b"", {}
            if method == "PUT":
                self.vers += 1
                self.changed(key, body or b"", self.vers)
                return 200, json.dumps({"version": self.vers}).encode(), {}
            if method == "DELETE":
                if got is not None:
                    self.changed(key, None, got[1])
                return 200, b"{}", {}
            return 405, b"", {}

    def state(self):
        return {"lineage": self.lineage, "seq": self.seq, "at": self.at, "serving": self.serving, "replica": REPLICA,
                "keys": sum(v[0] is not None for v in self.items.values())}

    def since(self, lineage, seq):

        with self.lock:
            full = lineage != self.lineage or seq < self.purged or seq > self.seq
            items = {k: [None if v[0] is None else base64.b64encode(v[0]).decode(), v[1], v[2], v[3]] for k, v in self.items.items()
                     if full or v[3] > seq}
            return {"lineage": self.lineage, "seq": self.seq, "at": self.at, "full": full, "from": seq, "items": items}

    def apply(self, got):

        with self.lock:
            if not got["full"] and (got["lineage"] != self.lineage or got["from"] != self.seq):
                return False
            if got["full"]:
                self.items = {}
            for k, v in got["items"].items():
                self.items[k] = [None if v[0] is None else base64.b64decode(v[0]), v[1], v[2], v[3]]
            self.lineage, self.seq, self.at = got["lineage"], got["seq"], got["at"]
            self.vers = max([self.vers] + [v[1] for v in self.items.values()])
            return True

    def purge(self):

        with self.lock:
            old = (time.time() - TOMB_TTL) * 1000
            for k in [k for k, v in self.items.items() if v[0] is None and v[2] < old]:
                self.purged = max(self.purged, self.items.pop(k)[3])


store = Store()


def keeper():

    return min(live["scanned"]) if live["scanned"] else None


def peer_call(k, method, path, body=None, timeout=5):

    st, _, data = conns.request(method, f"{url_of(k)}{path}", gzip.compress(json.dumps(body).encode()) if body is not None else None,
                                {"X-Tor-Key": CHUNK_KEY}, timeout=timeout)
    if st != 200:
        raise RuntimeError(f"HTTP {st} from copy {k}")
    return json.loads(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)


def step_down(k):

    if store.serving:
        log(f"store: copy {k} keeps it from now on")
    store.serving = False
    store.gen += 1
    store.ready.clear()


def take_over():


    with store.lock:
        if store.taking:
            return
        store.taking = True
    try:
        gen = store.gen
        scan_copies(wait=True)
        if keeper() != REPLICA:
            return
        others = [k for k in live["scanned"] if k != REPLICA]
        states = {}

        def tell(k):
            try:
                states[k] = peer_call(k, "POST", "/kv-keeper", {"keeper": REPLICA, "id": BOOT})
            except Exception:
                pass
        threads = [threading.Thread(target=tell, args=(k,), daemon=True) for k in others]
        for t in threads:
            t.start()
        for t in threads:
            t.join(6)
        lower = [k for k, s in states.items() if s.get("replica") == k and k < REPLICA]
        if lower:
            for k in lower:
                hint(k)
            return
        best = max(states.items(), key=lambda x: (x[1].get("at", 0), x[1].get("seq", 0)), default=None)
        if best and best[1].get("at", 0) > store.at and best[1].get("keys", 0):
            got = peer_call(best[0], "GET", "/kv-sync?lineage=&seq=0", timeout=20)
            store.apply(got)
            log(f"store: taken from copy {best[0]}: {len(got['items'])} keys, change {got['seq']}")
        if keeper() == REPLICA and store.gen == gen:
            with store.lock:
                store.lineage = os.urandom(6).hex()
            store.serving = True
            store.ready.set()
            log(f"store: kept by this copy ({store.state()['keys']} keys)")
    except Exception as err:
        log(f"store: taking it over: {err.__class__.__name__}: {err}")
    finally:
        store.taking = False


def keep_loop():


    acked = {}
    n = 0
    while True:
        time.sleep(KEEP_SYNC)
        n += 1
        k = keeper()
        if k is None:
            continue
        if k != REPLICA:
            if store.serving:
                step_down(k)
            continue
        if not store.serving:
            if not store.retired:
                threading.Thread(target=take_over, daemon=True).start()
            continue
        if n % 30 == 0:
            store.purge()
        ups = sorted(live["scanned"])
        for b in [x for x in ups if x > REPLICA][:KEEP_BACKUPS]:
            lineage, seq = acked.get(b, ("", -1))
            if lineage == store.lineage and seq == store.seq:
                continue

            def send(b=b, lineage=lineage, seq=seq):
                try:
                    got = peer_call(b, "POST", "/kv-sync", dict(store.since(lineage, seq), sender=REPLICA, id=BOOT), timeout=15)
                    if got.get("retire"):
                        step_down(REPLICA)
                        store.retired = True
                        return
                    acked[b] = (got["lineage"], got["seq"]) if got.get("ok") else ("", -1)
                    if got.get("keeper") is not None and got["keeper"] < REPLICA:
                        hint(got["keeper"])
                except Exception:
                    acked.pop(b, None)
            threading.Thread(target=send, daemon=True).start()


class KV:




    def __init__(self):
        self.listed = (0, [])
        self.down = False
        self.lock = threading.Lock()

    def call(self, method, key, body=None, version=None, probe=False):
        if self.down and not probe:
            raise RuntimeError("the store: down lately (not asked)")
        last = None
        for _ in range(2):
            k = keeper()
            if k is None:
                raise RuntimeError("the store: no copy found yet")
            if k == REPLICA and not store.retired:
                if store.ready.wait(5) and store.serving:
                    st, data, h = store.answer(method, key, body, version)
                    self.down = False
                    return st, data, h
                last = RuntimeError("this copy is taking the store over")
                break
            headers = {"x-password": KV_PASSWORD}
            if version is not None:
                headers["If-Match"] = str(version)
            try:
                st, h, data = conns.request(method, f"{url_of(k)}/kv" + (f"/{key}" if key else ""), body, headers, timeout=5)
            except Exception as e:
                last = e
                live["scanned"].pop(k, None)
                threading.Thread(target=scan_copies, daemon=True).start()
                continue
            if st == 421:
                other = json.loads(data).get("keeper")
                if other is not None and other != k:
                    hint(other)
                last = RuntimeError(f"copy {k} doesn't keep the store")
                continue
            if st >= 500 or st in (401, 429):
                last = RuntimeError(f"HTTP {st} from copy {k}")
                continue
            if self.down:
                log("the store answers again")
            self.down = False
            return st, data, h
        with self.lock:
            if not self.down:
                self.down = True
                log(f"the store: {last}: taken for down (calls fail at once; tried again every {KV_PAUSE} s)")
                threading.Thread(target=self.watch, daemon=True).start()
        raise RuntimeError(f"the store: {last}")

    def watch(self):

        while self.down:
            time.sleep(KV_PAUSE)
            try:
                self.call("GET", None, probe=True)
            except Exception:
                pass

    def get(self, key):
        st, data, h = self.call("GET", key)
        if st == 404:
            return None
        if st != 200:
            raise RuntimeError(f"the store: reading {key}: HTTP {st}")
        return data, int(h.get("X-Version") or 0)

    def put(self, key, data, version=None):

        st, body, _ = self.call("PUT", key, data, version)
        if st == 409:
            return None
        if st != 200:
            raise RuntimeError(f"the store: writing {key}: HTTP {st} {body[:200]!r}")
        return json.loads(body)["version"]

    def delete(self, key, version=None):

        self.call("DELETE", key, version=version)

    def listing(self):

        if time.time() - self.listed[0] > 2:
            st, data, _ = self.call("GET", None)
            if st != 200:
                raise RuntimeError(f"the store: listing: HTTP {st}")
            self.listed = (time.time(), json.loads(data))
        return self.listed[1]

    def keys(self, prefix):
        return [k["key"] for k in self.listing() if k["key"].startswith(prefix)]


kv = KV()


def seal(data):
    n = os.urandom(12)
    return n + AESGCM(SEAL).encrypt(n, data, None)


def unseal(data):
    return AESGCM(SEAL).decrypt(data[:12], data[12:], None)


def hid(ih):
    return hmac.new(SEAL, ih.encode(), "sha256").hexdigest()[:24]


def read_job(ih):
    got = kv.get(JOB + hid(ih))
    return (json.loads(unseal(got[0])), got[1]) if got else (None, None)




ses = lt.session({
    "listen_interfaces": f"0.0.0.0:{HELPER_PORT}",
    "enable_dht": True, "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False,
    "connections_limit": 2000, "unchoke_slots_limit": -1,
    "announce_to_all_trackers": True, "announce_to_all_tiers": True,
    "active_downloads": -1, "active_seeds": -1, "active_limit": -1,
    "dht_bootstrap_nodes": "dht.libtorrent.org:25401,router.bittorrent.com:6881,router.utorrent.com:6881,dht.transmissionbt.com:6881",
    "alert_mask": 0,
})
DELETE = getattr(getattr(lt, "options_t", None), "delete_files", None) or lt.session.delete_files
lt_lock = threading.Lock()
handles = {}


def magnet(ih):
    return f"magnet:?xt=urn:btih:{ih}" + "".join(f"&tr={urllib.parse.quote(t, safe='')}" for t in TRACKERS)


class NoMetadata(RuntimeError):
    pass


def torrent_file(ti):

    return b"d4:info" + bytes(ti.info_section()) + b"e"


def stored_meta(ih):

    got = kv.get(META + hid(ih))
    return unseal(got[0]) if got else None


def store_meta(ih, ti):


    try:
        body = seal(torrent_file(ti))
        return len(body) < 880_000 and kv.put(META + hid(ih), body, version=NEW) is not None
    except Exception as err:
        log(f"{ih[:8]}: metadata not stored ({err})")
        return False


HAVE = ".have"


def save_have(ih, e):



    st = e["h"].status(lt.status_flags_t.query_pieces)
    if not st.has_metadata or not os.path.isdir(f"{CACHE}/{ih}"):
        return
    bits = bytearray((len(st.pieces) + 7) // 8)
    for i, v in enumerate(st.pieces):
        if v:
            bits[i >> 3] |= 1 << (i & 7)
    with open(f"{CACHE}/{ih}/{HAVE}.new", "wb") as fh:
        fh.write(bytes(bits))
    os.replace(f"{CACHE}/{ih}/{HAVE}.new", f"{CACHE}/{ih}/{HAVE}")


def load_have(ih, n):

    try:
        bits = open(f"{CACHE}/{ih}/{HAVE}", "rb").read()
    except OSError:
        return None
    return [bool(bits[i >> 3] >> (i & 7) & 1) for i in range(n)] if len(bits) == (n + 7) // 8 else None


def add_torrent(ih, role, file_idx=None):



    e = handles.get(ih)
    if not e:
        atp = None
        try:
            torrent = stored_meta(ih)
            atp = torrent and lt.load_torrent_buffer(torrent)
        except Exception as err:
            log(f"{ih[:8]}: the stored metadata didn't load ({err}); asking the swarm")
        if atp:
            have = load_have(ih, atp.ti.num_pieces())
            if have:
                atp.have_pieces = have
            if file_idx is not None:
                atp.file_priorities = [1 if i == file_idx else 0 for i in range(atp.ti.num_files())]
        atp = atp or lt.parse_magnet_uri(magnet(ih))
        atp.trackers = list(dict.fromkeys(list(atp.trackers) + ALL_TRACKERS))
        atp.save_path = f"{CACHE}/{ih}"
        atp.flags |= lt.torrent_flags.default_dont_download
        with lt_lock:
            e = handles.get(ih)
            if not e:
                e = handles[ih] = {"h": ses.add_torrent(atp), "role": role, "since": time.time(), "wanted": time.time(),
                                   "plan": None, "looked": 0}
    if role == "helper":
        e["role"] = "helper"
    return e


def has_metadata(ih, e):


    h = e["h"]
    if h.status().has_metadata:
        return True
    if time.time() - e["looked"] > 10:
        e["looked"] = time.time()
        try:
            torrent = stored_meta(ih)
            if torrent:
                h.set_metadata(bytes(lt.load_torrent_buffer(torrent).ti.info_section()))
        except Exception as err:
            log(f"{ih[:8]}: setting the stored metadata: {err}")
    return False


def metadata(ih, f):



    torrent = stored_meta(ih)
    if torrent:
        return lt.load_torrent_buffer(torrent).ti
    e = add_torrent(ih, "meta")
    key, began, looked = HUNT + hid(ih), time.time(), time.time()
    try:
        kv.put(key, seal(json.dumps({"ih": ih, "t": began}).encode()))
    except Exception as err:
        log(f"{ih[:8]}: the hunt for its metadata not posted ({err}); looking alone")
    log(f"{ih[:8]}: asking the swarm for its metadata, with the fleet")
    try:
        while time.time() - began < HUNT_TTL and time.time() - f["last_play"] < 90:
            e["wanted"] = time.time()
            if e["h"].status().has_metadata:
                ti = e["h"].torrent_file()
                store_meta(ih, ti)
                log(f"{ih[:8]}: metadata in {time.time() - began:.0f} s")
                return ti
            if time.time() - looked > 2:
                looked = time.time()
                try:
                    torrent = stored_meta(ih)
                except Exception:
                    torrent = None
                if torrent:
                    log(f"{ih[:8]}: metadata in {time.time() - began:.0f} s, from another copy")
                    return lt.load_torrent_buffer(torrent).ti
            time.sleep(0.5)
    finally:
        try:
            kv.delete(key)
        except Exception:
            pass
    raise NoMetadata(f"no metadata from the swarm in {time.time() - began:.0f} s: no peers to be found")


def hunt(ih):

    e = add_torrent(ih, "meta")
    e["wanted"] = time.time()
    if e["h"].status().has_metadata and not e.get("stored"):
        e["stored"] = True
        if store_meta(ih, e["h"].torrent_file()):
            log(f"{ih[:8]}: found its metadata for the fleet")


def drop(ih):
    with lt_lock:
        e = handles.pop(ih, None)
    if e:
        ses.remove_torrent(e["h"], DELETE)
        shutil.rmtree(f"{CACHE}/{ih}", ignore_errors=True)
        log(f"{ih[:8]}: dropped")


def mine(job, c):



    w, last = warm_level(job.get("warm")), (job["last"] - job["first"]) // job["chunk"]
    if w == 1 and 0 < c < last or w == 2 and 3 < c < last:
        return False
    return REPLICA in chunk_owners(job, c)


def warm_level(w):


    return 2 if w is True else int(w or 0)


def plan_for(job, have, budget, free):



    first, last, cp, pl, head = job["first"], job["last"], job["chunk"], job["piece_length"], job["playhead"]
    chunks = []
    for c in range((last - first) // cp + 1):
        if not mine(job, c):
            continue
        a = first + c * cp
        b = min(last, a + cp - 1)
        dist = max(0, a - head) if b >= head else (last - first + 1) + (head - a)
        if a == first or b == last:
            dist = -1
        chunks.append((dist, a, b))
    chunks.sort()
    plan = []
    for i, (_, a, b) in enumerate(chunks):
        size = (b - a + 1) * pl
        new = sum(pl for k in range(a, b + 1) if not have[k])
        if size > budget or new > free:
            break
        budget -= size
        free -= new
        plan.append((a, b, 7 if i < 2 else 6 if i < 6 else 5 if i < 14 else 4 if i < 30 else 3 if i < 60 else 2 if i < 120 else 1))
    return plan


def fill(want, total):


    out, left = {}, total
    for i, (k, w) in enumerate(sorted(want.items(), key=lambda kw: kw[1])):
        out[k] = min(w, left // (len(want) - i))
        left -= out[k]
    return out


def owned(job, have=None):

    first, last, cp, pl = job["first"], job["last"], job["chunk"], job["piece_length"]
    size = missing = 0
    for c in range((last - first) // cp + 1):
        if mine(job, c):
            a, b = first + c * cp, min(last, first + c * cp + cp - 1)
            size += (b - a + 1) * pl
            missing += sum(pl for k in range(a, b + 1) if not have or not have[k])
    return size, missing


def pieces_of(ih):

    e = handles.get(ih)
    st = e and e["h"].status(lt.status_flags_t.query_pieces)
    return st.pieces if st and st.has_metadata else None


def played(job):
    return time.time() - job.get("played", job["t"]) < IDLE


def ram_shares(jobs, free):




    size, missing = {}, {}
    for job in jobs:
        size[job["ih"]], missing[job["ih"]] = owned(job, pieces_of(job["ih"]))
    out, room = {}, CACHE_BYTES
    for group in ([job["ih"] for job in jobs if played(job)], [job["ih"] for job in jobs if not played(job)]):
        budget = fill({ih: size[ih] for ih in group}, room)
        new = fill({ih: missing[ih] for ih in group}, free)
        out.update({ih: (budget[ih], new[ih]) for ih in group})
        room -= sum(budget.values())
        free -= sum(new.values())
    return out


def evict_idle(jobs, free):


    want = sum(owned(job, pieces_of(job["ih"]))[1] for job in jobs if played(job))
    idle = sorted((job.get("played", job["t"]), job["ih"]) for job in jobs if not played(job) and job["ih"] in handles)
    if want > free and idle:
        log(f"{idle[0][1][:8]}: idle, and the films being played need its RAM")
        drop(idle[0][1])


def help_with(job, budget, new):

    ih = job["ih"]
    e = add_torrent(ih, "helper", job["file"])
    e["wanted"] = time.time()
    h = e["h"]
    if not has_metadata(ih, e) or time.time() < e.get("urgent_until", 0):
        return
    plan = plan_for(job, h.status(lt.status_flags_t.query_pieces).pieces, budget, new)
    if plan != e["plan"]:
        ti = h.torrent_file()
        if e.get("file") != job["file"]:
            fprios = [0] * ti.num_files()
            fprios[job["file"]] = 1
            h.prioritize_files(fprios)
            e["file"] = job["file"]
        prios = [0] * ti.num_pieces()
        for a, b, p in plan:
            for i in range(a, b + 1):
                prios[i] = p
        h.prioritize_pieces(prios)
        if e["plan"] is None:
            log(f"{ih[:8]}: helping with {len(plan)} chunks ({sum(b - a + 1 for a, b, _ in plan) * job['piece_length'] / 1e9:.1f} GB) of {job['name']}")
        e["plan"] = plan


def sweep():

    for name in os.listdir(CACHE):
        if name not in handles and os.path.isdir(f"{CACHE}/{name}"):
            shutil.rmtree(f"{CACHE}/{name}", ignore_errors=True)
            log(f"{name[:8]}: swept out of the cache (no job wants it)")


def helper_loop():



    saved = swept = time.time()
    while True:
        try:
            wanted, jobs = set(), []
            for key in kv.keys("tor."):
                if not key.startswith((JOB, HUNT)):
                    continue
                got = kv.get(key)
                if not got:
                    continue
                try:
                    rec = json.loads(unseal(got[0]))
                except Exception:
                    continue
                ih, age = rec["ih"], time.time() - rec["t"]
                if key.startswith(HUNT):
                    if age > HUNT_TTL + 120:
                        kv.delete(key, got[1])
                        if not kv.get(JOB + hid(ih)):
                            kv.delete(META + hid(ih))
                    elif age < HUNT_TTL:
                        wanted.add(ih)
                        hunt(ih)
                    continue
                if age > JOB_TTL + 300:
                    kv.delete(key, got[1])
                    kv.delete(META + hid(ih))
                    kv.delete(PLAY + hid(ih))
                    log(f"{ih[:8]}: cleared {rec['name']}'s job: its front went {age / 60:.0f} minutes ago")
                elif age < JOB_TTL and REPLICA in rec["helpers"]:
                    jobs.append(rec)
            du = shutil.disk_usage(CACHE)
            free = max(0, du.free - 256 * 2**20)
            evict_idle(jobs, free)
            shares = ram_shares(jobs, free)
            for job in jobs:
                wanted.add(job["ih"])
                help_with(job, *shares[job["ih"]])
            for ih, e in list(handles.items()):
                if ih not in wanted and time.time() - e["wanted"] > 90:
                    drop(ih)
            if time.time() - saved > 30:
                saved = time.time()
                for ih, e in list(handles.items()):
                    if e["role"] == "helper":
                        save_have(ih, e)
            if time.time() - swept > 60:
                swept = time.time()
                sweep()
                sweep_readers()
        except Exception as err:
            if not kv.down:
                log(f"helper: {err.__class__.__name__}: {err}")
        time.sleep(3)


def chunk_bytes(ih, off, n, urgent=False):



    e = handles.get(ih)
    if not e:
        return None
    if not e["h"].status().has_metadata:
        if urgent:
            e["looked"] = 0
            has_metadata(ih, e)
        return None
    h = e["h"]
    ti = h.torrent_file()
    pl = ti.piece_length()
    pieces = range(off // pl, (off + n - 1) // pl + 1)
    missing = [i for i in pieces if not h.have_piece(i)]
    asked = e.setdefault("asked", {})
    for i in pieces:
        if i not in missing and i in asked:
            t, prio = asked.pop(i)
            if time.time() - t > 2:
                log(f"{ih[:8]}: piece {i} came {time.time() - t:.1f}s after it was asked for (its priority then {prio}, "
                    f"{h.status().num_peers} peers, {h.status().download_rate * 8 / 1e6:.0f} Mbps)")
    if not missing and e.get("urgent_until") and not any(not h.have_piece(i) for i in asked):
        e["urgent_until"] = 0
    if missing:
        if urgent:
            for k, i in enumerate(missing):
                asked.setdefault(i, (time.time(), h.piece_priority(i)))
                h.set_piece_deadline(i, 100 * k)
            if time.time() > e.get("urgent_until", 0):
                h.prioritize_pieces([(i, 0) for i, p in enumerate(h.get_piece_priorities()) if p and i not in asked])
                e["plan"] = None
            e["urgent_until"] = time.time() + 8
        return None
    out, fs = [], ti.files()
    while n > 0:
        f = fs.file_index_at_offset(off)
        foff = off - fs.file_offset(f)
        take = min(n, fs.file_size(f) - foff)
        with open(os.path.join(CACHE, ih, fs.file_path(f)), "rb") as fh:
            data = os.pread(fh.fileno(), take, foff)
        if len(data) != take:
            return None
        out.append(data)
        off += take
        n -= take
    return b"".join(out)




fronts = {}
failed = {}
serving = {}
streaming = {"n": 0}
job_cache = {}
front_lock = threading.Lock()
ts_pool = concurrent.futures.ThreadPoolExecutor(64)


def me():

    return {"replica": REPLICA, "id": BOOT}


def dealt(ih, helpers):


    k = int(ih[:8], 16) % max(1, len(helpers))
    return helpers[k:] + helpers[:k]


def make_job(ih, idx, ti):

    fs = ti.files()
    auto = idx < 0
    if auto:
        idx = max(range(fs.num_files()), key=fs.file_size)
    if idx >= fs.num_files():
        raise ValueError(f"the torrent has {fs.num_files()} files, not {idx + 1}")
    off, size, pl = fs.file_offset(idx), fs.file_size(idx), ti.piece_length()
    return {"ih": ih, "file": idx, "auto": auto, "name": fs.file_path(idx), "size": size, "offset": off, "piece_length": pl,
            "first": off // pl, "last": (off + size - 1) // pl, "chunk": max(1, CHUNK // pl),
            "helpers": dealt(ih, sorted(r for r in copies() if r != REPLICA)), "playhead": off // pl, "t": time.time(),
            "played": time.time(), "front": me()}


def front(ih, idx):


    with front_lock:
        f = fronts.get(ih)
        if f and f["file"] == idx:
            f["last_play"] = time.time()
            unwarm(f)
            return f
        f = fronts[ih] = {"file": idx, "last_play": time.time(), "job": None, "version": None, "dirty": False}
    threading.Thread(target=start_front, args=(ih, idx, f), daemon=True).start()
    return f


def warm(ih, idx, level=2):



    with front_lock:
        f = fronts.get(ih)
        if f:
            if f["file"] == idx and 0 < warm_level(f.get("warm")) < level:
                f["warm"] = level
                if f["job"]:
                    f["job"]["warm"] = level
                    f["dirty"] = True
            return
        f = fronts[ih] = {"file": idx, "last_play": time.time(), "job": None, "version": None, "dirty": False, "warm": level}
    threading.Thread(target=start_front, args=(ih, idx, f), daemon=True).start()


def unwarm(f):

    if f.get("warm"):
        f["warm"] = 0
        if f["job"]:
            f["job"]["warm"] = 0
            f["dirty"] = True


def start_front(ih, idx, f):
    try:
        job, v = read_job(ih)
        if job and f.get("warm"):
            with front_lock:
                if fronts.get(ih) is f:
                    fronts.pop(ih)
            return
        if job and is_for(job, idx):
            if job["front"].get("id") != BOOT and time.time() - job["t"] < LEASE:
                with front_lock:
                    if fronts.get(ih) is f:
                        fronts.pop(ih)
                return
            if job["front"].get("id") != BOOT:
                log(f"{ih[:8]}: taking over {job['name']} from copy {job['front']['replica']}")

            job["front"] = me()
            torrent = stored_meta(ih)
        else:
            ti = metadata(ih, f)
            job, v, torrent = make_job(ih, idx, ti), (v if job else NEW), torrent_file(ti)
            log(f"{ih[:8]}: coordinating {job['name']} ({job['size'] / 1e9:.1f} GB): {len(job['helpers'])} helpers, "
                f"{(job['last'] - job['first']) // job['chunk'] + 1} chunks")
        job["t"] = job["played"] = time.time()
        job["warm"] = warm_level(f.get("warm"))
        v2 = kv.put(JOB + hid(ih), seal(json.dumps(job).encode()), version=v)
        if v2 is None:
            theirs = read_job(ih)[0]
            if theirs and theirs["file"] == idx:
                with front_lock:
                    if fronts.get(ih) is f:
                        fronts.pop(ih)
                return
            raise RuntimeError("another copy wrote the job at the same moment")
        if torrent:
            torrents[ih] = torrent
            ts_give(ih)
        with front_lock:
            if fronts.get(ih) is f:
                f.update(job=job, version=v2)
        failed.pop(ih, None)
    except Exception as err:
        log(f"{ih[:8]}: coordinating failed: {err.__class__.__name__}: {err}")
        failed[ih] = (str(err), isinstance(err, NoMetadata), time.time())
        with front_lock:
            if fronts.get(ih) is f:
                fronts.pop(ih)


def front_state(ih, idx):


    why, for_metadata, when = failed.get(ih, ("", False, 0))
    if for_metadata and time.time() - when < 60 and ih not in fronts:
        return {"state": "failed", "why": why, "for_metadata": True}
    f = front(ih, idx)
    return {"state": "ready" if f["job"] else "hunting"}


def is_for(job, idx):

    return job["file"] == idx or idx < 0 and job.get("auto")


def current_job(ih, idx):


    f = fronts.get(ih)
    if f and f["job"] and f["file"] == idx:
        return f["job"]
    c = job_cache.get(ih)
    if not c or time.time() - c[1] > 1:
        try:
            job = read_job(ih)[0]
        except Exception:
            job = c[0] if c else None
        c = job_cache[ih] = (job, time.time())
    job = c[0]
    return job if job and is_for(job, idx) and time.time() - job["t"] < JOB_TTL else None


def report_playhead(ih, idx, byte):



    f = fronts.get(ih)
    if f and f["file"] == idx and f["job"]:
        front(ih, idx)
        note_playhead(ih, byte)
        return
    try:
        kv.put(PLAY + hid(ih), seal(json.dumps({"idx": idx, "byte": byte, "t": time.time()}).encode()))
    except Exception as err:
        log(f"{ih[:8]}: the playhead not posted ({err})")
    job = current_job(ih, idx)
    if not job or time.time() - job["t"] > LEASE:
        front(ih, idx)


def note_playhead(ih, byte):
    f = fronts.get(ih)
    job = f and f["job"]
    if job:
        piece = (job["offset"] + byte) // job["piece_length"]
        if abs(piece - job["playhead"]) >= job["chunk"]:
            job["playhead"] = piece
            f["dirty"] = True


def chunk_owners(job, c):


    hs, head = job["helpers"], job["playhead"]
    a = job["first"] + c * job["chunk"]
    out = [hs[c % len(hs)]]
    if len(hs) > 1 and a + job["chunk"] > head and a < head + HOT // job["piece_length"]:
        out.append(hs[(c + 1) % len(hs)])
    return out


def local_chunk(ih, off, n, wait=0):

    data, until = chunk_bytes(ih, off, n, urgent=True), time.time() + wait
    while data is None and time.time() < until:
        time.sleep(0.2)
        data = chunk_bytes(ih, off, n, urgent=True)
    return data


def from_helper(job, start, end, wait=0):


    ih, n = job["ih"], end - start + 1
    off = job["offset"] + start
    c = (off // job["piece_length"] - job["first"]) // job["chunk"]
    live = copies()
    for k, owner in enumerate(chunk_owners(job, c)):
        data, w = None, wait if k == 0 else 0
        if owner == REPLICA:
            data = local_chunk(ih, off, n, w)
        elif owner in live:
            try:
                st, _, body = conns.request("GET", f"{url_of(owner)}/chunk/{ih}?off={off}&n={n}&wait={w}",
                                            headers={"X-Tor-Key": CHUNK_KEY}, timeout=8 + w)
                data = body if st == 200 else None
            except Exception:
                pass
        if data is not None and len(data) == n:
            return data
    return None


def ts_range(ih, idx, start, end, stop):



    u = urllib.parse.urlparse(TS_URL)
    n = end - start + 1
    sock = socket.create_connection((u.hostname, u.port or 80), timeout=10)
    try:
        path = f"/stream?link={urllib.parse.quote(magnet(ih), safe='')}&index={idx + 1}&play"
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nRange: bytes={start}-{end}\r\nConnection: close\r\n\r\n".encode())
        got, size, head, idle = [], 0, None, 0
        while True:
            if stop and stop.is_set():
                return None, "given up"
            if not readable(sock, 1):
                idle += 1
                if idle >= 30:
                    return None, "no data for 30 s"
                continue
            idle = 0
            block = sock.recv(1 << 20)
            if not block:
                return None, f"{size} of {n} bytes, then the end"
            got.append(block)
            size += len(block)
            if head is None:
                raw = b"".join(got)
                if b"\r\n\r\n" not in raw:
                    continue
                head, body = raw.split(b"\r\n\r\n", 1)
                status = head.split(b" ", 2)[1]
                if status != b"206":
                    return None, f"HTTP {status.decode()} for a range"
                got, size = [body], len(body)
            if size >= n:
                return b"".join(got)[:n], None
    finally:
        sock.close()


ts_now = {"asks": 0, "trouble": {}}


def from_torrserver(ih, idx, start, end, stop=None):


    ts_now["asks"] += 1
    try:
        for attempt in range(60):
            if stop and stop.is_set():
                return None
            ts_give(ih)
            try:
                data, why = ts_range(ih, idx, start, end, stop)
            except OSError as err:
                data, why = None, f"{err.__class__.__name__}: {err}"
            if data is not None:
                return data
            if why == "given up":
                return None
            ts_now["trouble"][ih] = f"{time.strftime('%H:%M:%S')} {why}"
            time.sleep(1)
        raise RuntimeError(f"TorrServer didn't give bytes {start}-{end}")
    finally:
        ts_now["asks"] -= 1


def segment(ih, idx, job, start, end, served):


    t0 = time.time()
    data, polls = from_helper(job, start, end), 1
    if data is not None:
        served["helpers"] += len(data)
        return data
    stop = threading.Event()
    ts = ts_pool.submit(from_torrserver, ih, idx, start, end, stop)
    hp = ts_pool.submit(from_helper, job, start, end, 5)
    try:
        while True:
            done, _ = concurrent.futures.wait([f for f in (ts, hp) if f], timeout=10, return_when=concurrent.futures.FIRST_COMPLETED)
            if hp in done:
                try:
                    data = hp.result()
                except Exception:
                    data = None
                if data is not None:
                    served["helpers"] += len(data)
                    slow(ih, job, start, t0, "a helper", polls)
                    return data
                polls += 1
                hp = ts_pool.submit(from_helper, job, start, end, 5)
            if ts in done:
                try:
                    data = ts.result()
                except Exception as err:
                    ts_now["trouble"][ih] = f"{time.strftime('%H:%M:%S')} {err.__class__.__name__}: {err}"
                    data = None
                ts = None
                if data is not None:
                    served["torrserver"] += len(data)
                    slow(ih, job, start, t0, "TorrServer", polls)
                    return data
            if time.time() - t0 > 120:
                raise RuntimeError(f"no bytes at {start} from the helpers or TorrServer in 2 minutes")
    finally:
        stop.set()


def slow(ih, job, start, t0, source, polls):

    took = time.time() - t0
    if took > 2:
        c = ((job["offset"] + start) // job["piece_length"] - job["first"]) // job["chunk"]
        log(f"{ih[:8]}: a piece at {start / job['size'] * 100:.1f}% took {took:.1f}s, from {source} "
            f"(helpers {chunk_owners(job, c)} asked {polls} times)")


def front_loop():



    gone_since = {}
    while True:
        time.sleep(2)
        for ih, f in list(fronts.items()):
            job = f["job"]
            if not job:
                continue
            try:
                got = kv.get(PLAY + hid(ih))
                heard = json.loads(unseal(got[0])) if got else None
                if heard and heard["idx"] == f["file"] and heard["t"] > f.get("heard", 0):
                    f["heard"] = heard["t"]
                    f["last_play"] = max(f["last_play"], heard["t"])
                    unwarm(f)
                    note_playhead(ih, heard["byte"])
                if time.time() - f["last_play"] > (WARM_TTL if f.get("warm") else JOB_TTL):
                    kv.delete(JOB + hid(ih))
                    kv.delete(META + hid(ih))
                    kv.delete(PLAY + hid(ih))
                    with front_lock:
                        fronts.pop(ih, None)
                    torrents.pop(ih, None)
                    try:
                        ts_call("/torrents", {"action": "rem", "hash": ih})
                    except Exception:
                        pass
                    log(f"{ih[:8]}: nobody played {job['name']} for {JOB_TTL // 60} minutes: its job is done")
                    continue
                live = copies()
                spare = [r for r in sorted(live) if r != REPLICA and r not in job["helpers"]]
                for i, r in enumerate(job["helpers"]):
                    if r in live:
                        gone_since.pop((ih, r), None)
                    elif time.time() - gone_since.setdefault((ih, r), time.time()) > 60 and spare:
                        job["helpers"][i] = spare.pop(0)
                        log(f"{ih[:8]}: helper {r} left; copy {job['helpers'][i]} takes its chunks")
                        f["dirty"] = True
                if f["dirty"] or time.time() - job["t"] > 10:
                    job["t"], job["played"] = time.time(), f["last_play"]
                    f["dirty"] = False
                    v = kv.put(JOB + hid(ih), seal(json.dumps(job).encode()), version=f["version"])
                    if v is None:
                        theirs, v2 = read_job(ih)
                        if theirs and theirs["front"].get("id") != BOOT:
                            log(f"{ih[:8]}: copy {theirs['front']['replica']} coordinates it now")
                            with front_lock:
                                fronts.pop(ih, None)
                            continue
                        f["version"] = v2
                    else:
                        f["version"] = v
            except Exception as err:
                log(f"{ih[:8]}: coordinating: {err.__class__.__name__}: {err}")




def ts_call(path, body=None, timeout=10):
    req = urllib.request.Request(TS_URL + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return json.loads(data) if data else None


torrents = {}
ts_given = {}


def ts_give(ih):


    if time.time() - ts_given.get(ih, 0) < 240:
        return
    ts_given[ih] = time.time()
    if not torrents.get(ih):
        try:
            torrents[ih] = stored_meta(ih)
        except Exception:
            pass
    torrent = torrents.get(ih)
    if not torrent:
        return
    b = "fleet" + os.urandom(8).hex()
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{ih}.torrent"\r\n'
            f"Content-Type: application/x-bittorrent\r\n\r\n").encode() + torrent + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(TS_URL + "/torrent/upload", data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except Exception as err:
        log(f"{ih[:8]}: TorrServer didn't take the metadata ({err})")


def torrserver_loop():


    want = {"CacheSize": TS_CACHE, "ReaderReadAHead": 95, "PreloadCache": 2, "ConnectionsLimit": 200,
            "PeersListenPort": TS_PEER_PORT, "TorrentDisconnectTimeout": 600, "ResponsiveMode": True, "UseDisk": False}
    while True:
        try:
            cur = ts_call("/settings", {"action": "get"})
            if any(cur.get(k) != v for k, v in want.items()):
                ts_call("/settings", {"action": "set", "sets": {**cur, **want}})
                log("TorrServer's settings set")
        except Exception as err:
            log(f"TorrServer: {err}")
            time.sleep(5)
            continue
        time.sleep(300)


def trackers_loop():

    while True:
        try:
            req = urllib.request.Request(TRACKER_LIST, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                got = [t for t in r.read().decode().split() if t.startswith(("udp://", "http://", "https://"))]
            if len(got) >= 10:
                ALL_TRACKERS[:] = list(dict.fromkeys(TRACKERS + got))
                log(f"{len(ALL_TRACKERS)} trackers")
            time.sleep(12 * 3600)
        except Exception as err:
            log(f"trackers: {err}")
            time.sleep(600)




def timing(rows):

    rows = [r for r in list(rows) if time.time() - r[0] < 300]
    if not rows:
        return None
    secs = sorted(r[1] for r in rows)
    return {"n": len(rows), "urgent": sum(1 for r in rows if r[3]), "median_s": round(secs[len(secs) // 2], 2),
            "p90_s": round(secs[int(len(secs) * 0.9)], 2), "mbps_each": round(sum(r[2] for r in rows) * 8 / max(1e-6, sum(secs)) / 1e6)}


def status():
    out = {"app": APP, "replica": REPLICA, "code": CODE, "streams_now": streaming["n"], "copies": sorted(copies()), "fronting": [], "serving": [], "helping": [],
           "cpus": os.cpu_count(), "load": [round(x, 1) for x in os.getloadavg()],
           "hunting": [ih[:8] for ih, e in list(handles.items()) if e["role"] == "meta" and not e["h"].status().has_metadata]}
    for ih, f in list(fronts.items()):
        job = f["job"] or {}
        out["fronting"].append({"ih": ih[:8], "name": job.get("name"), "size_gb": round(job.get("size", 0) / 1e9, 2), "helpers": len(job.get("helpers", [])),
                                "lease_age_s": round(time.time() - job["t"], 1) if job else None,
                                "playhead": round((job.get("playhead", 0) - job.get("first", 0)) / max(1, job.get("last", 1) - job.get("first", 0)) * 100, 1)})
    for ih, s in list(serving.items()):
        out["serving"].append({"ih": ih[:8], "name": s["name"], "served_mb": {"helpers": round(s["helpers"] / 1e6, 1), "torrserver": round(s["torrserver"] / 1e6, 1)}})
    for ih, e in list(handles.items()):
        if e["role"] != "helper" or not e["plan"]:
            continue
        h, plan = e["h"], e["plan"]
        st = h.status()
        want = sum(b - a + 1 for a, b, _ in plan)
        have = sum(1 for a, b, _ in plan for i in range(a, b + 1) if h.have_piece(i))
        out["helping"].append({"ih": ih[:8], "chunks": len(plan), "have": f"{have}/{want} pieces", "down_mbps": round(st.download_rate * 8 / 1e6, 1),
                               "up_mbps": round(st.upload_rate * 8 / 1e6, 1), "peers": st.num_peers, "known_peers": st.list_peers,
                               "state": str(st.state).split(".")[-1], "paused": bool(st.flags & lt.torrent_flags.paused),
                               "error": st.errc.message() if st.errc.value() else None})
    out["dht_nodes"] = ses.status().dht_nodes
    out["converted"] = {"pieces": len(pieces), "gb": round(pieces_size["n"] / 1e9, 2),
                        "ffmpeg": [{"piece": n, "cls": j["cls"], "running": bool(j["p"]), "best": j["best"]} for (_, n), j in list(cpu_jobs.items())],
                        "source_trouble": {k: {"why": v["why"], "paused_s": max(0, round(v["until"] - time.time()))} for k, v in list(src_trouble.items())},
                        "macs": {"each": {mac_name(u): {"up": v["up"], "done_here": v["done"], "under_way_here": v["inflight"], "readied_here": v["readied"],
                                                        "jobs": (v["health"] or {}).get("jobs"), "engine": (v["health"] or {}).get("engine"),
                                                        "draining": (v["health"] or {}).get("draining"), "failed_in_a_row": v["fails"],
                                                        "usual_s": round(sorted(v["took"])[len(v["took"]) // 2], 2) if v["took"] else None}
                                          for u, v in list(macs.items())},
                                 "gave_up": mac_failed["n"], "sent_to_a_second": mac_failed["hedged"], "sent_again": mac_failed["again"],
                                 "under_way": len(mac_jobs),
                                 "last": [dict(zip(("at", "piece", "cls", "read_s", "mac_s", "sent_mb", "mac", "up_s", "convert_s", "down_s"), r)) for r in list(bstats["mac"])[-12:]]},
                        "reads": {"waiting": len(up_wait), "under_way": dict(up_now), "sends_waiting": len(send_wait),
                                  "source_5min": timing(bstats["vps"]), "from_reader_5min": timing(bstats["reader"])},
                        "last_pieces": [dict(zip(("at", "piece", "cls", "s", "cpu_s", "src_wait_s"), r)) for r in list(bstats["pieces"])[-12:]],
                        "served": [dict(zip(("at", "piece", "from", "s"), r)) for r in list(bstats["served"])[-20:]],
                        "ram_pieces_mb": round(mem_size["n"] / 1e6),
                        "films_read": {k: {"head": f["head"], "done": len(f["done"]), "of": f["total"], "converters": f["filling"],
                                           "buffer": {"ready_ahead_mb": round(f.get("ready_ahead", 0) / 1e6), "to_piece": f.get("buffer_end")}}
                                       for k, f in list(films.items())}}
    out["torrserver"] = {"asks_now": ts_now["asks"], "queued": ts_pool._work_queue.qsize(), "trouble": {k[:8]: v for k, v in ts_now["trouble"].items()}}
    try:
        out["torrserver"]["torrents"] = [{"ih": t.get("hash", "")[:8], "stat": t.get("stat_string"), "peers": f"{t.get('active_peers', 0)}/{t.get('total_peers', 0)}",
                                          "down_mbps": round((t.get("download_speed") or 0) * 8 / 1e6, 1)} for t in ts_call("/torrents", {"action": "list"}, timeout=3) or []]
    except Exception as err:
        out["torrserver"]["torrents"] = str(err)
    du = shutil.disk_usage(CACHE) if os.path.isdir(CACHE) else None
    out["ram_cache"] = {"used_gb": round((du.total - du.free) / 1e9, 2), "of_gb": round(du.total / 1e9, 2)} if du else None
    return out

















GDRIVE = os.environ.get("GDRIVE_URL", "https://gdrive2.billythebishop4.workers.dev")
PIECE_S = 1.0

TS_BASE = 10

HLS_AHEAD = 12
HLS_LOCAL = 1
CONVERTERS = int(os.environ.get("CONVERTERS", "40"))
X264 = ["-c:v", "libx264", "-preset", "superfast", "-x264-params", "mbtree=1:rc-lookahead=10", "-crf", "19", "-profile:v", "high",
        "-level", "4.1"]

AUDIO_RUNS = 2
AUDIO_BATCH = 16
SOUND_ASKS, SOUND_HOLD, SOUND_WAIT = 6, 3, 120

MUXED = os.environ.get("MUXED_AUDIO") == "1"


SRC_BLOCK = int(float(os.environ.get("SRC_BLOCK_MB", "8")) * 2**20)
SRC_ASKS = int(os.environ.get("SRC_ASKS", "6"))

SEND_MAX = 16
SRC_CACHE = int(float(os.environ.get("SRC_CACHE_GB", "3")) * 2**30)

THEIRS_CACHE = 2**30
READ_RATE, READ_MAX = 16.0, 250e6 / 8


READ_HIGH = float(os.environ.get("READ_HIGH_MBPS", "450")) * 1e6 / 8

BG_ASKS = 3
RUN_BLOCKS = 4
BG_RUN = 8

RELAY_KEEP = 4



LAND_BLOCKS = 6

LAND_CHUNK = 256 * 1024
LAND_TAILS = 16
HEAD_ALONE = 3
JUMP_ALONE = 10
SRC_PAUSE = 30


SRC_LEAD = 4
MAC_URLS = [u.strip().rstrip("/") for u in os.environ.get("MAC_URLS", os.environ.get("MAC_URL", "")).split(",") if u.strip()]


MAC_KEY = os.environ.get("MAC_KEY", "")
MAC_HEDGE = {0: 2.5, 1: 8}

MAC_WAIT = {0: 30, 1: 60}
MAC_ROOM = 2

MAC_FULL_WAIT = 3
MAC_HUNG = 12
MAC_HERE = 6
BATCH = 1

MAC_LOOK, MAC_LOOK_WAIT = 5, 3
PLAYER_RUNS = 2
LAND_LOAD = 1.5

LOOKUP_TRIES = 4
FFMPEG_MAX = int(os.environ.get("FFMPEG_MAX", "0")) or max(3, os.cpu_count() or 0)

FILL_RUNS = 5


FILL_CHUNK = 64 * 2**20
FILL_STALE = 90
FILL_FROM = 24

DEAL_RUN = 8

RUN_MAX = 12


ENC_TB = ["-enc_time_base:v", "demux"]


RUN_X264 = [x + ":ipratio=1" if x.startswith("mbtree") else x for x in X264]


SEG_DIR = os.environ.get("SEG_DIR", "/segs")
SEG_DISK = int(float(os.environ.get("SEG_DISK_GB", "30")) * 2**30)
BUFFER_BYTES = int(float(os.environ.get("BUFFER_MB", "4000")) * 2**20)

BUFFER_MAKING = 16
RAM_PIECES = int(float(os.environ.get("RAM_PIECES_GB", "2")) * 2**30)
FILM_IDLE = 600
FILM_LEFT = 30

EARLY_TS = b"\x47\x1f\xff\x10" + b"\xff" * 184

EARLY_MP4 = b"\x00\x00\x00\x08free"

EARLY_EVERY = 20
HINTS = 3
WARM_FIRST = 4
WARM_NEXT = 2


FACTS_FIRST = 4



FACTS_WORKERS = 4
LIGHT_ASKS = 4
FACTS_WAIT = 120
READER = f"{APP}.reader."
READER_TTL = 6 * 3600
FACTS = f"{APP}.facts."
FACTS_TTL = 60 * 86400
PROBE_V = 2
BROWSER_GROUPS = [
    ("1080 remux", ("from 1080p Blu-ray", "studio SDR colours · starts and seeks fastest")),
    ("4k remux", ("from 4K HDR Blu-ray", "most detail · HDR tone-mapped")),
    ("4k", ("from 4K", "4K encode · HDR tone-mapped if HDR")),
    ("1080", ("from 1080p", "1080p encode")),
]
BROWSER_MAX = 200
NOT_AS_IT_IS = re.compile(r"vc-?1|mpeg-?2|\bav1\b|xvid|\.avi\b|\.m2ts\b|\bsbs\b|\b3d\b", re.I)

NOT_WORTH_IT = re.compile(r"\b3d\b|\bsbs\b|60 ?fps|\brife\b|ai.?enhanced|xvid|dvdrip|\bcam\b|\.avi\b|\.m2ts\b", re.I)
NOT_MATROSKA = re.compile(r"\.(mp4|m4v|mov|webm|ts|wmv|avi|m2ts)\Z", re.I)

APPLE_AS_IS = re.compile(r"\.(mp4|m4v|mov)\Z", re.I)
NOT_FOR_APPLE = re.compile(r"truehd|\bdts|vc-?1|mpeg-?2|\bav1\b", re.I)
APPLE_SOUND = {"AAC", "Dolby Digital", "Dolby Digital Plus", "MP3", "FLAC", "PCM", "ALAC"}
EPISODE = re.compile(r"(?<![a-z0-9])s\d{1,2}[ ._-]?e\d{1,3}(?![0-9])|(?<![0-9])\d{1,2}x\d{2,3}(?![0-9])", re.I)

NOT_FOR_BROWSERS = re.compile(r"mkv|hevc|x265|h\.?265|10.?bit|hdr|dv\b|dovi|dolby.?vision|truehd|dts|atmos|remux|2160p|\b4k\b", re.I)
MANIFEST_BROWSER = {"id": "fleet.browser", "version": "1.0.0", "name": "Frame Browser",
                    "description": "The gdrive addon's big streams (4K, HEVC, HDR, TrueHD/DTS, MKV) converted by the fleet on the "
                                   "fly to 1080p H.264 with AAC stereo: they play in a browser.",
                    "resources": ["stream"], "types": ["movie", "series"], "idPrefixes": ["tt"], "catalogs": []}
src_meta = {}
src_blocks = collections.OrderedDict()
src_size = {"n": 0}
busy = {}
hls_lock = threading.Lock()
turns = threading.Condition()
up_wait = {}
up_now = {"urgent": 0, "bg": 0, "light": 0}
up_going = {}
runs = {}
reading = {}
src_tails = collections.OrderedDict()

land_reads = {}
send_wait = {}
send_now = {"n": 0}
turn_order = itertools.count()
paced_at = {}
src_trouble = {}
src_back = {}
probed = {}
pieces = collections.OrderedDict()
mem_pieces = collections.OrderedDict()
mem_size = {"n": 0}
pieces_size = {"n": 0}
films = {}
heard_at = {}
hinted = {}
src_waits = {}
bstats = {"vps": collections.deque(maxlen=300), "reader": collections.deque(maxlen=300), "pieces": collections.deque(maxlen=60),
          "served": collections.deque(maxlen=60), "mac": collections.deque(maxlen=120)}
macs = {u: {"up": True, "health": None, "inflight": 0, "readied": 0, "full_until": 0, "done": 0, "misses": 0, "fails": 0, "suspect": False,
            "out_until": 0, "look_at": 0, "looking": False, "calls": [], "took": collections.deque(maxlen=30), "answered": 0} for u in MAC_URLS}




mac_lock = threading.Lock()
mac_batch = {"until": 0}
mac_jobs = {}
mac_failed = {"n": 0, "hedged": 0, "again": 0}
mac_looked = {"at": 0, "busy": False}





def hls_token(gurl, file_id=""):


    return base64.urlsafe_b64encode(seal(json.dumps({"u": gurl, "f": file_id}).encode())).decode().rstrip("=")


def src_key(gurl, file_id=""):

    return hashlib.sha256(f"{file_id or gurl}|cuts {PIECE_S:g}".encode()).hexdigest()[:16]


def open_token(token):

    try:
        sealed = unseal(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))).decode()
    except Exception:
        return None
    gurl, file_id = sealed, ""
    if sealed.startswith("{"):
        opened = json.loads(sealed)
        gurl, file_id = opened["u"], opened.get("f") or ""
    key = src_key(gurl, file_id)
    with hls_lock:
        m = src_meta.setdefault(key, {"gurl": gurl, "url": None, "size": None, "duration": None, "start": None, "t": 0, "token": token, "reader": None})
        if m["gurl"] != gurl:
            m.update(gurl=gurl, token=token)
    return key, gurl


def src_lookup(key, again=False):




    m, t0 = src_meta[key], time.time()
    for _ in range(2):
        if m["url"] and time.time() - m["t"] < 1200 and (not again or m["t"] >= t0):
            return m
        if once("lookup", key, 0, lambda: src_find(key)) is not None:
            return m
    raise RuntimeError(m.get("lookup_err") or "the source's address wasn't found")


def src_find(key):





    m = src_meta[key]
    try:
        st, h, _ = conns.request("GET", m["gurl"], headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        if st not in (301, 302, 303, 307, 308):
            raise RuntimeError(f"the gdrive link answered HTTP {st}")
        url = urllib.parse.urljoin(m["gurl"], h.get("Location"))
        probe = m.get("finding")
        known = probe["known"] if probe and not probe["known"].is_set() and key not in facts_gentle else None
        ev = None
        with turns, hls_lock:
            if known is not None and (key, 0) not in src_blocks and (key, 0) not in reading and ("src", key, 0) not in busy:
                ev = reading[(key, 0)] = busy[("src", key, 0)] = threading.Event()
        if ev is None:
            st, h, _ = conns.request("GET", url, headers={"Range": "bytes=0-0", "User-Agent": "Mozilla/5.0"}, timeout=60)
            if st != 206:
                raise RuntimeError(f"the source answered HTTP {st} for a range")
            m.update(url=url, size=int(h.get("Content-Range").split("/")[1]), t=time.time())
            return m
        u = urllib.parse.urlsplit(url)
        c = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=60)
        try:
            t1 = time.time()
            c.request("GET", u.path + (f"?{u.query}" if u.query else ""), headers={"Range": f"bytes=0-{SRC_BLOCK - 1}", "User-Agent": "Mozilla/5.0"})
            r = c.getresponse()
            if r.status != 206:
                raise RuntimeError(f"the source answered HTTP {r.status} for a range")
            size = int(r.getheader("Content-Range").split("/")[1])
            m.update(url=url, size=size, t=time.time())
            known.at = time.time()
            known.set()
            if c.sock is not None:
                c.sock.settimeout(30)
            try:
                data = r.read(min(SRC_BLOCK, size))
                if len(data) == min(SRC_BLOCK, size):
                    bstats["vps"].append((time.time(), time.time() - t1, len(data), True))
                    keep_block(key, 0, data)
            except (http.client.HTTPException, OSError):
                pass
        finally:
            c.close()
            with hls_lock:
                ev = unclaim(key, 0)
            if ev:
                ev.set()
            with turns:
                turns.notify_all()
        return m
    except Exception as err:
        m["lookup_err"] = f"{err.__class__.__name__}: {err}"
        raise


def once(kind, key, n, make):

    k = (kind, key, n)
    with hls_lock:
        ev = busy.get(k)
        first = ev is None
        if first:
            ev = busy[k] = threading.Event()
    if not first:
        ev.wait(600)
        return None
    try:
        return make()
    finally:
        with hls_lock:
            busy.pop(k, None)
        ev.set()


def block_cached(key, b):
    with hls_lock:
        data = src_blocks.get((key, b))
        if data is not None:
            src_blocks.move_to_end((key, b))
        return data


def keep_block(key, b, data):
    with hls_lock:
        if (key, b) in src_blocks:
            return
        src_blocks[(key, b)] = data
        src_size["n"] += len(data)
        while src_size["n"] > SRC_CACHE and src_blocks:
            src_size["n"] -= len(src_blocks.popitem(last=False)[1])
        if src_meta.get(key, {}).get("reader") not in (None, REPLICA):
            theirs = [k for k in src_blocks if src_meta.get(k[0], {}).get("reader") not in (None, REPLICA)]
            n = sum(len(src_blocks[k]) for k in theirs)
            for k in theirs:
                if n <= THEIRS_CACHE:
                    break
                n -= len(src_blocks[k])
                src_size["n"] -= len(src_blocks.pop(k))


def fetch_kind(key, urgent):


    return "src" if urgent or src_meta[key].get("reader") in (None, REPLICA) else "src-bg"


def wanted_block(key, b, urgent=True):

    return block_cached(key, b) is None and (fetch_kind(key, urgent), key, b) not in busy


def watched(key):

    f = films.get(key)
    return f is not None and time.time() - f["seen"] < FILM_LEFT


def bg_rate(key):



    info = probed.get(key)
    if watched(key) and settled():
        return READ_HIGH
    rate, most = (READ_RATE, READ_MAX) if watched(key) else (2.0, 60e6 / 8)
    if not info or not info.get("size") or not info.get("duration"):
        return most
    return min(most, rate * info["size"] / info["duration"])


def settled():





    now = time.time()
    for k, f in list(films.items()):
        if now - f.get("seen", 0) >= FILM_LEFT:
            continue
        orig = f.get("variant") == "orig"
        pk, done, head = k + ORIG if orig else k, () if orig else f.get("done", ()), f["head"]
        if now - f.get("jumped", 0) < JUMP_ALONE or any((pk, m) not in pieces and (pk, m) not in mem_pieces and m not in done
                                                        for m in range(head + 1, min(f.get("total") or 10**9, head + 1 + HLS_AHEAD))):
            return False
    return not near_waits() and not any(from_player(k, n) <= HLS_AHEAD for (k, _), n in list(up_going.items()))


def read_alone(key, n):




    d = from_player(key, n)
    f = films.get(key)
    return d <= HEAD_ALONE or (d <= HLS_AHEAD and f is not None and time.time() - f.get("jumped", 0) < JUMP_ALONE)


def far(key, n):

    f = films.get(key)
    return (f is not None and n is not None
            and not f["head"] - 2 <= n <= f["head"] + max(HLS_AHEAD, f.get("buffer_end", f["head"]) - f["head"]))


def from_player(key, n):



    if n is None:
        return -1
    f = films.get(key)
    head = f["head"] if f else 0
    return n - head if n >= head else head - n if n >= head - 2 else 10**6 + head - n


@contextlib.contextmanager
def upstream_turn(key, b, n, urgent, count=1):










    k = (key, b)
    with turns:
        w = up_wait[k] = {"urgent": urgent, "n": n, "light": not urgent and key in facts_gentle}
        while k not in reading and k not in src_blocks:
            now = time.time()
            if w["urgent"]:
                d = from_player(key, w["n"])
                go = (up_now["urgent"] < SRC_ASKS + (2 if d <= HEAD_ALONE else 0) and (d <= 1 or not landing_pending(key))
                      and min((from_player(x[0], y["n"]), x) for x, y in up_wait.items() if y["urgent"])[1] == k)
            elif w["light"]:
                go = up_now["light"] < LIGHT_ASKS and not near_waits()
            else:
                go = (up_now["bg"] < (BG_ASKS if watched(key) else 1) and paced_at.get(key, 0) <= now and not near_waits() and not landing_pending(key)
                      and min((from_player(x[0], y["n"]), x) for x, y in up_wait.items() if not y["light"] and paced_at.get(x[0], 0) <= now)[1] == k)
            if go:
                break
            turns.wait(0.5)
        del up_wait[k]
        kind = None if k in reading or k in src_blocks else "urgent" if w["urgent"] else "light" if w["light"] else "bg"
        if kind:
            up_now[kind] += 1
        if kind == "bg":
            paced_at[key] = now + count * SRC_BLOCK / bg_rate(key)
        elif kind == "urgent":
            up_going[k] = w["n"]
    try:
        yield kind
    finally:
        if kind:
            with turns:
                up_now[kind] -= 1
                up_going.pop(k, None)
                turns.notify_all()


def near_waits():

    return any(y["urgent"] and from_player(x[0], y["n"]) <= HLS_AHEAD for x, y in up_wait.items())


def landing_pending(key):




    f = films.get(key)
    land = f.get("landing") if f is not None else None
    if land is None or time.time() - f.get("jumped", 0) >= JUMP_ALONE or not src_meta.get(key, {}).get("size"):
        return False
    try:
        pb, pe, _ = piece_span(key, land, f.get("variant") == "orig")
    except Exception:
        return False
    return any(not block_here(key, b, pb) for b in range(pb // SRC_BLOCK, (pe - 1) // SRC_BLOCK + 1))


def block_here(key, b, at):

    if (key, b) in src_blocks:
        return True
    tail = src_tails.get((key, b))
    return tail is not None and b * SRC_BLOCK + tail[0] <= at


def up_hurry(key, b, n):


    with turns:
        w = up_wait.get((key, b))
        if w and (not w["urgent"] or (n is not None and w["n"] is not None and from_player(key, n) < from_player(key, w["n"]))):
            w.update(urgent=True, n=n)
            turns.notify_all()
        ev = reading.get((key, b))
        if ev is not None and from_player(key, n) < from_player(key, getattr(ev, "n", 10**9)):
            ev.n = n


@contextlib.contextmanager
def send_turn(key, n, urgent):


    me = (0 if urgent else 1, next(turn_order))
    with turns:
        send_wait[me] = (key, n)
        while not (send_now["n"] < SEND_MAX and (urgent or not any(landing_pending(k) for k in list(films)))
                   and min(send_wait, key=lambda x: (x[0], from_player(*send_wait[x]), x[1])) == me):
            turns.wait(0.5)
        del send_wait[me]
        send_now["n"] += 1
    try:
        yield
    finally:
        with turns:
            send_now["n"] -= 1
            turns.notify_all()


def src_block(key, b, urgent=True, n=None):



    data = block_cached(key, b)
    m = src_meta[key]
    reader = m.get("reader")
    here = reader in (None, REPLICA)
    urgent = urgent and key not in facts_gentle
    tried = False
    while data is None:
        if urgent and here:
            if tried and far(key, n):
                raise Dropped(f"the source's block {b}: for a piece no longer wanted")
            up_hurry(key, b, n)
            if (key, b) in reading and from_player(key, n) <= HEAD_ALONE:

                for run in [r for r in list(runs.values()) if r["key"] == key and r["bg"] and b in r["left"]]:
                    end_call(run)
        tried = True

        def fetch():
            if not here:
                for attempt in range(4):
                    t0 = time.time()
                    try:
                        st, _, body = conns.request("GET", f"{url_of(reader)}/blk/{m['token']}/{b}?u={int(urgent)}" + ("" if n is None else f"&n={n}"),
                                                    headers={"X-Tor-Key": CHUNK_KEY}, timeout=60)
                        if st == 200 and len(body) > 0:
                            bstats["reader"].append((time.time(), time.time() - t0, len(body), urgent))
                            keep_block(key, b, body)
                            return body
                        err = f"HTTP {st}"
                    except Exception as e:
                        err = f"{e.__class__.__name__}: {e}"
                    time.sleep(0.5 * 2 ** attempt)
                raise RuntimeError(f"block {b} from the reader, copy {reader}: {err}")
            with hls_lock:
                tail = src_tails.get((key, b))



            more = claim_run(key, b) if urgent and not read_alone(key, n) and tail is None else []

            span = None if tail is None else land_span(b * SRC_BLOCK, b * SRC_BLOCK + tail[0], tail[1])

            arrived, result = threading.Event(), {}
            threading.Thread(target=read_run, args=(key, [b] + more, urgent, n, arrived, result, span), daemon=True).start()
            arrived.wait()
            data = block_cached(key, b)
            if data is None:
                raise (Dropped if result.get("dropped") else RuntimeError)(result.get("err") or f"the source's block {b}: not read")
            return data
        data = once(fetch_kind(key, urgent), key, b, fetch) or block_cached(key, b)
    return data


def claim_run(key, b, count=RUN_BLOCKS, urgent=True):




    size, more = src_lookup(key)["size"], []
    with turns, hls_lock:
        for nb in range(b + 1, min((size - 1) // SRC_BLOCK, b + count - 1) + 1):
            w = up_wait.get((key, nb))
            if (key, nb) in src_blocks or (key, nb) in reading or (("src", key, nb) in busy and not w) or (w and w["urgent"] and not urgent):
                break
            ev = reading[(key, nb)] = threading.Event()
            busy.setdefault(("src", key, nb), ev)
            more.append(nb)
        turns.notify_all()
    return more


def unclaim(key, nb):

    ev = reading.pop((key, nb), None)
    if ev is not None and busy.get(("src", key, nb)) is ev:
        del busy[("src", key, nb)]
    return ev


def let_go(key, nb, ev):


    if ev is not None:
        if reading.get((key, nb)) is ev:
            del reading[(key, nb)]
        if busy.get(("src", key, nb)) is ev:
            del busy[("src", key, nb)]
    return ev


def relay_conn(u, urgent, timeout=30):



    key = (u.scheme, u.netloc)
    with conns.lock:
        while len(conns.idle[key]) > (0 if urgent else 1):
            c, since = conns.idle[key].pop()
            if time.time() - since <= POOL_IDLE and c.sock is not None and not readable(c.sock):
                c.sock.settimeout(timeout)
                return c, True
            c.close()
    return (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=timeout), False


def relay_keep(u, c, r, run):


    key = (u.scheme, u.netloc)
    with conns.lock:
        if (not run.get("ended") and c.sock is not None and getattr(r, "isclosed", lambda: False)() and not getattr(r, "will_close", True)
                and len(conns.idle[key]) < RELAY_KEEP):
            run["conn"] = None
            conns.idle[key].append((c, time.time()))
            return None
    return c


def land_span(s, e, tail=None):


    return {"s": s, "e": e, "buf": bytearray(e - s), "got": 0, "over": False, "dropped": False, "cv": threading.Condition(), "tail": tail}


def keep_tail(key, b, lo, data):

    with hls_lock:
        src_tails[(key, b)] = (lo, data)
        src_tails.move_to_end((key, b))
        while len(src_tails) > LAND_TAILS:
            src_tails.popitem(last=False)


def land_take(key, r, span, left, size, urgent, t0):


    s, e, buf = span["s"], span["e"], span["buf"]
    pos = s + span["got"]
    while left:
        if pos < min((left[0] + 1) * SRC_BLOCK, size, e):
            data = r.read(min(LAND_CHUNK, e - pos))
            if not data:
                raise http.client.IncompleteRead(b"", e - pos)
            buf[pos - s:pos - s + len(data)] = data
            pos += len(data)
            with span["cv"]:
                span["got"] = pos - s
                span["cv"].notify_all()
            continue
        nb = left.pop(0)
        lo, hi = max(s, nb * SRC_BLOCK), min((nb + 1) * SRC_BLOCK, size, e)
        if span["tail"] is not None:
            keep_block(key, nb, bytes(buf[lo - s:hi - s]) + span["tail"])
            with hls_lock:
                src_tails.pop((key, nb), None)
        elif lo > nb * SRC_BLOCK:
            keep_tail(key, nb, lo - nb * SRC_BLOCK, bytes(buf[lo - s:hi - s]))
        else:
            keep_block(key, nb, bytes(buf[lo - s:hi - s]))
        bstats["vps"].append((time.time(), time.time() - t0, hi - lo, urgent))
        t0 = time.time()
        with turns:
            turns.notify_all()
        if src_trouble.pop(key, None):
            src_back[key] = time.time()
        yield nb


def read_run(key, blocks, urgent, n, arrived, result, span=None):








    left, why, st, tries, spare, claimed = list(blocks), "?", None, 0, True, False
    head = urgent and from_player(key, n) <= 1
    with hls_lock:
        claims = {nb: reading.get((key, nb)) for nb in blocks[1:]}

    def arrive(nb):
        if nb == blocks[0]:
            arrived.set()
            return
        with hls_lock:
            ev = let_go(key, nb, claims.pop(nb, None))
        if ev is not None:
            ev.set()
    try:
        while tries < 4:
            st, src = None, src_lookup(key, again=tries == 2 or (st is not None and 400 <= st < 500))

            with hls_lock:
                if claims.get(left[0]) is not None and reading.get((key, left[0])) is claims[left[0]]:
                    del reading[(key, left[0])]
            with upstream_turn(key, left[0], n, urgent, len(left) if urgent else BG_RUN) as went:
                if not went:
                    ev = reading.get((key, left[0]))
                    if ev:
                        ev.wait(120)
                        if hasattr(ev, "n"):
                            urgent, n = True, ev.n
                    if (key, left[0]) in src_blocks:
                        return
                    tries += 1
                    continue
                if not urgent and tries == 0 and not claimed and key not in facts_gentle:
                    claimed = True
                    more = claim_run(key, left[-1], BG_RUN, False)
                    with hls_lock:
                        claims.update((nb, reading.get((key, nb))) for nb in more)
                    left += more
                    if went == "bg" and len(left) < BG_RUN:
                        with turns:
                            paced_at[key] -= (BG_RUN - len(left)) * SRC_BLOCK / bg_rate(key)
                with hls_lock:
                    trouble = src_trouble.get(key)
                    paused = trouble and time.time() < trouble["until"] and not head
                if paused:
                    raise RuntimeError(f"the source isn't answering ({trouble['why']}): asked again in {trouble['until'] - time.time():.0f} s")
                if span is None:
                    start, end = left[0] * SRC_BLOCK, min(src["size"], (left[-1] + 1) * SRC_BLOCK) - 1
                else:
                    start, end = span["s"] + span["got"], span["e"] - 1
                u = urllib.parse.urlsplit(src["url"])
                c, kept = relay_conn(u, urgent)
                r, stale = None, False
                run = {"key": key, "n": n, "conn": c, "bg": not urgent, "left": left}
                with hls_lock:
                    runs[id(run)] = run
                try:
                    t0 = time.time()
                    if c.sock is None:
                        c.connect()
                    if run.get("ended"):
                        raise ConnectionAbortedError("ended")
                    c.request("GET", u.path + (f"?{u.query}" if u.query else ""), headers={"Range": f"bytes={start}-{end}", "User-Agent": "Mozilla/5.0"})
                    r = c.getresponse()
                    st, why = r.status, f"HTTP {r.status}"
                    if st == 206:
                        if span is not None:
                            for nb in land_take(key, r, span, left, src["size"], urgent, t0):
                                arrive(nb)
                        while left:
                            nb = left[0]
                            want = min(SRC_BLOCK, src["size"] - nb * SRC_BLOCK)
                            if not urgent:
                                with turns:
                                    while landing_pending(key) and not run.get("ended"):
                                        turns.wait(0.2)
                            data = r.read(want)
                            if len(data) < want:
                                raise http.client.IncompleteRead(data, want - len(data))
                            bstats["vps"].append((time.time(), time.time() - t0, len(data), urgent))
                            t0 = time.time()
                            keep_block(key, nb, data)
                            with turns:
                                turns.notify_all()
                            if src_trouble.pop(key, None):
                                src_back[key] = time.time()
                            left.pop(0)
                            arrive(nb)
                        c = relay_keep(u, c, r, run)
                        return
                    r.read()
                except Exception as e:
                    st, why = None, f"{e.__class__.__name__}: {e}"
                    stale = kept and r is None
                finally:
                    if c is not None:
                        c.close()
                    with hls_lock:
                        runs.pop(id(run), None)
            if run.get("ended"):
                raise Dropped(f"the source's blocks {left[0]}-{left[-1]}: no longer wanted")
            if stale and spare:
                spare = False
                continue
            with hls_lock:
                trouble = src_trouble.setdefault(key, {"n": 0, "until": 0, "why": why, "since": time.time()})
                trouble["n"] += 1
                trouble["why"] = why
                if trouble["n"] >= 6 and time.time() >= trouble["until"]:
                    trouble.update(n=3, until=time.time() + SRC_PAUSE)
                    log(f"hls {key}: the source isn't answering ({why}): its reads wait {SRC_PAUSE} s")
            tries += 1
            if tries < 4:
                time.sleep(min(1, 0.5 * 2 ** (tries - 1)) if head else 0.5 * 2 ** (tries - 1))
        raise RuntimeError(f"the source's blocks {left[0]}-{left[-1]}: {why}")
    except Exception as err:
        result.update(err=str(err), dropped=isinstance(err, Dropped))
    finally:
        arrived.set()
        with hls_lock:
            evs = [let_go(key, nb, claims.pop(nb, None)) for nb in left if nb != blocks[0]]
        for ev in evs:
            if ev is not None:
                ev.set()


def land_now(key, n):



    f = films.get(key)
    if n is not None and f is not None and f.get("landing") == n and time.time() - f.get("jumped", 0) < JUMP_ALONE:
        land_read(key, n)


def land_read(key, m):






    info, f, size = probed.get(key), films.get(key), src_meta.get(key, {}).get("size")
    if not info or not info.get("pos") or f is None or not size or f.get("variant") == "orig":
        return
    try:
        pb, pe, _ = piece_span(key, m)
        if m + 2 < len(info["cuts"]):
            pe = max(pe, piece_span(key, m + 1)[1])
    except Exception:
        return
    b0, ev0 = pb // SRC_BLOCK, threading.Event()
    last = min((min(pe, size) - 1) // SRC_BLOCK, b0 + LAND_BLOCKS - 1)
    with turns, hls_lock:
        old = land_reads.get(key)
        if (old is not None and old["m"] == m and old["s"] == pb) or block_here(key, b0, pb) or (key, b0) in reading or ("src", key, b0) in busy:
            return
        busy[("src", key, b0)] = ev0
        blocks = [b0]
        for nb in range(b0 + 1, last + 1):
            if (key, nb) in src_blocks or (key, nb) in reading or (("src", key, nb) in busy and (key, nb) not in up_wait):
                break
            ev = reading[(key, nb)] = threading.Event()
            busy.setdefault(("src", key, nb), ev)
            blocks.append(nb)
        span = land_span(pb, min(size, (blocks[-1] + 1) * SRC_BLOCK))
        span["m"] = m
        land_reads.pop(key, None)
        land_reads[key] = span
        while len(land_reads) > 4:
            del land_reads[next(iter(land_reads))]
        turns.notify_all()
    threading.Thread(target=land_go, args=(key, m, blocks, span, ev0), daemon=True).start()


def land_go(key, m, blocks, span, ev0):

    result = {}
    try:
        read_run(key, blocks, True, m, threading.Event(), result, span)
    finally:
        with hls_lock:
            if busy.get(("src", key, blocks[0])) is ev0:
                del busy[("src", key, blocks[0])]
        ev0.set()
        with span["cv"]:
            span.update(over=True, dropped=bool(result.get("dropped")), err=result.get("err"))
            span["cv"].notify_all()


def land_part(key, a, z, some=False):



    s = land_reads.get(key)
    if s is not None and s["s"] <= a < s["e"]:
        z, need = min(z, s["e"]), a + 1 if some else min(z, s["e"])
        with s["cv"]:
            while s["s"] + s["got"] < need and not s["over"]:
                s["cv"].wait(1)
            have = s["s"] + s["got"]
        if have >= need:
            return bytes(s["buf"][a - s["s"]:min(z, have) - s["s"]])
        if s["dropped"]:
            raise Dropped(f"the source's bytes from {a}: no longer wanted")
    b = a // SRC_BLOCK
    with hls_lock:
        tail = src_tails.get((key, b))
    if tail is not None and b * SRC_BLOCK + tail[0] <= a:
        return tail[1][a - b * SRC_BLOCK - tail[0]:z - b * SRC_BLOCK - tail[0]]
    return None


def drop_reads(key, head, span):




    f = films.get(key) or {}
    quiet = not piece_has(key + ORIG if f.get("variant") == "orig" else key, head)
    with hls_lock:
        far = [run for run in runs.values() if run["key"] == key and ((quiet and run["bg"]) or run["n"] is not None and not head - 2 <= run["n"] <= head + span)]
    for run in far:
        end_call(run)


def probe_path(key):
    return f"{SEG_DIR}/{key}/probe.json"


def probe_kept(key):

    try:
        with open(probe_path(key)) as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return None
    return info if info.get("v") == PROBE_V else None


def probe_keep(key, info):

    os.makedirs(f"{SEG_DIR}/{key}", exist_ok=True)
    with open(probe_path(key) + ".new", "w") as fh:
        json.dump({**info, "v": PROBE_V}, fh)
    os.replace(probe_path(key) + ".new", probe_path(key))


def probe_source(key):




    for _ in range(2):
        if key in probed:
            return probed[key]
        once("probe", key, 0, lambda: probe_now(key))
    if key not in probed:
        raise RuntimeError("the source couldn't be probed")
    return probed[key]


def lookup_begin(key):




    with hls_lock:
        out = src_meta[key].get("finding")
        if out is not None and not out["known"].is_set():
            return out
        out = src_meta[key]["finding"] = {"known": threading.Event(), "at": time.time()}
    threading.Thread(target=lookup_now, args=(key, out), daemon=True).start()
    return out


def lookup_now(key, out):





    known = out.setdefault("known", threading.Event())
    try:
        for _ in range(LOOKUP_TRIES):
            try:
                src_lookup(key)
                out.pop("err", None)
                return
            except (http.client.HTTPException, OSError) as err:
                out["err"] = err
                if isinstance(err, TimeoutError):
                    return
            except Exception as err:
                out["err"] = err
                return
    finally:
        if not known.is_set():
            known.at = time.time()
            known.set()


def probe_blocks(key, kf=None):





    size, light = src_meta[key].get("size") or 0, key in facts_gentle
    if not size or (kf is not None and light):
        return
    last = (size - 1) // SRC_BLOCK
    if kf is None:
        want = [last] + ([] if light else [1, 2])
    else:
        end = next((p for t, p, _ in kf if t >= kf[0][0] + 3 * PIECE_S), size) + 2**16
        want = range(kf[0][1] // SRC_BLOCK, min(end // SRC_BLOCK, kf[0][1] // SRC_BLOCK + 3) + 1)
    for b in want:
        if 0 < b <= last and wanted_block(key, b):
            threading.Thread(target=quietly, args=(src_block, key, b, True, None), daemon=True).start()


def probe_now(key):





    t0 = time.time()
    info, how = probe_kept(key), "from disk"
    found = lookup_begin(key)
    if info is None:
        how = "from a copy"
        _, info = ask_copies(key, src_meta[key]["token"])
    if info is not None:
        probed[key] = info
        src_meta[key].update(duration=info["duration"], start=info["start"])
        if how != "from disk":
            probe_keep(key, info)
        log(f"hls {key}: probe {how} in {time.time() - t0:.1f}s ({len(info['cuts']) - 1} pieces)")
        return info
    found["known"].wait()
    if not src_meta[key]["url"] or time.time() - src_meta[key]["t"] >= 1200:
        raise RuntimeError(f"gdrive can't get this file right now ({found.get('err') or 'no answer'}): pick another version") from None
    t0 = min(t0, found["at"])
    t1 = time.time()
    probe_blocks(key)
    got = {}

    def index():
        for attempt in range(3):
            try:
                got["kf"] = mkv_keyframes(key)
                got["took"] = time.time() - t1
                probe_blocks(key, got["kf"])
                return
            except NoIndex as err:
                got["kf"] = None
                log(f"hls {key}: no keyframe index ({err}): pieces every {PIECE_S:g} s")
                return
            except SourceBroken as err:
                got["broken"] = str(err)
                return
            except Exception as err:
                got["err"] = f"the keyframe index: {err.__class__.__name__}: {err}"
                time.sleep(1 + attempt)
    t = threading.Thread(target=index, daemon=True)
    t.start()
    t2 = time.time()
    out = subprocess.run(["ffprobe", "-v", "error", "-headers", f"X-Tor-Key: {CHUNK_KEY}\r\n", "-show_entries",
                          "format=duration,size,start_time,format_name:stream=index,codec_type,codec_name,codec_tag_string,profile,level,width,height,pix_fmt,"
                          "color_transfer,r_frame_rate,channels:stream_tags=language,title:stream_side_data=side_data_type,dv_profile",
                          "-of", "json", f"http://127.0.0.1:{PAGE_PORT}/src/{key}?u=1"], capture_output=True, timeout=120)
    took = time.time() - t2
    t.join(120)
    info = json.loads(out.stdout or b"{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = info.get("format", {})
    if not video or not fmt.get("duration"):
        raise RuntimeError(f"ffprobe: {out.stderr.decode(errors='replace')[-300:]}")
    eng = next((s for s in audio if (s.get("tags") or {}).get("language") == "eng"), audio[0] if audio else None)
    kinds = {"truehd": "TrueHD", "dts": "DTS", "eac3": "Dolby Digital Plus", "ac3": "Dolby Digital", "aac": "AAC", "flac": "FLAC",
             "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis"}
    audios = [{"i": s["index"], "lang": (s.get("tags") or {}).get("language"), "title": ((s.get("tags") or {}).get("title") or "").strip()[:80],
               "kind": s.get("profile") if s.get("codec_name") == "dts" and s.get("profile") else
               kinds.get(s.get("codec_name"), "PCM" if (s.get("codec_name") or "").startswith("pcm") else (s.get("codec_name") or "").upper()),
               "ch": int(s.get("channels") or 0)} for s in audio]
    duration = float(fmt["duration"])
    if got.get("broken"):
        raise SourceBroken(got["broken"])
    if "matroska" in fmt.get("format_name", "") and "kf" not in got:
        raise RuntimeError(got.get("err") or "the keyframe index wasn't read")
    kf = got.get("kf") if "matroska" in fmt.get("format_name", "") else None
    cuts, pos, aligned, pend = piece_cuts(kf, float(fmt.get("start_time") or 0), duration)
    ocuts, opos, oaligned = keyframe_cuts(kf, float(fmt.get("start_time") or 0), duration) if kf else (None, None, None)

    start = float(fmt.get("start_time") or 0)
    fps = video.get("r_frame_rate") or "0/1"
    dv = next((d.get("dv_profile") for d in video.get("side_data_list") or [] if "dv_profile" in d), None)
    probed[key] = {"duration": duration, "size": int(fmt.get("size") or 0), "a": eng["index"] if eng else -1, "audios": audios,
                   "hdr": int(video.get("color_transfer") in ("smpte2084", "arib-std-b67")), "w": int(video.get("width") or 0),
                   "start": start, "cuts": cuts, "pos": pos, "aligned": aligned, "pend": pend, "ocuts": ocuts, "opos": opos, "oaligned": oaligned,
                   "h": int(video.get("height") or 0), "vcodec": video.get("codec_name"), "vprofile": video.get("profile") or "",
                   "vlevel": int(video.get("level") or 0), "range": {"smpte2084": "PQ", "arib-std-b67": "HLG"}.get(video.get("color_transfer"), "SDR"),
                   "fps": round(int(fps.split("/")[0]) / max(1, int(fps.split("/")[1])), 3), "dv": dv,
                   "hdrp": int(any("2094-40" in str(d.get("side_data_type") or "") for d in video.get("side_data_list") or [])),
                   "vtag": (video.get("codec_tag_string") or "")[:8]}
    src_meta[key].update(duration=duration, start=start)
    probe_keep(key, probed[key])
    facts_note(key, facts_of(probed[key]))
    threading.Thread(target=quietly, args=(facts_keep, key), daemon=True).start()
    log(f"hls {key}: probed in {time.time() - t0:.1f}s (the source's address {t1 - t0:.1f}s, ffprobe {took:.1f}s, "
        f"the keyframe index {got.get('took', 0):.1f}s; {len(cuts) - 1} pieces)")
    return probed[key]


def facts_of(info):





    w, h, dv = info.get("w") or 0, info.get("h") or 0, info.get("dv")
    res = "4K" if w >= 3000 or h >= 1600 else "1080p" if w >= 1800 or h >= 1000 else "720p" if w >= 1100 or h >= 650 else "SD"
    hdr = "HDR10+" if info.get("hdrp") else {"PQ": "HDR10", "HLG": "HLG"}.get(info.get("range"), "")
    if dv is not None:
        hdr = "Dolby Vision" + (" only" if dv == 5 else f" · {hdr}" if hdr else "")
    vc, audios = info.get("vcodec") or "", info.get("audios") or []
    main = next((x for x in audios if x["i"] == info.get("a")), audios[0] if audios else None)
    sound = ""
    if main:
        atmos = main["kind"] in ("TrueHD", "Dolby Digital Plus") and re.search(r"atmos", main.get("title") or "", re.I)
        sound = " ".join(filter(None, (main["kind"], "Atmos" if atmos else "", {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(main["ch"], f"{main['ch']} ch" if main["ch"] else ""))))
    names = []
    for x in ([main] if main else []) + audios:
        code = (x.get("lang") or "").lower()
        name = LANGS.get(code, (None, None))[1] or (code.title() if code and code != "und" else "")
        if name and name not in names:
            names.append(name)
    apple = vc in ("hevc", "h264") and info.get("vtag") != "dvhe" and all(x["kind"] in APPLE_SOUND for x in audios)
    return {"h": " ".join(filter(None, (res, hdr))), "v": {"hevc": "HEVC", "h264": "H.264", "av1": "AV1", "vc1": "VC-1", "mpeg2video": "MPEG-2", "vp9": "VP9"}.get(vc, vc.upper()),
            "s": sound, "l": ", ".join(names) if len(names) <= 2 else f"{names[0]} + {len(names) - 1} languages",
            "o": int(orig_unfit(info) is None), "a": int(apple), "c": "colours may be off" if dv == 5 else ""}


facts_mem = {}
facts_q = collections.deque()
facts_gentle = set()


def facts_note(key, f):
    if len(facts_mem) > 5000:
        for k in list(facts_mem)[:1000]:
            facts_mem.pop(k, None)
    facts_mem[key] = f
    return f


def facts_known(key):

    f = facts_mem.get(key)
    if f is None:
        info = probed.get(key) or probe_kept(key)
        if info:
            f = facts_note(key, facts_of(info))
    return f


def facts_keep(key):

    if not kv.down:
        kv.put(FACTS + key, json.dumps(facts_mem[key]).encode())


def facts_probe(key, token):

    with hls_lock:
        if key in facts_mem or key in probed or any(k == key for k, _, _ in facts_q):
            return
        facts_q.append((key, token, time.time()))


def facts_urgent(key):

    facts_gentle.discard(key)
    with turns:
        for (k, _), w in up_wait.items():
            if k == key and w.get("light"):
                w.update(light=False, urgent=True)
        turns.notify_all()


def facts_worker():



    while True:
        with hls_lock:
            item = facts_q.popleft() if facts_q else None
        if item is None:
            time.sleep(0.5)
            continue
        key, token, at = item
        if time.time() - at > FACTS_WAIT or facts_known(key):
            continue
        facts_gentle.add(key)
        try:
            open_token(token)
            if reader_of(key) in (None, REPLICA) and claim_reader(key) == REPLICA:
                src_meta[key]["warm"] = time.time()
            probe_source(key)
        except Exception as err:
            log(f"facts {key}: {err.__class__.__name__}: {err}")
        finally:
            facts_gentle.discard(key)


def facts_lookup(keys, ask=True, at=None):



    out = {}
    for k in keys:
        f = facts_known(k)
        if f:
            out[k] = f
    left = [k for k in keys if k not in out]
    if not left or not ask:
        return out
    others = [k for k in copies() if k != REPLICA and (at is None or k == at)]
    done, pending = threading.Event(), [len(others)]

    def one(k):
        try:
            st, _, body = conns.request("GET", f"{url_of(k)}/facts?k={','.join(left)}", headers={"X-Tor-Key": CHUNK_KEY}, timeout=3)
            if st == 200:
                for key, f in json.loads(body).items():
                    if key in left and isinstance(f, dict):
                        out[key] = facts_note(key, f)
        except Exception:
            pass
        with hls_lock:
            pending[0] -= 1
            if not pending[0]:
                done.set()
    for k in others:
        threading.Thread(target=one, args=(k,), daemon=True).start()
    if others:
        done.wait(2.5)
    left = [k for k in left if k not in out]
    if left and not kv.down:
        def record(k):
            try:
                got = kv.get(FACTS + k)
                if got:
                    out[k] = facts_note(k, json.loads(got[0]))
            except Exception as err:
                kv_quiet(f"reading {k}'s facts: {err}")
        threads = [threading.Thread(target=record, args=(k,), daemon=True) for k in left[:12]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(3)
    return out


def src_head(key, end, piece):


    m = src_meta[key]
    head = m.get("head")
    if head is None:
        head = src_read(key, 0, end, piece)
        if end <= 32 * 2**20:
            m["head"] = head
    return head


def src_read(key, start, n, piece=None):


    size = src_meta[key].get("size") or src_lookup(key)["size"]
    end, pos, out = min(start + n, size), start, []
    land_now(key, piece)
    while pos < end:
        b = pos // SRC_BLOCK
        part = land_part(key, pos, min(end, (b + 1) * SRC_BLOCK))
        if part is not None:
            out.append(part)
            pos += len(part)
            continue
        for nb in range(b + 1, (end - 1) // SRC_BLOCK + 1):
            if wanted_block(key, nb):
                threading.Thread(target=quietly, args=(src_block, key, nb, True, piece), daemon=True).start()
        part = src_block(key, b, True, piece)[pos - b * SRC_BLOCK:end - b * SRC_BLOCK]
        if not part:
            break
        out.append(part)
        pos += len(part)
    return b"".join(out)


def ebml_vint(buf, p, marker):


    first, n, mask = buf[p], 1, 0x80
    while n <= 8 and not first & mask:
        mask, n = mask >> 1, n + 1
    v = first if marker else first & (mask - 1)
    for i in range(1, n):
        v = v << 8 | buf[p + i]
    return v, n, not marker and v == (1 << 7 * n) - 1


def ebml(buf, pos, end):

    while pos < end - 1:
        eid, a, _ = ebml_vint(buf, pos, True)
        size, b, unknown = ebml_vint(buf, pos + a, False)
        data = pos + a + b
        yield eid, data, end if unknown else min(end, data + size)
        if unknown:
            return
        pos = data + size


class NoIndex(Exception):
    """A Matroska file without the keyframe index (Cues) the cuts come from."""


class SourceBroken(Exception):
    """A source that can't play: a file TorBox has only part of, say."""


def mkv_keyframes(key):


    head = src_read(key, 0, 256 * 1024)
    seg = next((a for eid, a, _ in ebml(head, 0, len(head)) if eid == 0x18538067), None)
    if seg is None:
        raise NoIndex("not a Matroska file")
    where, heads, seen = {}, [seg], set()
    while heads:
        at = heads.pop()
        if at in seen or len(seen) > 4:
            continue
        seen.add(at)
        buf, off = (head, at) if at + 65536 <= len(head) else (src_read(key, at, 65536), 0)
        for eid, a, b in ebml(buf, off, len(buf)):
            if eid == 0x1F43B675:
                break
            if eid != 0x114D9B74:
                continue
            for sid, sa, sb in ebml(buf, a, b):
                if sid == 0x4DBB:
                    target = p = None
                    for x, xa, xb in ebml(buf, sa, sb):
                        if x == 0x53AB:
                            target = int.from_bytes(buf[xa:xb], "big")
                        elif x == 0x53AC:
                            p = seg + int.from_bytes(buf[xa:xb], "big")
                    if target == 0x114D9B74 and p is not None:
                        heads.append(p)
                    elif target is not None and p is not None:
                        where.setdefault(target, p)
            break

    def element(eid, cap=32 * 2**20):

        if eid not in where:
            raise NoIndex(f"no {eid:x} in the SeekHead")
        size = src_lookup(key)["size"]
        if where[eid] >= size:
            raise SourceBroken(f"the file is cut short: {size / 1e9:.1f} GB of it, its index at {where[eid] / 1e9:.1f} GB")
        hdr = src_read(key, where[eid], 16)
        _, a, _ = ebml_vint(hdr, 0, True)
        size, b, unknown = ebml_vint(hdr, a, False)
        return src_read(key, where[eid] + a + b, cap if unknown else min(size, cap))

    def uint(buf, a, b):
        return int.from_bytes(buf[a:b], "big")
    info = element(0x1549A966, 65536)
    scale = next((uint(info, a, b) for eid, a, b in ebml(info, 0, len(info)) if eid == 0x2AD7B1), 1000000)
    tracks = element(0x1654AE6B, 2**20)
    video = None
    for eid, a, b in ebml(tracks, 0, len(tracks)):
        if eid == 0xAE:
            f = {x: uint(tracks, xa, xb) for x, xa, xb in ebml(tracks, a, b) if x in (0xD7, 0x83)}
            if f.get(0x83) == 1:
                video = f.get(0xD7)
                break
    cues = element(0x1C53BB6B)
    kf = []
    for eid, a, b in ebml(cues, 0, len(cues)):
        if eid != 0xBB:
            continue
        t, places = None, []
        for x, xa, xb in ebml(cues, a, b):
            if x == 0xB3:
                t = uint(cues, xa, xb)
            elif x == 0xB7:
                f = {y: uint(cues, ya, yb) for y, ya, yb in ebml(cues, xa, xb) if y in (0xF7, 0xF1, 0xF0)}
                places.append(f)
        hit = next((f for f in places if f.get(0xF7) == video and 0xF1 in f), None)
        if t is not None and hit:
            kf.append((t * scale / 1e9, seg + hit[0xF1], hit.get(0xF0)))
    if len(kf) < 2:
        raise NoIndex(f"{len(kf)} keyframes in the index")
    return sorted(kf)


def keyframe_cuts(kf, start, duration):


    kf = [(t - start, p, r) for t, p, r in kf if 0 <= t - start < duration]
    if not kf or kf[0][0] >= 0.5:
        return None, None, None
    cuts, pos, aligned = [0.0], [kf[0][1]], [kf[0][2] is not None and kf[0][2] < 64]
    for t, p, r in kf[1:]:
        if t - cuts[-1] >= PIECE_S - 0.05:
            cuts.append(round(t, 3))
            pos.append(p)
            aligned.append(r is not None and r < 64)
    if duration - cuts[-1] < 0.5 and len(cuts) > 1:
        cuts.pop()
        pos.pop()
        aligned.pop()
    return cuts + [duration], pos, aligned


def piece_cuts(kf, start, duration):








    near = sorted((t - start, p, r) for t, p, r in kf if 0 <= t - start < duration) if kf else []
    if near:
        gaps = sorted(b[0] - a[0] for a, b in zip(near, near[1:]))
        if gaps and gaps[len(gaps) // 2] <= 2 * PIECE_S:
            cuts, pos, aligned = keyframe_cuts(kf, start, duration)
            if cuts:
                return cuts, pos, aligned, None
    n = max(1, round(duration / PIECE_S))
    cuts = [round(i * PIECE_S, 3) for i in range(n)] + [duration]
    if not near:
        return cuts, None, None, None
    pos, aligned, pend, j, e = [], [], [], 0, 0
    for i in range(n):
        while j + 1 < len(near) and near[j + 1][0] <= cuts[i] + 0.002:
            j += 1
        while e < len(near) and near[e][0] < cuts[i + 1] - 0.002:
            e += 1
        t, p, r = near[j]
        pos.append(p)
        aligned.append(r is not None and r < 64 and abs(t - cuts[i]) <= 0.002)
        if e >= len(near):
            pend.append(None)
        elif near[e][2] is not None and near[e][2] < 64:
            pend.append(near[e][1])
        elif e + 1 < len(near):

            span_b, span_t = near[e + 1][1] - near[e][1], near[e + 1][0] - near[e][0]
            pend.append(near[e][1] + min(span_b, int(span_b * 1.5 / span_t) if span_t > 0 else span_b) + 2**16)
        else:
            pend.append(None)
    return cuts, pos, aligned, pend


def piece_span(key, m, orig=False):



    info = probed[key]
    pos, aligned = (info["opos"], info.get("oaligned") or []) if orig else (info["pos"], info.get("aligned") or [])
    size = src_meta[key].get("size") or src_lookup(key)["size"]
    pend = None if orig else info.get("pend")
    if pend:
        e = pend[m] if m < len(pend) else None
        return pos[m], e if e is not None and e > pos[m] else min(size, pos[m] + 96 * 2**20), True
    if m + 1 >= len(pos):
        return pos[m], min(size, pos[m] + 96 * 2**20), False
    if m + 1 < len(aligned) and aligned[m + 1]:
        return pos[m], pos[m + 1], False
    return pos[m], pos[m + 2] if m + 2 < len(pos) else min(size, pos[m + 1] + 32 * 2**20), True


def piece_cut(key, m, orig=False):


    info = probed[key]
    cuts, pos = (info["ocuts"], info["opos"]) if orig else (info["cuts"], info["pos"])
    return (cuts[m], round(cuts[m + 1] - cuts[m], 3), pos[m] if pos else None,
            pos[m + 1] if pos and m + 1 < len(pos) else None)


def muxed(info):

    return info["a"] if MUXED else -1


def film_q(key):


    info = probed[key]
    return f"a={muxed(info)}&h={info['hdr']}&w={info['w']}&r={REPLICA}&z={info['size']}&d={info['duration']:.3f}&s={info['start']:.3f}"


def piece_q(key, m):


    t, l, pb, pe = piece_cut(key, m)
    return f"{film_q(key)}&t={t:.3f}&l={l:.3f}" + ("" if pb is None else f"&pb={pb}") + ("" if pe is None else f"&pe={pe}")


def when_asked(query):

    try:
        return float((query.get("t") or ["0"])[0])
    except ValueError:
        return 0


def query_cut(query):

    try:
        t, l = float(query["t"][0]), float(query["l"][0])
    except (KeyError, ValueError):
        return None
    pb, pe = ((query.get(k) or [""])[0] for k in ("pb", "pe"))
    return t, l, int(pb) if pb.isdigit() else None, int(pe) if pe.isdigit() else None




def piece_path(key, n):
    return f"{SEG_DIR}/{key}/{n}"


def piece_load():

    found = []
    for key in os.listdir(SEG_DIR) if os.path.isdir(SEG_DIR) else []:
        for name in os.listdir(f"{SEG_DIR}/{key}"):
            if name.isdigit():
                st = os.stat(f"{SEG_DIR}/{key}/{name}")
                found.append((st.st_mtime, key, int(name), st.st_size))
    for _, key, n, size in sorted(found):
        pieces[(key, n)] = size
        pieces_size["n"] += size


def piece_get(key, n):
    with hls_lock:
        data = mem_pieces.get((key, n))
        if data is not None:
            mem_pieces.move_to_end((key, n))
            return data
        if (key, n) not in pieces:
            return None
        pieces.move_to_end((key, n))
    try:
        with open(piece_path(key, n), "rb") as fh:
            return fh.read()
    except OSError:
        with hls_lock:
            pieces_size["n"] -= pieces.pop((key, n), 0)
        return None


def piece_has(key, n):

    with hls_lock:
        data = mem_pieces.get((key, n))
        return len(data) if data is not None else pieces.get((key, n), 0)


def piece_put(key, n, data):
    if src_meta.get(key.split(":")[0], {}).get("reader") == REPLICA:

        with hls_lock:
            old = mem_pieces.pop((key, n), None)
            mem_size["n"] += len(data) - (len(old) if old is not None else 0)
            mem_pieces[(key, n)] = data
            while mem_size["n"] > RAM_PIECES and mem_pieces:
                mem_size["n"] -= len(mem_pieces.popitem(last=False)[1])
    os.makedirs(f"{SEG_DIR}/{key}", exist_ok=True)
    with open(piece_path(key, n) + ".new", "wb") as fh:
        fh.write(data)
    os.replace(piece_path(key, n) + ".new", piece_path(key, n))
    gone = []
    with hls_lock:
        pieces_size["n"] += len(data) - pieces.pop((key, n), 0)
        pieces[(key, n)] = len(data)
        while pieces_size["n"] > SEG_DISK and len(pieces) > 1:
            (k, m), size = pieces.popitem(last=False)
            pieces_size["n"] -= size
            gone.append((k, m))
    with made:
        made.notify_all()
    for k, m in gone:
        try:
            os.remove(piece_path(k, m))
        except OSError:
            pass


made = threading.Condition()


def piece_wait(key, n, wait):


    end = time.time() + wait
    with made:
        while not piece_has(key, n):
            if time.time() >= end:
                return None
            made.wait(end - time.time())
    return piece_get(key, n)


class Dropped(RuntimeError):
    """A piece no longer wanted: its player went elsewhere (see drop_far)."""





cpu_cond = threading.Condition()
cpu_jobs = {}



def job_rank(item):
    (_, n), j = item
    return (j["cls"], -j["t"], n)


def sched(pid, best):

    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return
    for tid in tids:
        try:
            os.sched_setscheduler(int(tid), os.SCHED_OTHER if best else os.SCHED_IDLE, os.sched_param(0))
        except OSError:
            pass


def cpu_arrange():


    started = sorted((item for item in cpu_jobs.items() if item[1]["p"]), key=job_rank)
    for i, (_, j) in enumerate(started):
        best = i == 0
        if j["best"] != best:
            j["best"] = best
            sched(j["p"].pid, best)


def drop_far(key, head, span):





    with cpu_cond:
        for (k, n), j in list(cpu_jobs.items()):
            if k == key and j["cls"] == 0 and not head - 2 <= n <= head + HLS_AHEAD:
                j["dropped"] = True
                if j["p"]:
                    try:
                        j["p"].kill()
                    except OSError:
                        pass
        cpu_cond.notify_all()
    with mac_lock:
        for (k, n), job in list(mac_jobs.items()):
            if k == key and not job["dropped"] and not head - 2 <= n <= head + (HLS_AHEAD if job["cls"] == 0 else span):
                job["dropped"] = True
                job["news"].put(None)
    drop_reads(key, head, span)


def cpu_drop(key, n):

    with cpu_cond:
        j = cpu_jobs.get((key, n))
        if j:
            j["dropped"] = True
            if j["p"]:
                try:
                    j["p"].kill()
                except OSError:
                    pass
        cpu_cond.notify_all()


def cpu_hurry(key, n, cls):

    with cpu_cond:
        j = cpu_jobs.get((key, n))
        if j and cls < j["cls"]:
            j.update(cls=cls, t=time.time())
            cpu_arrange()
            cpu_cond.notify_all()


def piece_urgent(key, n, default):

    j = cpu_jobs.get((key, n))
    return j["cls"] < 2 if j else default


def run_ffmpeg(cmd, key, n, cls):



    with cpu_cond:
        j = cpu_jobs[(key, n)] = {"cls": cls, "t": time.time(), "p": None, "best": None, "dropped": False}

        def turn():
            if j["cls"] == 0:
                running = [x for x in cpu_jobs.values() if x["p"] and x["cls"] == 0]
                waiting = [it for it in cpu_jobs.items() if not it[1]["p"] and it[1]["cls"] == 0]
                return len(running) < PLAYER_RUNS and min(waiting, key=job_rank)[1] is j
            return j["cls"] == 1 or (sum(1 for x in cpu_jobs.values() if x["p"]) < FFMPEG_MAX
                                     and min((it for it in cpu_jobs.items() if not it[1]["p"]), key=job_rank)[1] is j)
        while not j["dropped"] and not turn():
            cpu_cond.wait(1)
        if j["dropped"]:
            cpu_jobs.pop((key, n), None)
            cpu_cond.notify_all()
            raise Dropped(f"piece {n} dropped: its player went elsewhere, or it was made elsewhere first")
        p = j["p"] = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        started = time.time()
        cpu_arrange()
    got = {}

    def drain(name, f):
        got[name] = f.read()
        f.close()
    readers = [threading.Thread(target=drain, args=(name, f), daemon=True) for name, f in (("out", p.stdout), ("err", p.stderr))]
    for t in readers:
        t.start()
    timer = threading.Timer(300 if cls < 2 else 1800, p.kill)
    timer.start()
    try:
        _, status, usage = os.wait4(p.pid, 0)
    finally:
        timer.cancel()
        with cpu_cond:
            cpu_jobs.pop((key, n), None)
            cpu_arrange()
            cpu_cond.notify_all()
    p.returncode = os.waitstatus_to_exitcode(status)
    for t in readers:
        t.join()
    if j["dropped"]:
        raise Dropped(f"piece {n} dropped: its player went elsewhere, or it was made elsewhere first")
    return subprocess.CompletedProcess(cmd, p.returncode, got.get("out", b""), got.get("err", b"")), usage.ru_utime + usage.ru_stime, started


def piece_cmd(key, n, a, hdr, w, cls, t, end, pe, keys="0"):




    s0 = src_meta[key].get("start") or 0.0
    vf = [f"trim=start={s0 + t:.3f}:end={end:.3f}"] + (["scale=1920:-2"] if w > 1920 and not hdr else []) + \
         (["zscale=" + ("w=1920:h=-2:f=bicubic:" if w > 1920 else "") + "t=linear:npl=100:p=bt709", "format=gbrpf32le",
           "tonemap=tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv"] if hdr else []) + ["format=yuv420p"]




    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-headers", f"X-Tor-Key: {CHUNK_KEY}\r\n",
            "-noaccurate_seek", "-ss", f"{t + 0.135:.3f}", "-copyts",
            "-i", f"http://127.0.0.1:{PAGE_PORT}/src/{key}?u={int(cls < 2)}&n={n}" + ("" if pe is None else f"&pe={pe}"),
            "-map", "0:v:0"] + (["-map", f"0:{a}", "-af", f"atrim=start={s0 + t:.3f}:end={end:.3f}"] if a >= 0 else []) + \
           ["-vf", ",".join(vf)] + (X264 if keys == "0" else RUN_X264) + ENC_TB + \
           ["-force_key_frames", keys, "-c:a", "aac", "-ac", "2", "-b:a", "192k",
            "-output_ts_offset", f"{TS_BASE - s0:.3f}", "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", "pipe:1"]


def convert(key, n, a, hdr, w, cls, cut):



    t, l, pb, pe = cut
    s0 = src_meta[key].get("start") or 0.0
    data = piece_get(key, n)
    while data is None:
        cpu_hurry(key, in_run.get((key, n), n), cls)

        def make():
            fetch, t0 = [], time.time()
            if pb is not None:
                for b in range(pb // SRC_BLOCK, (pe if pe is not None else pb) // SRC_BLOCK + 1):
                    if wanted_block(key, b, cls < 2):
                        fetch.append(threading.Thread(target=quietly, args=(src_block, key, b, cls < 2, n), daemon=True))
                        fetch[-1].start()
            for th in fetch if cls == 2 else []:
                th.join(max(0, t0 + 60 - time.time()))
            cmd = piece_cmd(key, n, a, hdr, w, cls, t, s0 + t + l, pe)
            try:
                out, cpu, started = run_ffmpeg(cmd, key, n, cls)
            finally:
                wait = src_waits.pop((key, n), 0.0)
            if out.returncode or not out.stdout:
                raise RuntimeError(f"ffmpeg, piece {n} (exit {out.returncode}): {out.stderr.decode(errors='replace')[-300:]}")
            piece_put(key, n, out.stdout)
            took = time.time() - started
            bstats["pieces"].append((int(time.time()), n, cls, round(took, 1), round(cpu, 1), round(wait, 1)))
            if cls < 2 and took > 8:
                log(f"hls {key}: piece {n} took {took:.1f}s to convert ({cpu:.0f} s of CPU, {wait:.1f} s waiting on the source)")
            return out.stdout
        try:
            data = once("seg", key, n, make) or piece_get(key, n)
        except Dropped:
            data = piece_get(key, n)
            if data is None:
                raise
    return data


in_run = {}
runs_off = {}


def convert_run(key, ns, a, hdr, w, cuts, cls=2):






    s0 = src_meta[key].get("start") or 0.0
    while True:
        todo = [x for x in ns if not piece_has(key, x)]
        if not todo:
            return
        with hls_lock:
            mine = []
            for x in todo:
                if ("seg", key, x) in busy or (mine and x != mine[-1] + 1):
                    break
                mine.append(x)
            alone = len(mine) < 2 or a >= 0 or runs_off.get(key, 0) > time.time()
            if not alone:
                for x in mine:
                    busy[("seg", key, x)] = threading.Event()
                    in_run[(key, x)] = mine[0]
        if alone:
            convert(key, todo[0], a, hdr, w, cls, cuts[todo[0]])
            continue
        t, _, pb, pe = cuts[mine[0]]
        lt, ll = cuts[mine[-1]][:2]
        t0 = time.time()
        try:
            fetch = [threading.Thread(target=quietly, args=(src_block, key, b, cls < 2, mine[0]), daemon=True)
                     for b in range(pb // SRC_BLOCK, pe // SRC_BLOCK + 1) if wanted_block(key, b, cls < 2)]
            for th in fetch:
                th.start()
            for th in fetch if cls == 2 else []:
                th.join(max(0, t0 + 60 - time.time()))
            cmd = piece_cmd(key, mine[0], a, hdr, w, cls, t, s0 + lt + ll, pe, ",".join(f"{s0 + cuts[x][0]:.3f}" for x in mine))
            try:
                out, cpu, started = run_ffmpeg(cmd, key, mine[0], cls)
            finally:
                wait = src_waits.pop((key, mine[0]), 0.0)
            if out.returncode or not out.stdout:
                raise RuntimeError(f"ffmpeg, pieces {mine[0]}-{mine[-1]} (exit {out.returncode}): {out.stderr.decode(errors='replace')[-300:]}")
            try:
                parts = ts_pieces(out.stdout, len(mine), [round((cuts[x][0] + TS_BASE) * 90000) - 180 for x in mine[1:]])
            except RuntimeError as err:
                runs_off[key] = time.time() + 600
                log(f"hls {key}: pieces {mine[0]}-{mine[-1]} as one: {err}: a piece at a time for 10 minutes")
                continue
            for x, part in zip(mine, parts):
                piece_put(key, x, part)
            took = time.time() - started
            for x in mine:
                bstats["pieces"].append((int(time.time()), x, cls, round(took, 1), round(cpu / len(mine), 2), round(wait / len(mine), 1)))
        finally:
            with hls_lock:
                evs = [busy.pop(("seg", key, x), None) for x in mine]
                for x in mine:
                    in_run.pop((key, x), None)
            for ev in evs:
                if ev is not None:
                    ev.set()


def query_run(n, cut, query):

    last = int(query["run"][0])
    ts = [cut[0]] + [float(x) for x in query["ts"][0].split(",")]
    ls = [float(x) for x in query["ls"][0].split(",")]
    if cut[2] is None or not n < last < n + 100 or len(ts) != last - n + 1 or len(ls) != len(ts):
        raise ValueError("a run's run, ts, ls, pb")
    rb = int(query["rb"][0])
    return list(range(n, last + 1)), {x: (ts[x - n], ls[x - n], cut[2], rb) for x in range(n, last + 1)}


def run_q(key, run):


    cuts = [piece_cut(key, x)[:2] for x in run]
    return (f"&run={run[-1]}&ts=" + ",".join(f"{t:.3f}" for t, _ in cuts[1:]) + "&ls=" + ",".join(f"{l:.3f}" for _, l in cuts)
            + f"&rb={piece_span(key, run[-1])[1]}")


def ready_run(key, m):




    info, f = probed.get(key) or {}, films.get(key) or {}
    pos = info.get("pos") if info.get("pend") and (not MUXED or info.get("a", -1) < 0) else None
    run = [m]
    with hls_lock:
        taken, done = f.get("fill", {}).get("taken", {}), f.get("done", set())
        while pos and len(run) < RUN_MAX and run[-1] + 1 < len(pos) and pos[run[-1] + 1] == pos[m]:
            x = run[-1] + 1
            if x in done or x in taken or (key, x) in pieces or (key, x) in mem_pieces or (key, x) in mac_jobs:
                break
            run.append(x)
    return run


def where_keep(key, ns, owner):

    try:
        os.makedirs(f"{SEG_DIR}/{key}", exist_ok=True)
        with open(f"{SEG_DIR}/{key}/where", "a") as fh:
            fh.write("".join(f"{x} {owner} {int(time.time())}\n" for x in ns))
    except OSError:
        pass


def converters(key, reader):


    others = sorted(r for r in copies() if r != reader)
    k = int(key[:8], 16) % max(1, len(others))
    return (others[k:] + others[:k])[:CONVERTERS] or [reader]


def piece_owner(key, n, reader):




    f = films.get(key) or {}
    where = f.get("fill", {}).get("where", {}).get(n)
    if where is not None:
        return where
    owners = f.get("owners") or converters(key, reader)
    info = probed.get(key) or {}
    pos, g = info.get("pos") if info.get("pend") else None, n
    while pos and 0 < g < len(pos) and n - g < 100 and pos[g - 1] == pos[n]:
        g -= 1
    return owners[(g if pos else n // DEAL_RUN) % len(owners)]


CINEMETA = "https://v3-cinemeta.strem.io"


def cinemeta(path):
    req = urllib.request.Request(f"{CINEMETA}{path}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def title_search(q):

    found = {}

    def one(kind):
        try:
            found[kind] = cinemeta(f"/catalog/{kind}/top/search={urllib.parse.quote(q)}.json").get("metas", [])[:20]
        except Exception:
            found[kind] = []
    threads = [threading.Thread(target=one, args=(k,)) for k in ("movie", "series")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out = []
    for movie, show in itertools.zip_longest(found["movie"], found["series"]):
        for kind, m in (("movie", movie), ("series", show)):
            tid = m and (m.get("imdb_id") or m.get("id") or "")
            if tid and re.fullmatch(r"tt\d+", tid):
                out.append({"id": tid, "type": kind, "name": m.get("name") or tid, "year": m.get("releaseInfo") or m.get("year") or "",
                            "poster": m.get("poster") or ""})
    return out


def title_meta(kind, tid):

    m = cinemeta(f"/meta/{kind}/{tid}.json").get("meta") or {}
    out = {"name": m.get("name"), "year": m.get("releaseInfo") or m.get("year"), "poster": m.get("poster"),
           "overview": m.get("description") or "", "genres": m.get("genres") or []}
    if kind == "series":
        seasons = {}
        for v in m.get("videos") or []:
            s, e = v.get("season"), v.get("episode", v.get("number"))
            if s is None or e is None:
                continue
            seasons.setdefault(str(s), []).append({"episode": e, "name": v.get("name") or v.get("title") or "", "id": v.get("id") or f"{tid}:{s}:{e}"})
        for eps in seasons.values():
            eps.sort(key=lambda x: x["episode"])
        out["seasons"] = seasons
    return out




gw_cache = collections.OrderedDict()
gw_held = [0]
gw_lock = threading.Lock()
gw_fetching = {}
gw_slots = collections.defaultdict(lambda: threading.BoundedSemaphore(6))



def gw_fetch(url, old):



    headers = {"Accept-Encoding": "gzip"}
    if old and old.get("etag"):
        headers["If-None-Match"] = old["etag"]
    for hop in range(4):
        for attempt in range(3):
            try:
                with gw_slots[urllib.parse.urlsplit(url).netloc]:
                    st, h, body = conns.request("GET", url, headers=headers, timeout=20)
                break
            except (http.client.HTTPException, OSError):
                if attempt == 2:
                    raise
                time.sleep(0.3 * (attempt + 1))
        if st not in (301, 302, 303, 307, 308) or not h.get("Location"):
            break
        url = urllib.parse.urljoin(url, h.get("Location"))
        headers.pop("If-None-Match", None)
    if st == 304 and old:
        return None
    if st >= 500 or st in (301, 302, 303, 307, 308, 429):
        raise RuntimeError(f"HTTP {st}")
    if len(body) > 32 << 20:
        raise RuntimeError(f"{len(body) >> 20} MB: too big")
    enc = (h.get("Content-Encoding") or "identity").lower()
    if enc not in ("gzip", "identity"):
        raise RuntimeError(f"sent as {enc}")
    ctype = h.get("Content-Type") or mimetypes.guess_type(url)[0] or "application/octet-stream"
    gz = enc == "gzip"
    if not gz and len(body) > 1024 and re.match(r"(text/|application/(json|javascript|xml))", ctype):
        body, gz = gzip.compress(body, 6), True
    m = re.search(r"max-age=(\d+)", h.get("Cache-Control") or "")
    ttl = min(max(int(m.group(1)) if m else 3600, 300), 30 * 86400) if st == 200 else 600
    return {"status": st, "type": ctype, "body": body, "gz": gz, "etag": h.get("ETag") or f'"{hashlib.sha256(body).hexdigest()[:24]}"',
            "ttl": ttl, "until": time.time() + ttl, "size": len(body)}


def gateway(host, path, query):





    if host.endswith(".metahub.space"):
        path = re.sub(r"/img\.(jpe?g|png)\Z", "/img", path)
    key = f"{host}/{path}" + (f"?{query}" if query else "")
    with gw_lock:
        old = gw_cache.get(key)
        if old:
            gw_cache.move_to_end(key)
            if time.time() < old["until"]:
                return old
        ev = gw_fetching.get(key)
        mine = ev is None
        if mine:
            ev = gw_fetching[key] = threading.Event()
    if not mine:
        ev.wait(25)
        with gw_lock:
            got = gw_cache.get(key)
        if not got:
            raise RuntimeError("not fetched")
        return got
    try:
        try:
            got = gw_fetch(f"https://{key}", old)
        except Exception as err:
            if not old:
                raise
            log(f"gateway {key}: {err.__class__.__name__}: {err}: its old answer")
            with gw_lock:
                old["until"] = time.time() + 60
            return old
        with gw_lock:
            if got is None:
                old["until"] = time.time() + old["ttl"]
                return old
            if key in gw_cache:
                gw_held[0] -= gw_cache[key]["size"]
            gw_cache[key] = got
            gw_cache.move_to_end(key)
            gw_held[0] += got["size"]
            while gw_held[0] > GW_RAM and len(gw_cache) > 1:
                gw_held[0] -= gw_cache.popitem(last=False)[1]["size"]
        return got
    finally:
        with gw_lock:
            gw_fetching.pop(key, None)
        ev.set()


def browser_streams(kind, sid, base, as_is=False):




    req = urllib.request.Request(f"{GDRIVE}/stream/{kind}/{sid}.json", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        found = json.loads(r.read()).get("streams", [])
    groups = {k: [] for k, _ in BROWSER_GROUPS}
    rest, plain = [], []

    def first_line(s):
        return (s.get("title") or s.get("description") or "").split("\n")[0]

    def pack(s):
        return kind == "series" and not EPISODE.search(first_line(s)) and not re.search(r"\.(mkv|mp4|avi|m2ts|ts|webm)\Z", first_line(s).strip(), re.I)
    for s in found:
        label = " ".join(str(s.get(k) or "") for k in ("name", "title", "description"))
        if not s.get("url"):
            continue
        size = re.search(r"([\d.]+) ?([GM])B", label)
        gb = float(size.group(1)) / (1 if size.group(2) == "G" else 1000) if size else 0.0
        if not NOT_FOR_BROWSERS.search(label):
            plain.append((gb, s))
            continue
        res = "4k" if re.search(r"2160p|\b4k\b|\buhd\b", label, re.I) and not re.search(r"1080p", label, re.I) else \
            "1080" if re.search(r"1080p", label, re.I) else None
        if res and not NOT_WORTH_IT.search(label):
            heavy = re.search(r"remux", label, re.I) or gb >= (50 if res == "4k" else 30)
            groups[res + (" remux" if heavy else "")].append((gb, s))
        else:
            rest.append((gb, s))

    def ident(s, gb):
        return "|".join((str((s.get("behaviorHints") or {}).get("bingeGroup", "")), first_line(s), f"{gb:.2f}") + ((sid,) if pack(s) else ()))

    def token(s, gb):
        return hls_token(s["url"], ident(s, gb))

    def key_of(s, gb):
        return src_key(s["url"], ident(s, gb))

    def file_of(s, gb, what):
        name = (s.get("behaviorHints") or {}).get("filename") or first_line(s)
        return f"{base}/{what}/{token(s, gb)}/{urllib.parse.quote(uncached(name), safe='')}"

    def entry(s, gb, kind_label, hint, url):
        first = first_line(s)
        orig = url.endswith("/orig.m3u8")
        key = key_of(s, gb)
        f = facts_known(key)
        if f and (orig or "/hls/" not in url):
            kind_label, hint = f["h"], " · ".join(filter(None, (f["v"], f["s"], f["l"])))
        elif f:
            kind_label = "from " + f["h"]
        e = {"name": f"🎞 Original\n{kind_label}" if orig else f"🌐 Browser 1080p\n{kind_label}" if "/hls/" in url else f"▶ As is\n{kind_label}",
             "title": f"{first}\n💾 {gb:.1f} GB" + (" (the season's pack)" if pack(s) else "") + f" · {hint}\n" + ("original video without re-encoding; repackaged with lossless audio for browser playback" if orig else
                                                              "converted by the fleet as it plays" if "/hls/" in url else "plays as it is, not converted"),
             "url": url, "download": file_of(s, gb, "dl"), "behaviorHints": {"bingeGroup": "fleet-browser|" + str((s.get("behaviorHints") or {}).get("bingeGroup", ""))}, "k": key}
        if f:
            e["f"] = f
        return e

    def converted(s, gb, as_it_is=False):
        return f"{base}/hls/{token(s, gb)}/" + ("orig.m3u8" if as_it_is else "index.m3u8")
    def quality(s):
        label = " ".join(str(s.get(k) or "") for k in ("name", "title", "description"))
        return next((i for i, p in enumerate((r"remux", r"blu-?ray|\bbd(rip)?\b|\bbrrip\b|\buhd\b", r"web-?dl|\bweb\b(?!-?rip)", r"web-?rip",
                                               r"hdtv|dvd")) if re.search(p, label, re.I)), 5)

    def place(section, gb, s):

        return (section, quality(s), pack(s), -gb)
    out = []
    for group, (what, hint) in BROWSER_GROUPS:
        for gb, s in groups[group]:
            out.append((place(2, gb, s), entry(s, gb, what, hint, converted(s, gb))))
            label = " ".join(str(s.get(k) or "") for k in ("name", "title", "description"))
            name = (s.get("behaviorHints") or {}).get("filename") or first_line(s).strip()
            if NOT_MATROSKA.search(name):
                if as_is and APPLE_AS_IS.search(name) and not NOT_FOR_APPLE.search(label):
                    e = entry(s, gb, what.replace("from ", ""), "the original file, in Apple's own player", file_of(s, gb, "file"))
                    e["apple"] = True
                    if e.get("f", {}).get("a", 1):
                        out.append((place(1, gb, s), e))
            elif not NOT_AS_IT_IS.search(label):
                e = entry(s, gb, what.replace("from ", ""), "as on the source, nothing lost: " + ("4K HDR, " if "4K" in what else "") + "lossless audio", converted(s, gb, True))
                if e.get("f", {}).get("o", 1):
                    out.append((place(0, gb, s), e))
    for gb, s in rest:
        label = " ".join(str(s.get(k) or "") for k in ("name", "title", "description"))
        res = next((r for r, p in (("720p", r"720p"), ("SD", r"576p|480p|\bdvd"), ("4K", r"2160p|\b4k\b"), ("1080p", r"1080p")) if re.search(p, label, re.I)), "other")
        out.append((place(2, gb, s), entry(s, gb, f"from {res}", "converted", converted(s, gb))))
    if as_is:
        for gb, s in plain:
            label = " ".join(str(s.get(k) or "") for k in ("name", "title", "description"))
            res = next((r for r, p in (("4K", r"2160p|\b4k\b"), ("1080p", r"1080p"), ("720p", r"720p")) if re.search(p, label, re.I)), "")
            out.append((place(1, gb, s), entry(s, gb, " ".join(x for x in (res, "plays in a browser") if x), "the original file", file_of(s, gb, "file"))))
    out = [e for _, e in sorted(out, key=lambda x: x[0])]
    out = out[:BROWSER_MAX]
    warm = {}


    for group, _ in BROWSER_GROUPS:
        for gb, s in sorted(groups[group], key=lambda x: -x[0])[:1]:
            warm.setdefault(token(s, gb), key_of(s, gb))
    for tok in list(warm)[:WARM_FIRST]:
        threading.Thread(target=quietly, args=(warm_film, tok), daemon=True).start()
    soon = set(list(warm.values())[:WARM_FIRST])

    def first_of(entries, n):
        keys = {}
        for e in entries:
            if "f" not in e and (e["k"] in keys or len(keys) < n):
                keys.setdefault(e["k"], e["url"].split("/")[-2])
        return keys
    origs = [e for e in out if e["name"].startswith("🎞")]
    want = first_of(origs, 1)
    want.update(first_of([e for e in out if e.get("apple")], 1))
    for key, tok in first_of(origs, FACTS_FIRST).items():
        want.setdefault(key, tok)
    for key, tok in want.items():
        if key not in soon:
            soon.add(key)
            facts_probe(key, tok)
    near = {}
    for e in out:
        if e["k"] not in soon and len(near) < WARM_NEXT:
            near.setdefault(e["k"], e["url"].split("/")[-2])
    for tok in near.values():
        threading.Thread(target=quietly, args=(warm_film, tok, False), daemon=True).start()
    for e in out:
        if "f" not in e and e["k"] in soon:
            e["soon"] = 1
    return out


reader_known = {}
asked = {}
kv_said = {"at": 0}


def kv_quiet(msg):


    if not kv.down and time.time() - kv_said["at"] > 60:
        kv_said["at"] = time.time()
        log(f"reader: the store: {msg}")


def reader_record(key):


    got = kv.get(READER + key)
    if not got:
        return None, None
    try:
        return json.loads(unseal(got[0])), got[1]
    except Exception:
        return None, got[1]


def reader_named(rec):

    if rec and rec.get("replica") in copies() and time.time() - rec.get("t", 0) < READER_TTL:
        return rec["replica"]
    return None


def reader_of(key):




    at, k = reader_known.get(key, (0, None))
    if time.time() - at < (FILM_IDLE if k == REPLICA else 5):
        return k
    try:
        k = reader_named(reader_record(key)[0])
    except Exception as err:
        kv_quiet(f"reading {key}'s record: {err}")
        k, _ = ask_copies(key, src_meta[key]["token"])
    reader_known[key] = (time.time(), k)
    return k


def claim_reader(key):



    try:
        for _ in range(2):
            rec, version = reader_record(key)
            other = reader_named(rec)
            if other is not None and other != REPLICA:
                reader_known[key] = (time.time(), other)
                return other
            me = seal(json.dumps({"replica": REPLICA, "id": BOOT, "t": time.time()}).encode())
            if kv.put(READER + key, me, NEW if version is None else version) is not None:
                break
    except Exception as err:
        kv_quiet(f"claiming {key}: {err}")
    reader_known[key] = (time.time(), REPLICA)
    if key in src_meta:
        src_meta[key]["warm"] = time.time()
    return REPLICA


def sweep_readers():


    now = time.time() * 1000
    for it in kv.listing():
        old = now - (it.get("modified") or 0)
        if (it["key"].startswith(READER) and old > READER_TTL * 1000) or (it["key"].startswith(FACTS) and old > FACTS_TTL * 1000):
            kv.delete(it["key"], it["version"])


def ask_copies(key, token):


    at, reader, info = asked.get(key, (0, None, None))
    if time.time() - at < 10:
        return reader, info
    others = [k for k in copies() if k != REPLICA]
    got, left, done, most = [], [len(others)], threading.Event(), {}

    def ask(k):
        try:
            st, _, body = conns.request("GET", f"{url_of(k)}/hls/{token}/probe", headers={"X-Tor-Key": CHUNK_KEY}, timeout=3)
            if st == 200:
                ans = json.loads(gzip.decompress(body))
                got.append((k, ans))
                if ans.get("reader"):
                    done.set()
        except Exception:
            pass
        with hls_lock:
            left[0] -= 1
            if not left[0]:
                done.set()
            elif left[0] <= len(others) // 10:
                most.setdefault("t", time.time())
    for k in others:
        threading.Thread(target=ask, args=(k,), daemon=True).start()
    end = time.time() + 3
    while others and not done.wait(0.05) and time.time() < min(end, most.get("t", end) + 0.3):
        pass
    reader = next((k for k, a in list(got) if a.get("reader")), None)
    info = next((a["info"] for k, a in sorted(list(got), key=lambda x: not x[1].get("reader")) if a.get("info")), None)
    asked[key] = (time.time(), reader, info)
    return reader, info


def first_blocks(key, orig):





    pos = probed[key].get("opos" if orig else "pos")
    if not pos:
        return
    _, pe, _ = piece_span(key, min(2, len(pos) - 1), orig)
    for b in range(0, min(pe // SRC_BLOCK, 3) + 1):
        if wanted_block(key, b):
            threading.Thread(target=quietly, args=(src_block, key, b, True, 0), daemon=True).start()


def first_pieces(key, info, orig, waits):



    if waits:
        threading.Thread(target=quietly, args=(first_blocks, key, orig), daemon=True).start()
    for n in range(0 if orig else HLS_LOCAL):
        if piece_get(key, n) is None:
            args = (reader_piece, key, n, muxed(info), info["hdr"], info["w"], piece_cut(key, n)) if waits else (mac_piece, key, n, 1)
            threading.Thread(target=quietly, args=args, daemon=True).start()
    if info["a"] >= 0:
        threading.Thread(target=quietly, args=(audio_piece, key, 0, info["a"], orig), daemon=True).start()


def warm_film(token, full=True):




    key, _ = open_token(token)
    if not full:
        src_lookup(key)
        return
    if reader_of(key) not in (None, REPLICA) or claim_reader(key) != REPLICA:
        return
    src_meta[key]["warm"] = time.time()
    first_pieces(key, probe_source(key), False, False)


def quietly(fn, *args):
    try:
        fn(*args)
    except Dropped:
        pass
    except Exception as err:
        log(f"{fn.__name__}: {err.__class__.__name__}: {err}")


def buffer_film(key):




    f = films[key]
    making, failed = set(), {}

    def make(m):
        sound = None
        try:
            track = f.get("audio")

            if track and track[1] == (f.get("variant") == "orig"):
                if not track[1] and m > f["head"] + HLS_AHEAD:
                    threading.Thread(target=quietly, args=(fill_audio, key, m), daemon=True).start()
                else:
                    sound = threading.Thread(target=quietly, args=(audio_piece, key, m, *track), daemon=True)
                    sound.start()
            if (orig_piece(key, m) if f.get("variant") == "orig" else fetch_piece(key, m)) is None:
                failed[m] = time.time()
        except Dropped:
            pass
        except Exception as err:
            failed[m] = time.time()
            log(f"hls {key}: piece {m}: {err}")
        finally:
            if sound is not None:
                sound.join()
            with hls_lock:
                making.discard(m)
            f["wake"].set()
    try:
        while time.time() - f["seen"] < FILM_IDLE:
            f["wake"].clear()
            head, orig = f["head"], f.get("variant") == "orig"
            total = len(probed[key]["ocuts"]) - 1 if orig and probed.get(key, {}).get("ocuts") else f["total"]
            n_made, made_bytes = f["made"]
            guess = made_bytes / n_made if n_made else 1.2e6
            ahead, ready, want, m = 0, 0, [], head
            pk = key + ORIG if orig else key
            taken = f.get("fill", {}).get("taken", {})

            for m in range(head + 1, total):
                size = piece_has(pk, m)
                ahead += size or guess
                ready += size
                if ahead > BUFFER_BYTES:
                    break
                if (not size and m not in making and (m not in taken or m <= head + FILL_FROM) and len(want) < BUFFER_MAKING
                        and (time.time() - failed.get(m, 0) > 10 or failed.get(m, 0) < src_back.get(key, 0))):

                    want.append(m)
            f.update(buffer_end=m, ready_ahead=ready)
            if not watched(key):
                f["wake"].wait(1)
                continue
            ups = [] if orig else mac_check()
            batch = BATCH if ups and probed.get(key, {}).get("pend") and time.time() > mac_batch["until"] else 1

            with hls_lock:
                near = [x for x in making if head < x <= m]
                room = BUFFER_MAKING - len(near)
                if ups:
                    busy = len({id(mac_jobs[(key, x)]) for x in near if (key, x) in mac_jobs})
                    room = min(2 * BUFFER_MAKING - len(near), ((MAC_ROOM + 1) * len(ups) - busy) * batch)

                go = want[:max(0, room)]
                if (f.get("landing") is not None and time.time() - f["jumped"] < JUMP_ALONE
                        and (pk, head) not in pieces and (pk, head) not in mem_pieces):

                    go = [x for x in go if x <= head + HEAD_ALONE]
                making.update(go)
            if batch > 1:

                run = []
                for x in go + [None]:
                    if x is None or (run and (x != run[-1] + 1 or len(run) == batch)) or x == total - 1:
                        if run:
                            mac_start(key, run[0], 1, run=run)
                        run = [] if x is None or x == total - 1 else [x]
                    else:
                        run.append(x)
            for m in go:
                threading.Thread(target=make, args=(m,), daemon=True).start()
            f["wake"].wait(1)
    finally:
        f["buffering"] = False


def film_made(key, data):

    f = films.get(key)
    if f:
        n, total = f["made"]
        f["made"] = (n + 1, total + len(data))


def fetch_piece(key, m):


    def get():
        f = films.get(key)
        info = probed.get(key) or {}
        mine = info.get("w", 0) <= 1920 and not info.get("hdr") and from_player(key, m) > HLS_AHEAD

        data = None if (f and m in f["done"]) or mine else mac_piece(key, m, 1)
        if data is None:
            data = piece_get(key, m)
        if data is not None:
            film_made(key, data)
            return data
        owner, have = piece_owner(key, m, REPLICA), f is not None and m in f["done"]
        run = [m] if have else ready_run(key, m)
        try:
            st, h, body = conns.request("GET", f"{url_of(owner)}/hls/{films[key]['token']}/s{m}?{piece_q(key, m)}"
                                        + (run_q(key, run) if len(run) > 1 else "") + "&prep=get", None, {"X-Tor-Key": CHUNK_KEY}, 8 if have else 180)
            if st != 200 or not body:
                raise RuntimeError(f"piece {m} from copy {owner}: HTTP {st}")
        except Exception:
            if have:
                with hls_lock:
                    f["done"].discard(m)
                    f["fill"]["where"].pop(m, None)
            raise
        piece_put(key, m, body)
        film_made(key, body)
        if len(run) > 1 and f is not None and "fill" in f and h.get("X-Tor-Run") == str(run[-1]):
            with hls_lock:
                f["done"].update(run[1:])
                f["fill"]["where"].update((x, owner) for x in run[1:])
            where_keep(key, run[1:], owner)
        return body
    return once("get", key, m, get) or piece_get(key, m)




def mac_name(u):

    host = u.split("//")[-1]
    return host.split(":")[-1] if host[:1].isdigit() else host.split(".")[0]


def mac_check():


    if not MAC_URLS:
        return []
    with mac_lock:
        mac_looked["at"] = time.time()
        start = not mac_looked["busy"]
        mac_looked["busy"] = True
    if start:
        threading.Thread(target=mac_watch, daemon=True).start()
    return [u for u in MAC_URLS if macs[u]["up"] and not (macs[u]["health"] or {}).get("draining")]


def mac_watch():



    try:
        while True:
            with mac_lock:
                if time.time() - mac_looked["at"] > 300:
                    mac_looked["busy"] = False
                    return
                due = [u for u in MAC_URLS if not macs[u]["looking"] and time.time() >= macs[u]["look_at"]]
                for u in due:
                    macs[u]["looking"] = True
            for u in due:
                threading.Thread(target=mac_look, args=(u,), daemon=True).start()
            time.sleep(0.2)
    except Exception as err:
        log(f"tor-mac: the looks stopped: {err.__class__.__name__}: {err}")
        mac_looked["busy"] = False


def mac_look(u):


    m = macs[u]
    try:
        try:
            st, _, body = conns.request("GET", f"{u}/healthz", timeout=MAC_LOOK_WAIT, hold={})
            health = json.loads(body) if st == 200 else None
            why = None if isinstance(health, dict) and health.get("ok") else f"HTTP {st}"
        except Exception as err:
            health, why = None, f"{err.__class__.__name__}: {err}"
        with mac_lock:
            now = time.time()
            if why is None:
                m.update(health=health, misses=0, suspect=False, look_at=now + MAC_LOOK, answered=now, seen=m["inflight"])
                if m["full_until"] > now and now - m.get("full_seen", 0) < 30 and not health.get("draining"):

                    if health.get("jobs", 0) < (health.get("max") or 3) + 1:

                        m["full_until"] = now + min(8, 0.25 * 2 ** m["fulls"]) if m.get("fulls", 0) > 1 else 0
                    else:
                        m["look_at"] = now + 0.5
                if not m["up"] and now >= m["out_until"]:
                    m["up"] = True
                    log(f"tor-mac: {u} answers again")
            else:
                m["misses"] += 1
                if m["up"] and (m["misses"] >= 2 or m["suspect"]):
                    mac_down(u, f"didn't answer a look ({why})")
                m["look_at"] = now + (1 if m["up"] else min(MAC_LOOK, 2 ** min(m["misses"] - 1, 8)))
    finally:
        m["looking"] = False


def mac_down(u, why):


    m = macs[u]
    if m["up"]:
        m.update(up=False, health=None)
        log(f"tor-mac: {u} {why}: taken for down")
    m["look_at"] = min(m["look_at"], time.time() + 1)
    for call in list(m["calls"]):
        end_call(call)


def mac_trouble(u, why, sure):



    with mac_lock:
        m = macs[u]
        m["fails"] += 1
        if m["fails"] >= 3:
            m["out_until"] = time.time() + min(120, 5 * 2 ** (m["fails"] - 3))
        if sure or m["fails"] >= 3:
            mac_down(u, why)
        else:
            m.update(suspect=True, look_at=0)


def mac_slow(u):


    with mac_lock:
        if macs[u]["up"] and not macs[u]["suspect"]:
            macs[u].update(suspect=True, look_at=0)


def mac_usual():


    return min([sorted(v["took"])[len(v["took"]) // 2] for v in macs.values() if len(v["took"]) >= 3] or [0])


def mac_patience(cls):



    return min(2 * MAC_HEDGE[cls], max(MAC_HEDGE[cls], 1.5 * mac_usual()))


def near_head(key, m):


    f = films.get(key)
    return f is not None and 0 <= m - f["head"] <= HEAD_ALONE


def mac_quiet(u, now):


    m = macs[u]
    calls = m["calls"]
    return bool(calls) and now - max(m["answered"], min(c["t"] for c in calls)) > max(1.5, 1.5 * mac_usual())


def mac_fault(st, data):



    if st is None:
        why = data.decode(errors="replace")[:200]
        return why, why.startswith(("ConnectionRefusedError", "gaierror"))
    if st == 200:
        return f"not MPEG-TS ({len(data)} bytes)", False
    return f"HTTP {st}: {data[:120].decode(errors='replace')}", st in (403, 404, 502, 521, 523, 530)


def is_ts(data):

    return len(data) >= 188 and len(data) % 188 == 0 and data[0] == 0x47 and data[-188] == 0x47


def mac_load(u):


    m = macs[u]
    return max(0, ((m["health"] or {}).get("jobs") or 0) - m.get("seen", 0)) + m["inflight"]


def mac_room(u):



    return mac_load(u) < ((macs[u]["health"] or {}).get("max") or 3)


def mac_pick(cls, skip):





    now = time.time()
    ups = [u for u in mac_check() if u not in skip]
    if not ups:
        return None, "none"
    free = [u for u in ups if macs[u]["full_until"] <= now and (cls == 0 or (macs[u]["readied"] < MAC_ROOM and mac_room(u)))]
    if not free:
        return None, "full"
    usual = mac_usual() or 1.5

    def back(u):
        took = sorted(macs[u]["took"])
        return (took[len(took) // 2] if len(took) >= 3 else usual) * (1 + 0.2 * mac_load(u))
    return min(free, key=lambda u: (macs[u]["suspect"], mac_quiet(u, now), back(u), random.random())), None


def mac_call(call, q, body, job):


    u = call["u"]
    try:
        st, _, data = conns.request("POST", f"{u}/convert?{q}&p={min(call['cls'], 1)}", body=body, timeout=MAC_WAIT[call["cls"]], hold=call,
                                    headers={"X-Mac-Key": MAC_KEY, "Content-Type": "application/octet-stream"})
    except Exception as err:
        st, data = None, f"{err.__class__.__name__}: {err}".encode()
    with mac_lock:
        m = macs[u]
        m["inflight"] -= 1
        m["readied"] -= call["cls"] > 0
        m["calls"] = [c for c in m["calls"] if c is not call]
        if st is not None:
            m["answered"] = time.time()
    job["news"].put((call, st, data))


def mac_start(key, m, cls, hint=False, run=None):





    info = probed.get(key)
    if not info or not info["pos"] or not MAC_URLS:
        return None
    with mac_lock:
        job = mac_jobs.get((key, m))
        if job is None or job["dropped"]:
            pieces = [m]
            for n in (run or [])[1:]:
                other = mac_jobs.get((key, n))
                if n != pieces[-1] + 1 or piece_has(key, n) or (other is not None and not other["dropped"]):
                    break
                pieces.append(n)
            job = {"cls": cls, "calls": [], "news": queue.Queue(), "done": threading.Event(), "data": None,
                   "dropped": False, "here": False, "stages": "", "hint": hint, "pieces": pieces}
            for n in pieces:
                mac_jobs[(key, n)] = job
            threading.Thread(target=mac_race, args=(key, m, job), daemon=True).start()
        elif cls < job["cls"]:
            job["cls"] = cls
            job["news"].put(None)
    return job


def mac_piece(key, m, cls):

    job = mac_start(key, m, cls)
    if job is None:
        return None
    job["done"].wait(MAC_WAIT[1] + 600)
    if job["dropped"]:
        raise Dropped(f"piece {m} dropped: its player went elsewhere")
    return job["data"] if m == job["pieces"][0] else piece_get(key, m)


def mac_body(key, m, last=None):









    info = probed[key]
    last = m if last is None else last
    pb, pe, cut = piece_span(key, m)
    if last != m:
        _, pe, cut = piece_span(key, last)
    head = src_head(key, info["pos"][0], m)
    unit, tracks = mkv_head(head)
    video = next((num for num, kind in tracks if kind == 1), None)
    numbers, order = [num for num, kind in tracks if kind == 2], [x["i"] for x in info["audios"]]
    audio = numbers[order.index(info["a"])] if muxed(info) >= 0 and info["a"] in order and len(numbers) == len(order) else None
    t, (lt, ll) = piece_cut(key, m)[0], piece_cut(key, last)[:2]
    begin, end = (info["start"] + t - 0.002) / unit, (info["start"] + lt + ll + 0.002) / unit
    hevc, nal = info.get("vcodec") == "hevc", info.get("vcodec") in ("h264", "hevc")
    more = last + 1 < len(info["pos"])

    full = pe - pb + (min((pe - pb) // 2 + 2**16, -pe % SRC_BLOCK) if more else 0)
    late = max(1.0, 20 / (info.get("fps") or 24)) / unit
    span = mkv_until(key, pb, full, m, video, end + late) if info.get("pend") else src_read(key, pb, full, m)
    while True:
        blocks = [b for b in mkv_blocks(span, 0, len(span)) if b[1] is not None and b[0] in (video, audio)]
        if blocks:
            _, x, _ = ebml_vint(span, blocks[-1][2], True)
            size, y, _ = ebml_vint(span, blocks[-1][2] + x, False)
            if blocks[-1][2] + x + y + size > len(span):
                blocks.pop()
        vids = [i for i, b in enumerate(blocks) if b[0] == video]
        shown = [i for i in vids if blocks[i][1] < end]
        if not shown:
            raise RuntimeError(f"piece {m}: no blocks of its video in its bytes")
        first, stop = max([i for i in vids if blocks[i][1] <= begin + 0.004 / unit and mkv_key(span, *blocks[i][2:5])] or vids[:1]), shown[-1]
        pics = (lambda i: mkv_pics(span, blocks[i][4] + 3, blocks[i][5], hevc) if nal and blocks[i][6] == 1 else None)
        z = vids[-1]
        nk = next((pics(i) for i in vids if blocks[i][1] >= end - 0.004 / unit and mkv_key(span, *blocks[i][2:5])), None)
        if not more or (blocks[z][1] >= end and not mkv_key(span, *blocks[z][2:5])) or (nk and all(p[0] in ((18, 20) if hevc else (5,)) for p in nk)):
            break

        more = False
        span += src_read(key, pb + len(span), min((pe - pb) // 2, 8 * 2**20) + 2**16, m)
    k0, seen = blocks[first][1], {i: pics(i) for i in vids if first <= i <= stop and (hevc or blocks[i][1] < begin)}
    top = max([p[1] for v in seen.values() if v for p in v] or [0]) if None not in seen.values() else 99

    def unneeded(i):

        return blocks[i][1] < begin and bool(seen[i]) and all(((p[0] <= 14 and p[0] % 2 == 0 and p[1] == top) or (6 <= p[0] <= 9 and blocks[i][1] < k0))
                                                              if hevc else p[1] == 0 for p in seen[i])
    keep = [b for i, b in enumerate(blocks) if (b[0] == video and first <= i <= stop and not unneeded(i))
            or (b[0] == audio and begin - AUDIO_PRE / unit <= b[1] < end + 0.1 / unit)]
    kept = [num for num, kind in tracks if num in (video, audio)]
    return mac_head(head, kept) + mkv_clusters([(span[b[2]:b[3]], b[4] - b[2], b[1]) for b in keep]), kept.index(audio) if audio in kept else -1


def mkv_until(key, start, n, piece, track, until):





    size = src_meta[key].get("size") or src_lookup(key)["size"]
    land_now(key, piece)
    for nb in range(start // SRC_BLOCK + 1, (min(start + n, size) - 1) // SRC_BLOCK + 1):
        if wanted_block(key, nb):
            threading.Thread(target=quietly, args=(src_block, key, nb, True, piece), daemon=True).start()
    span, step = b"", max(2**20, n // 8)
    while len(span) < n:
        more = src_read(key, start + len(span), min(n - len(span), step), piece)
        if not more:
            break
        span += more
        for tr, ts, p, _, _, _, _ in mkv_blocks(span, 0, len(span)):
            if tr == track and ts is not None and ts >= until:
                _, a, _ = ebml_vint(span, p, True)
                length, b, _ = ebml_vint(span, p + a, False)
                if p + a + b + length <= len(span):
                    return span
    return span


def mac_head(head, keep):



    def el(eid, data):
        return eid.to_bytes((eid.bit_length() + 7) // 8, "big") + (len(data) | 1 << 56).to_bytes(8, "big") + data
    top = list(ebml(head, 0, len(head)))
    out = [head[:next(b for eid, _, b in top if eid == 0x1A45DFA3)], b"\x18\x53\x80\x67\x01" + b"\xff" * 7]
    for eid, a, b in ebml(head, next(a for eid, a, _ in top if eid == 0x18538067), len(head)):
        if eid == 0x1F43B675:
            break
        if eid == 0x1549A966:
            out.append(el(eid, head[a:b]))
        elif eid == 0x1654AE6B:
            out.append(el(eid, b"".join(el(0xAE, head[xa:xb]) for x, xa, xb in ebml(head, a, b) if x == 0xAE and any(
                y == 0xD7 and int.from_bytes(head[ya:yb], "big") in keep for y, ya, yb in ebml(head, xa, xb)))))
    return b"".join(out)


def mkv_pics(buf, p, e, hevc):


    out = []
    while p + 5 < e:
        h = buf[p + 4]
        if (h >> 1 & 63 < 32) if hevc else (h & 31 in (1, 5)):
            out.append((h >> 1 & 63, (buf[p + 5] & 7) - 1) if hevc else (h & 31, h >> 5))
        p += 4 + int.from_bytes(buf[p:p + 4], "big")
    return out if p == e and out else None


def ts_pts(pk):

    p = 5 + pk[4] if pk[3] & 0x20 else 4
    if p + 14 > len(pk) or pk[p:p + 3] != b"\0\0\1" or not pk[p + 7] & 0x80:
        return -1
    return (pk[p + 9] >> 1 & 7) << 30 | pk[p + 10] << 22 | (pk[p + 11] >> 1) << 15 | pk[p + 12] << 7 | pk[p + 13] >> 1


def ts_pieces(data, count, starts=None):




    pat = pmt = pmt_pid = video = None
    pieces, cur, keyed = [], [], False
    for i in range(0, len(data) - 187, 188):
        pk = data[i:i + 188]
        if pk[0] != 0x47:
            raise RuntimeError("not MPEG-TS")
        pid, pusi, af = ((pk[1] & 0x1f) << 8) | pk[2], pk[1] & 0x40, (pk[3] >> 4) & 3
        if pid == 0:
            pat = pk
            if pmt_pid is None:
                p = 5 + pk[4]
                sl = ((pk[p + 1] & 0x0f) << 8) | pk[p + 2]
                for q in range(p + 8, p + 3 + sl - 4, 4):
                    if ((pk[q] << 8) | pk[q + 1]) != 0:
                        pmt_pid = ((pk[q + 2] & 0x1f) << 8) | pk[q + 3]
                        break
            continue
        if pid == pmt_pid:
            pmt = pk
            if video is None:
                p = 5 + pk[4]
                sl = ((pk[p + 1] & 0x0f) << 8) | pk[p + 2]
                pil = ((pk[p + 10] & 0x0f) << 8) | pk[p + 11]
                q = p + 12 + pil
                while q < p + 3 + sl - 4:
                    stype, epid, esl = pk[q], ((pk[q + 1] & 0x1f) << 8) | pk[q + 2], ((pk[q + 3] & 0x0f) << 8) | pk[q + 4]
                    if stype in (0x1b, 0x24) and video is None:
                        video = epid
                    q += 5 + esl
            continue
        rai = af & 2 and pk[4] > 0 and pk[5] & 0x40
        if pid == video and pusi and rai:
            if keyed and (starts is None or (len(pieces) < len(starts) and 0 <= ts_pts(pk) - starts[len(pieces)] < 9000)):
                pieces.append(b"".join(cur))
                cur = []
            keyed = True
        if not cur and pat and pmt:
            cur = [pat, pmt]
        cur.append(pk)
    if cur:
        pieces.append(b"".join(cur))
    if len(pieces) != count:
        raise RuntimeError(f"{len(pieces)} pieces in the run, not {count}")
    return pieces


def mac_race(key, m, job):







    info = probed[key]
    calls, skip, broke, why = job["calls"], set(), [], None
    pieces = job["pieces"]
    last = pieces[-1]
    try:
        if not mac_check():
            return
        t, l, _, _ = piece_cut(key, m)
        if last != m:
            lt, ll, _, _ = piece_cut(key, last)
            l = round(lt + ll - t, 3)
        t0 = time.time()
        body, a = mac_body(key, m, last)
        q = (f"t={info['start'] + t:.3f}&l={l:.3f}&s={info['start']:.3f}&a={a}&h={info['hdr']}&w={info['w']}"
             + ("&k=1" if last != m else ""))
        t1 = time.time()
        while not job["dropped"] and not piece_has(key, m):
            now, cls = time.time(), job["cls"]
            if now - t1 > MAC_WAIT[cls]:
                why = f"no piece in {MAC_WAIT[cls]} s"
                break
            for c in calls:
                if c.get("ended") and not c["over"]:
                    c["over"] = True
                    skip.add(c["u"])
                    mac_failed["again"] += 1
            live = [c for c in calls if not c["over"]]
            patience = mac_patience(cls)
            for c in live:
                if not c.get("slow") and now - (c.get("sent") or c["t"]) > patience:
                    c["slow"] = True
                if not c.get("hung") and now - c["t"] > MAC_HUNG:
                    c["hung"] = True
                    mac_trouble(c["u"], f"no answer in {MAC_HUNG} s", False)
                if not c.get("looked") and now - c["t"] > mac_patience(0):

                    c["looked"] = True
                    mac_slow(c["u"])
            slow = [c for c in live if c.get("slow")]
            hurried = cls == 0 and live and all(c["cls"] > 0 for c in live)

            busy = None
            while len(live) < (2 if slow or hurried or cls == 0 else 1):


                u, busy = mac_pick(1 if cls == 0 and live and not (slow or hurried) else cls, skip | {c["u"] for c in live})

                if not u:
                    break
                mac_failed["hedged"] += bool(live)
                call = {"u": u, "t": now, "cls": cls, "over": False, "try": len(calls) + 1}
                with mac_lock:
                    macs[u]["inflight"] += 1
                    macs[u]["readied"] += cls > 0
                    macs[u]["calls"].append(call)
                calls.append(call)
                live.append(call)
                threading.Thread(target=mac_call, args=(call, q, body, job), daemon=True).start()
            if not live and busy == "none":
                why = why or "no Mac up"
                break
            if not live and busy == "full" and cls > 0 and now - t1 > MAC_FULL_WAIT:

                why = "the Macs are full"
                break
            if cls == 0 and (not live or (all(c.get("slow") for c in live) and (info["w"] <= 1920 and not info["hdr"] or now - t1 > MAC_HERE))):


                job["here"] = True
            try:
                news = job["news"].get(timeout=0.2)
            except queue.Empty:
                continue
            if news is None:
                continue
            call, st, data = news
            u, over = call["u"], call["over"]
            call["over"] = True
            if st == 200 and is_ts(data):
                if last != m:
                    try:
                        parts = ts_pieces(data, len(pieces))
                    except Exception as err:

                        mac_batch["until"] = time.time() + 600
                        log(f"hls {key}: pieces {m}-{last} as one from {mac_name(u)}: {err}: one at a time for 10 minutes")
                        why = f"the run wasn't split: {err}"
                        break
                    for n, part in zip(pieces, parts):
                        piece_put(key, n, part)
                    data = parts[0]
                else:
                    piece_put(key, m, data)
                job["data"] = data
                now = time.time()
                up, conv, down = (call.get("sent", now) - call["t"], call.get("headers", now) - call.get("sent", now),
                                  call.get("read", now) - call.get("headers", now))
                before = [f"{mac_name(c['u'])} {'slow' if c.get('slow') else 'failed' if c['over'] else 'behind'}" for c in calls if c is not call]
                job["stages"] = (f"read {t1 - t0:.1f}s, up {up:.1f}s, convert {conv:.1f}s, down {down:.1f}s on {mac_name(u)}"
                                 + (f" x{len(pieces)}" if last != m else "") + ("" if call["try"] == 1 else f" (try {call['try']}: {', '.join(before)})"))
                with mac_lock:
                    macs[u].update(done=macs[u]["done"] + 1, fails=0, fulls=0)
                    if last == m:
                        macs[u]["took"].append(now - call["t"])
                bstats["mac"].append((int(now), m, cls, round(t1 - t0, 2), round(now - call["t"], 2), round(len(body) / 1e6, 1),
                                      mac_name(u) + (f" x{len(pieces)}" if last != m else "") + ("" if call["try"] == 1 else f" (try {call['try']})"),
                                      round(up, 2), round(conv, 2), round(down, 2)))
                for x, fault in broke:
                    mac_trouble(x, fault, False)
                break
            if over:
                continue
            if call.get("ended"):
                mac_failed["again"] += 1
                skip.add(u)
                continue
            if st in (502, 503):


                with mac_lock:

                    now, x = time.time(), macs[u]
                    fulls = x.get("fulls", 0) + 1 if now - x.get("full_seen", 0) < 60 else 1
                    x.update(full_until=now + (2 if b"draining" in data else 30), full_seen=now, fulls=fulls, look_at=min(x["look_at"], now + 0.3))
                if st == 502:
                    mac_slow(u)
                continue
            why, sure = mac_fault(st, data)
            skip.add(u)
            if st in (400, 500):

                if b"videotoolbox" in data:
                    mac_trouble(u, why, False)
                broke.append((u, why))
                if len(broke) >= 2:
                    break
                continue
            mac_failed["again"] += 1
            mac_trouble(u, why, sure)
    except Dropped:
        job["dropped"] = True
    except Exception as err:
        why = f"{err.__class__.__name__}: {err}"
    finally:
        for c in calls:
            if not c["over"] and time.time() - c["t"] > MAC_WAIT[c["cls"]]:


                end_call(c)
        with mac_lock:
            for n in pieces:
                if mac_jobs.get((key, n)) is job:
                    del mac_jobs[(key, n)]
        if job["data"] is None and not job["dropped"] and not piece_has(key, m):
            mac_failed["n"] += 1
            if why and why != "the Macs are full":
                log(f"hls {key}: piece {m} not made on the Macs: {why}")
        job["done"].set()


ORIG = ":ov"
AUDIO_PRE = 0.3

LANGS = {"eng": ("en", "English"), "spa": ("es", "Spanish"), "fre": ("fr", "French"), "fra": ("fr", "French"), "ger": ("de", "German"),
         "deu": ("de", "German"), "ita": ("it", "Italian"), "jpn": ("ja", "Japanese"), "kor": ("ko", "Korean"), "chi": ("zh", "Chinese"),
         "zho": ("zh", "Chinese"), "rus": ("ru", "Russian"), "por": ("pt", "Portuguese"), "hin": ("hi", "Hindi"), "ara": ("ar", "Arabic"),
         "dut": ("nl", "Dutch"), "nld": ("nl", "Dutch"), "swe": ("sv", "Swedish"), "nor": ("no", "Norwegian"), "nob": ("nb", "Norwegian"),
         "dan": ("da", "Danish"), "fin": ("fi", "Finnish"), "pol": ("pl", "Polish"), "tur": ("tr", "Turkish"), "ukr": ("uk", "Ukrainian"),
         "cze": ("cs", "Czech"), "ces": ("cs", "Czech"), "hun": ("hu", "Hungarian"), "gre": ("el", "Greek"), "ell": ("el", "Greek"),
         "heb": ("he", "Hebrew"), "tha": ("th", "Thai"), "vie": ("vi", "Vietnamese"), "ind": ("id", "Indonesian"), "may": ("ms", "Malay"),
         "msa": ("ms", "Malay"), "rum": ("ro", "Romanian"), "ron": ("ro", "Romanian"), "bul": ("bg", "Bulgarian"), "hrv": ("hr", "Croatian"),
         "srp": ("sr", "Serbian"), "slv": ("sl", "Slovenian"), "slo": ("sk", "Slovak"), "slk": ("sk", "Slovak"), "est": ("et", "Estonian"),
         "lav": ("lv", "Latvian"), "lit": ("lt", "Lithuanian"), "ice": ("is", "Icelandic"), "isl": ("is", "Icelandic"), "per": ("fa", "Persian"),
         "fas": ("fa", "Persian"), "tam": ("ta", "Tamil"), "tel": ("te", "Telugu"), "ben": ("bn", "Bengali"), "urd": ("ur", "Urdu"),
         "mal": ("ml", "Malayalam"), "kan": ("kn", "Kannada"), "mar": ("mr", "Marathi"), "cat": ("ca", "Catalan"), "baq": ("eu", "Basque"),
         "eus": ("eu", "Basque"), "glg": ("gl", "Galician"), "fil": ("fil", "Filipino"), "tgl": ("tl", "Tagalog")}
MKV_KIDS = {0xE7, 0xA3, 0xA0, 0xA7, 0xAB, 0xEC, 0xBF, 0xAF}
MKV_RESYNC = re.compile(rb"[\xa3\xa0\xe7\xa7\xab\xec\xbf]|\x1f\x43\xb6\x75")


def orig_unfit(info):

    if not info.get("opos"):
        return "it isn't cut at its keyframes (not Matroska, or no keyframe index)"
    if info.get("vcodec") not in ("hevc", "h264"):
        return f"its video is {info.get('vcodec')}, which browsers don't play"
    if info.get("dv") == 5:
        return "it's Dolby Vision only (profile 5): its colours would be wrong"
    return None


def mp4_boxes(b, i=0, end=None):
    end = len(b) if end is None else end
    while i + 8 <= end:
        size, kind = struct.unpack(">I4s", b[i:i + 8])
        if size < 8:
            return
        yield kind, i, i + 8, i + size
        i += size


def mp4_scales(init):

    scales = {}
    for kind, _, a, z in mp4_boxes(init):
        for k2, _, a2, z2 in (mp4_boxes(init, a, z) if kind == b"moov" else ()):
            if k2 != b"trak":
                continue
            tid = scale = None
            for k3, _, a3, z3 in mp4_boxes(init, a2, z2):
                if k3 == b"tkhd":
                    v = init[a3]
                    tid = struct.unpack(">I", init[a3 + (20 if v == 1 else 12):a3 + (24 if v == 1 else 16)])[0]
                elif k3 == b"mdia":
                    for k4, _, a4, _ in mp4_boxes(init, a3, z3):
                        if k4 == b"mdhd":
                            v = init[a4]
                            scale = struct.unpack(">I", init[a4 + (20 if v == 1 else 12):a4 + (24 if v == 1 else 16)])[0]
            scales[tid] = scale
    return scales


def orig_place(init, media, at):


    scales = mp4_scales(init)
    m = bytearray(media)
    first, fields = {}, []
    for kind, _, a, z in mp4_boxes(m):
        for k2, _, a2, z2 in (mp4_boxes(m, a, z) if kind == b"moof" else ()):
            if k2 != b"traf":
                continue
            tid = tfdt = cts = None
            for k3, _, a3, _ in mp4_boxes(m, a2, z2):
                if k3 == b"tfhd":
                    tid = struct.unpack(">I", m[a3 + 4:a3 + 8])[0]
                elif k3 == b"tfdt":
                    wide = m[a3] == 1
                    tfdt = struct.unpack(">Q" if wide else ">I", m[a3 + 4:a3 + (12 if wide else 8)])[0]
                    fields.append((tid, a3 + 4, wide))
                elif k3 == b"trun":
                    v, flags = m[a3], int.from_bytes(m[a3 + 1:a3 + 4], "big")
                    p = a3 + 8 + (4 if flags & 1 else 0) + (4 if flags & 4 else 0)
                    p += sum(4 for bit in (0x100, 0x200, 0x400) if flags & bit)
                    cts = struct.unpack(">i" if v == 1 else ">I", m[p:p + 4])[0] if flags & 0x800 else 0
            if tid is not None and tid not in first and tfdt is not None:
                first[tid] = tfdt + (cts or 0)
    for tid, p, wide in fields:
        if tid in first and scales.get(tid):
            fmt = ">Q" if wide else ">I"
            n = struct.calcsize(fmt)
            t = struct.unpack(fmt, m[p:p + n])[0] + round(at * scales[tid]) - first[tid]
            m[p:p + n] = struct.pack(fmt, max(0, t))
    return bytes(m)


def mkv_head(buf):


    seg = next((a for eid, a, _ in ebml(buf, 0, len(buf)) if eid == 0x18538067), None)
    unit, tracks = 1e-3, []
    for eid, a, b in ebml(buf, seg or 0, len(buf)):
        if eid == 0x1F43B675:
            break
        if eid == 0x1549A966:
            unit = next((int.from_bytes(buf[xa:xb], "big") / 1e9 for x, xa, xb in ebml(buf, a, b) if x == 0x2AD7B1), unit)
        elif eid == 0x1654AE6B:
            for x, xa, xb in ebml(buf, a, b):
                if x == 0xAE:
                    f = {y: int.from_bytes(buf[ya:yb], "big") for y, ya, yb in ebml(buf, xa, xb) if y in (0xD7, 0x83)}
                    tracks.append((f.get(0xD7), f.get(0x83)))
    return unit, tracks


def mkv_blocks(buf, p, end):



    ts = None
    while p < end - 2:
        try:
            eid, a, _ = ebml_vint(buf, p, True)
            size, b, unknown = ebml_vint(buf, p + a, False)
        except IndexError:
            return
        data = p + a + b
        stop = end if unknown else min(end, data + size)
        if eid == 0x1F43B675:
            ts, p = None, data
            continue
        if eid == 0xE7:
            ts = int.from_bytes(buf[data:stop], "big")
        elif eid in (0xA3, 0xA0):
            blk, blk_end = (data, stop) if eid == 0xA3 else next(((xa, xb) for x, xa, xb in ebml(buf, data, stop) if x == 0xA1), (None, None))
            if blk is not None and blk + 4 <= blk_end:
                track, k, _ = ebml_vint(buf, blk, False)
                rel = int.from_bytes(buf[blk + k:blk + k + 2], "big", signed=True)
                laced = buf[blk + k + 2] & 6 and blk + k + 3 < blk_end
                yield track, None if ts is None else ts + rel, p, stop, blk + k, blk_end, buf[blk + k + 3] + 1 if laced else 1
        p = stop


def mkv_resync(buf, end, tracks):


    for m in MKV_RESYNC.finditer(buf, 0, end):
        p, hops = m.start(), 0
        try:
            while p < end:
                eid, a, _ = ebml_vint(buf, p, True)
                size, b, unknown = ebml_vint(buf, p + a, False)
                if unknown or (eid not in MKV_KIDS and eid != 0x1F43B675):
                    break
                if eid == 0xA3 and ebml_vint(buf, p + a + b, False)[0] not in tracks:
                    break
                p, hops = p + a + b + (0 if eid == 0x1F43B675 else size), hops + 1
        except IndexError:
            continue
        if p == end and hops >= 3:
            return m.start()
    return None


def mkv_key(buf, s, z, r):

    if buf[s] == 0xA3:
        return bool(buf[r + 2] & 0x80)
    _, b, _ = ebml_vint(buf, s + 1, False)
    return not any(x == 0xFB for x, _, _ in ebml(buf, s + 1 + b, z))


def mkv_clusters(blocks):


    out, k = [], 0
    while k < len(blocks):
        j = k
        while j < len(blocks) and abs(blocks[j][2] - blocks[k][2]) < 30000:
            j += 1
        out.append(mkv_cluster(blocks[k][2], blocks[k:j]))
        k = j
    return b"".join(out)


def mkv_cluster(ts, blocks):


    out = bytearray(b"\xe7\x88" + ts.to_bytes(8, "big"))
    for blk, r, t in blocks:
        at = len(out) + r
        out += blk
        out[at:at + 2] = (t - ts).to_bytes(2, "big", signed=True)
    return b"\x1f\x43\xb6\x75" + (len(out) | 1 << 56).to_bytes(8, "big") + bytes(out)


def audio_start(blocks):



    xs, x = [], 0
    for _, frames in blocks:
        xs.append(x)
        x += frames
    ts = [t for t, _ in blocks]
    n = len(ts)
    if n < 4 or xs[-1] == 0:
        return ts[0]
    mx, mt = sum(xs) / n, sum(ts) / n
    slope = sum((x - mx) * (t - mt) for x, t in zip(xs, ts)) / sum((x - mx) ** 2 for x in xs)
    start = mt - slope * mx
    return start if max(abs(t - start - slope * x) for x, t in zip(xs, ts)) <= 0.75 else ts[0]


def audio_pre(info, pos, n, pb):


    rate = info["size"] / max(1.0, info["duration"])
    return max(pos[n - 1], pb - max(2**20, int(rate * AUDIO_PRE * 3)))


def sound_blocks(key, n, a, strict):



    info = probed[key]
    pos, size = info["pos"], src_meta[key].get("size") or info["size"]
    pb, pe, _ = piece_span(key, n)
    need = set(range(pb // SRC_BLOCK, (min(pe, size) - 1) // SRC_BLOCK + 1))
    if src_meta[key].get("head") is None:
        need.update(range(0, (pos[0] - 1) // SRC_BLOCK + 1))
    if n > 0 and (strict or next((x["kind"] for x in info["audios"] if x["i"] == a), "") == "TrueHD"):
        need.update(range(audio_pre(info, pos, n, pb) // SRC_BLOCK, (pb - 1) // SRC_BLOCK + 1))
    return need


def in_ram(key, n, a, strict):


    try:
        need = sound_blocks(key, n, a, strict)
    except Exception:
        return not strict
    return all(block_cached(key, b) is not None for b in need)


def audio_source(key, n, a, orig):





    info = probed[key]
    cuts, pos, aligned = (info["ocuts"], info["opos"], info.get("oaligned") or []) if orig else (info["cuts"], info["pos"], info.get("aligned") or [])
    head = src_head(key, pos[0], n)
    unit, tracks = mkv_head(head)
    numbers, order = [t for t, kind in tracks if kind == 2], [x["i"] for x in info["audios"]]
    if len(numbers) != len(order) or a not in order:
        raise RuntimeError(f"audio track {a}: not in the file's header")
    track, s0 = numbers[order.index(a)], info["start"]
    lo = round((s0 + cuts[n]) / unit)
    hi = round((s0 + cuts[n + 1]) / unit) if n + 2 < len(cuts) else None
    pb, pe, cut_end = piece_span(key, n, orig)
    before = n > 0
    if before:
        start = audio_pre(info, pos, n, pb)
        blocks = range(start // SRC_BLOCK, (pb - 1) // SRC_BLOCK + 1)
        if any(block_cached(key, b) is None for b in blocks) and next((x["kind"] for x in info["audios"] if x["i"] == a), "") != "TrueHD":
            before = False


        else:
            for b in blocks:
                if wanted_block(key, b):
                    threading.Thread(target=quietly, args=(src_block, key, b, True, n), daemon=True).start()
    span = (mkv_until(key, pb, pe - pb, n, track, hi) if cut_end and hi is not None and info.get("pend") and not orig
            else src_read(key, pb, pe - pb, n))
    from_start = n < len(aligned) and aligned[n]
    own, pre = [], []
    for tr, ts, s, z, r, _, frames in mkv_blocks(span, 0, len(span)):
        if tr != track or ts is None:
            continue
        if not from_start and ts < lo:
            pre.append((span[s:z], r - s))
        elif not (cut_end and hi is not None and ts >= hi):
            own.append((span[s:z], r - s, ts, frames))
    if not own:
        raise RuntimeError(f"no audio of track {a} in piece {n}")
    if before:
        tail = src_read(key, start, pb - start, n)
        at = 0 if start == pos[n - 1] else mkv_resync(tail, len(tail), {t for t, _ in tracks})
        if at is not None:
            pre = [(tail[s:z], r - s) for tr, _, s, z, r, _, _ in mkv_blocks(tail, at, len(tail)) if tr == track] + pre
    pre = pre[-600:]
    first = own[0][2]
    base = max(0, first - round(10 / unit) - len(pre))
    pre = mkv_cluster(base, [(b, r, base + i) for i, (b, r) in enumerate(pre)]) if pre else b""
    return head + pre + mkv_clusters([x[:3] for x in own]), first * unit, audio_start([(ts, frames) for _, _, ts, frames in own]) * unit


def orig_times(init, media, frames, unit, offset):




    scale = mp4_scales(init).get(1)
    m = bytearray(media)
    runs = []
    for kind, _, a, z in mp4_boxes(m):
        for k2, _, a2, z2 in (mp4_boxes(m, a, z) if kind == b"moof" else ()):
            if k2 != b"traf":
                continue
            tid, tfdt, truns = None, None, []
            for k3, _, a3, _ in mp4_boxes(m, a2, z2):
                if k3 == b"tfhd":
                    tid = struct.unpack(">I", m[a3 + 4:a3 + 8])[0]
                elif k3 == b"tfdt":
                    tfdt = (a3 + 4, m[a3] == 1)
                elif k3 == b"trun":
                    truns.append(a3)
            if tid != 1:
                continue
            if tfdt is None or len(truns) != 1:
                return None
            t = truns[0]
            v, flags = m[t], int.from_bytes(m[t + 1:t + 4], "big")
            if flags & 0xB00 != 0xB00:
                return None
            runs.append((tfdt, t + 8 + (4 if flags & 1 else 0) + (4 if flags & 4 else 0), struct.unpack(">I", m[t + 4:t + 8])[0],
                         *((16, 12) if flags & 0x400 else (12, 8)), v))
    if not scale or not runs:
        return None
    sizes = [struct.unpack(">I", m[p + i * w + 4:p + i * w + 8])[0] for _, p, count, w, _, _ in runs for i in range(count)]
    fs = [s for _, s in frames]
    k = next((k for k in range(len(fs) - len(sizes) + 1) if fs[k:k + len(sizes)] == sizes), None)
    if k is None:
        return None
    pts = [round((frames[k + i][0] * unit + offset) * scale) for i in range(len(sizes))]
    order = sorted(pts)
    lead = max([round(0.25 * scale)] + [o - q for o, q in zip(order, pts)])
    dts = [o - lead for o in order]
    steps = sorted(b - a for a, b in zip(order, order[1:]) if b > a)
    dts.append(dts[-1] + (steps[len(steps) // 2] if steps else round(scale / 24)))
    i = 0
    for (at, wide), p, count, w, cto, v in runs:
        m[at:at + (8 if wide else 4)] = struct.pack(">Q" if wide else ">I", dts[i])
        for j in range(count):
            q = p + j * w
            m[q:q + 4] = struct.pack(">I", dts[i + 1] - dts[i])
            m[q + cto:q + cto + 4] = struct.pack(">i" if v else ">I", pts[i] - dts[i])
            i += 1
    return bytes(m)


def orig_piece(key, n):




    ok = key + ORIG
    data = piece_get(ok, n)
    if data is not None:
        return data

    def make():
        init, media = orig_video(key, n)
        if piece_get(ok, -1) is None:
            piece_put(ok, -1, init)
        piece_put(ok, n, media)
        return media
    return once("orig", key, n, make) or piece_get(ok, n)


def orig_video(key, n):



    info = probed[key]
    t, l, _, _ = piece_cut(key, n, True)
    s0 = info["start"]
    pb, pe, _ = piece_span(key, n, True)
    head, span = src_head(key, info["opos"][0], n), src_read(key, pb, pe - pb, n)
    unit, tracks = mkv_head(head)
    video = next((num for num, kind in tracks if kind == 1), None)
    tol = max(1, round(0.002 / unit))
    lo = round((s0 + t) / unit)
    hi = round((s0 + t + l) / unit) if n + 2 < len(info["ocuts"]) else None
    frames = []
    for tr, ts, s, z, r, e, _ in mkv_blocks(span, 0, len(span)):
        if tr != video or ts is None:
            continue
        if not frames and not (ts >= lo - tol and mkv_key(span, s, z, r)):
            continue
        if frames and hi is not None and ts >= hi - tol and mkv_key(span, s, z, r):
            break
        frames.append((span[s:z], r - s, ts, e - r - 3))
    if not frames:
        raise RuntimeError(f"piece {n} as it is: its keyframe isn't in its bytes")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-copyts", "-i", "pipe:0", "-map", "0:v:0", "-c:v", "copy",
           "-tag:v", "hvc1" if info["vcodec"] == "hevc" else "avc1", "-map_chapters", "-1", "-map_metadata", "-1", "-fflags", "+bitexact",
           "-use_editlist", "0", "-movflags", "+frag_keyframe+empty_moov+default_base_moof+frag_discont", "-frag_duration", "100000000",
           "-f", "mp4", "pipe:1"]
    out = subprocess.run(cmd, input=head + mkv_clusters([x[:3] for x in frames]), capture_output=True, timeout=60)
    cut = next((s for kind, s, _, _ in mp4_boxes(out.stdout) if kind == b"moof"), None)
    if out.returncode or cut is None:
        raise RuntimeError(f"ffmpeg, piece {n} as it is (exit {out.returncode}): {out.stderr.decode(errors='replace')[-300:]}")
    init, media = out.stdout[:cut], out.stdout[cut:]
    return init, orig_times(init, media, [(ts, size) for _, _, ts, size in frames], unit, TS_BASE - s0) or orig_place(init, media, TS_BASE + t)


def audio_piece(key, n, a, orig):




    pk = f"{key}:{'o' if orig else 'c'}a{a}"
    data = piece_get(pk, n)
    if data is not None:
        return data

    def make():
        init, data = audio_make(key, n, a, orig)
        if init is not None and piece_get(pk, -1) is None:
            piece_put(pk, -1, init)
        piece_put(pk, n, data)
        return data
    return once("audio", pk, n, make) or piece_get(pk, n)


def audio_make(key, n, a, orig):

    info = probed[key]
    s0, cuts = info["start"], info["ocuts"] if orig else info["cuts"]
    if info["opos"] if orig else info["pos"]:
        body, first, start = audio_source(key, n, a, orig)
        src, trim = ["-copyts", "-i", "pipe:0"], f"atrim=start={first - 0.0004:.4f}"
    else:
        body, first = None, s0 + cuts[n]
        start = first
        src = ["-headers", f"X-Tor-Key: {CHUNK_KEY}\r\n", "-ss", f"{max(0.0, cuts[n] - 2):.3f}", "-copyts",
               "-i", f"http://127.0.0.1:{PAGE_PORT}/src/{key}?u=1&n={n}"]
        trim = f"atrim=start={start:.3f}:end={s0 + cuts[n + 1]:.3f}"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"] + src + \
          ["-map", f"0:{a}", "-af", trim + ",asetpts=STARTPTS+N/SR/TB", "-map_metadata", "-1", "-map_chapters", "-1"] + \
          (["-c:a", "flac", "-fflags", "+bitexact", "-use_editlist", "0", "-movflags", "+frag_keyframe+empty_moov+default_base_moof+frag_discont",
            "-frag_duration", "100000000", "-f", "mp4"] if orig else
           ["-c:a", "aac", "-ac", "2", "-b:a", "192k", "-output_ts_offset", f"{TS_BASE - s0 + start - first:.6f}", "-muxdelay", "0",
            "-muxpreload", "0", "-f", "mpegts"]) + ["pipe:1"]
    out = subprocess.run(cmd, input=body, capture_output=True, timeout=60)
    if out.returncode or not out.stdout:
        raise RuntimeError(f"ffmpeg, audio piece {n} of track {a} (exit {out.returncode}): {out.stderr.decode(errors='replace')[-300:]}")
    if not orig:
        return None, out.stdout
    cut = next((s for kind, s, _, _ in mp4_boxes(out.stdout) if kind == b"moof"), None)
    if cut is None:
        raise RuntimeError(f"ffmpeg, audio piece {n} of track {a}: no fragment")
    return out.stdout[:cut], orig_place(out.stdout[:cut], out.stdout[cut:], TS_BASE + start - s0)


def orig_codecs(info):

    if info["vcodec"] == "hevc":
        main10 = "10" in info["vprofile"]
        v = f"hvc1.{2 if main10 else 1}.{4 if main10 else 6}.L{info['vlevel'] or 150}.B0"
    else:
        profile = {"Baseline": 66, "Constrained Baseline": 66, "Main": 77, "High": 100, "High 10": 110}.get(info["vprofile"], 100)
        v = f"avc1.{profile:02x}00{info['vlevel'] or 41:02x}"
    return v + (",fLaC" if info["audios"] else "")


def audio_run(key, ns, a, keep=True):




    s0, srcs = probed[key]["start"], [audio_source(key, n, a, False) for n in ns]
    ins, outs = [os.pipe() for _ in ns], [os.pipe() for _ in ns]
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-copyts"]
    for r, _ in ins:
        cmd += ["-i", f"pipe:{r}"]
    for i, ((_, first, start), (_, w)) in enumerate(zip(srcs, outs)):
        cmd += ["-map", f"{i}:{a}", "-af", f"atrim=start={first - 0.0004:.4f},asetpts=STARTPTS+N/SR/TB", "-map_metadata", "-1",
                "-map_chapters", "-1", "-c:a", "aac", "-ac", "2", "-b:a", "192k", "-output_ts_offset", f"{TS_BASE - s0 + start - first:.6f}",
                "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", f"pipe:{w}"]
    feeds, takes, timer = [open(w, "wb") for _, w in ins], [open(r, "rb") for r, _ in outs], None
    try:
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 pass_fds=[r for r, _ in ins] + [w for _, w in outs])
        finally:
            for fd in [r for r, _ in ins] + [w for _, w in outs]:
                os.close(fd)
        timer = threading.Timer(300, p.kill)
        timer.start()
        with concurrent.futures.ThreadPoolExecutor(2 * len(ns)) as pool:
            for fh, (body, _, _) in zip(feeds, srcs):
                pool.submit(lambda fh, body: (fh.write(body), fh.close()), fh, body)
            got = [pool.submit(fh.read) for fh in takes]
            err = p.stderr.read()
            p.wait()
            got = [x.result() for x in got]
    finally:
        if timer:
            timer.cancel()
        for fh in feeds + takes:
            try:
                fh.close()
            except OSError:
                pass
    if p.returncode or not all(got):
        raise RuntimeError(f"ffmpeg (exit {p.returncode}): {err.decode(errors='replace')[-300:]}")
    for n, data in zip(ns, got) if keep else ():
        if not piece_has(f"{key}:ca{a}", n):
            piece_put(f"{key}:ca{a}", n, data)
    return dict(zip(ns, got))


def audio_renditions(info, orig):


    lines, names, langs = [], set(), set()
    for x in info["audios"]:
        code, lang = LANGS.get((x["lang"] or "").lower(), (None, None))
        if not code and x["lang"] and x["lang"].lower() != "und":
            code = lang = x["lang"]
        ch = x["ch"] if orig else 2
        what = x["title"] or " ".join(filter(None, (x["kind"], {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(x["ch"]))))
        name = re.sub(r'["\n\r]', "'", f"{lang or 'Unknown'} · {what}")
        while name in names:
            name += " ·"
        names.add(name)
        default = x["i"] == info["a"]
        lines.append(f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="{name}"' + (f',LANGUAGE="{code}"' if code else "") +
                     f',DEFAULT={"YES" if default else "NO"},AUTOSELECT={"YES" if default or (code and code not in langs) else "NO"}'
                     f',CHANNELS="{ch or 2}",URI="{"o" if orig else "c"}a{x["i"]}.m3u8?r={REPLICA}"')
        langs.add(code)
    return lines


def init_piece(key, pk, make):




    for _ in range(3):
        data = piece_get(pk, -1)
        if data is not None:
            return data
        f = films.get(key)
        try:
            make(f["head"] if f else 0)
        except Dropped:
            continue
    return piece_get(pk, -1)


def patiently(key, n, make):




    t0 = time.time()
    while True:
        try:
            return make()
        except Dropped:
            raise
        except Exception:
            trouble = src_trouble.get(key)
            if not trouble or time.time() - t0 > 60 or time.time() - trouble.get("since", 0) > 120 or from_player(key, n) > 1:
                raise
            time.sleep(1)


land_races = {"n": 0}


def land_race(key, m, hdr, w):






    f = films.get(key)
    if f is None or hdr or w > 1920 or os.getloadavg()[0] >= LAND_LOAD * (os.cpu_count() or 1):
        return False
    if not ((f.get("landing") == m and time.time() - f.get("jumped", 0) < JUMP_ALONE) or time.time() - f.get("macs_failed", 0) < 60):
        return False
    with hls_lock:
        if land_races["n"]:
            return False
        land_races["n"] += 1
    return True


def reader_piece(key, m, a, hdr, w, cut):




    data = piece_get(key, m)
    if data is not None:
        return data, "disk"
    f = films.get(key)
    t0 = time.time()
    if f and m in f["done"]:
        try:
            data = fetch_piece(key, m)
        except Exception as err:
            log(f"hls {key}: {err}")
        if data is not None:
            return data, f"owner, copy {piece_owner(key, m, REPLICA)} in {time.time() - t0:.1f}s"
    land_now(key, m)
    job, here = mac_start(key, m, 0), None
    race = land_race(key, m, hdr, w)
    try:
        while True:
            if here is None and (race or job is None or job["here"] or job["done"].is_set()):
                if f is not None and job is not None and job["done"].is_set() and job["data"] is None and not job["dropped"] and not piece_has(key, m):
                    f["macs_failed"] = time.time()
                here = {"over": False, "err": None, "t": time.time()}

                def run(here=here):
                    try:
                        convert(key, m, a, hdr, w, 0, cut)
                    except Exception as err:
                        here["err"] = err
                    finally:
                        here["over"] = True
                        with made:
                            made.notify_all()
                threading.Thread(target=run, daemon=True).start()
            data = piece_wait(key, m, 0.2)
            if data is not None:
                break
            if job is not None and job["dropped"]:
                raise Dropped(f"piece {m} dropped: its player went elsewhere")
            if here is not None and here["over"] and (job is None or job["done"].is_set()):
                data = piece_get(key, m)
                if data is None:
                    raise here["err"] or RuntimeError(f"piece {m}: not made")
                break
    finally:
        if here is not None and not here["over"] and data is not None:
            cpu_drop(key, m)
        if race:
            with hls_lock:
                land_races["n"] -= 1
    if job is not None and job["data"] is not None:
        return data, f"mac: {job['stages']}" + (f", waited {time.time() - t0:.1f}s in all" if here else "")
    return data, f"here in {time.time() - here['t']:.1f}s" + (" (raced with the Macs: first)" if race else f" ({job['stages'] or 'the Macs did not make it'})"
                                                               if job is not None else "") if here else "made meanwhile"




def tell_reader(token, key, n, reader, variant=None, when=None):



    if reader == REPLICA:
        return film_heard(key, token, n, variant, when)
    last_at, last = heard_at.get(key, (0, -10))
    if time.time() - last_at < 3 and last <= n <= last + 3:
        return
    heard_at[key] = (time.time(), n)
    try:
        conns.request("GET", f"{url_of(reader)}/hls/{token}/at{n}?r={reader}" + (f"&v={variant}" if variant else "") + (f"&t={when:.3f}" if when else ""),
                      headers={"X-Tor-Key": CHUNK_KEY}, timeout=5)
    except Exception:
        pass


def film_heard(key, token, head, variant=None, when=None):





    src_meta[key]["reader"] = REPLICA
    total = len(probe_source(key)["cuts"]) - 1
    with hls_lock:
        f = films.setdefault(key, {"token": token, "total": total, "head": head, "seen": 0, "done": set(), "filling": 0, "jumped": time.time(), "landing": head,
                                   "wake": threading.Event(), "buffering": False, "made": (0, 0), "buffer_end": head, "ready_ahead": 0, "heard": 0, "claimed": 0})
        when = when or time.time()
        jumped, span, old = False, max(HLS_AHEAD, f["buffer_end"] - f["head"]), f["head"]
        if when >= f["heard"] - 1:

            jumped = not f["head"] <= head <= f["head"] + 3
            if jumped:
                f.update(jumped=time.time(), landing=head)
                if "fill" in f:
                    f["fill"]["claim"].clear()
            f.update(head=head, heard=max(when, f["heard"]))
        f["seen"] = time.time()
        if variant in ("orig", "conv"):
            f["variant"] = variant
        start = (f["filling"] == 0 and len(f["done"]) < f["total"] and f.get("variant") != "orig"
                 and time.time() - f.get("fill_end", 0) > 30)
        if start:
            owners = f["owners"] = converters(key, REPLICA)
            f["filling"] = len(owners)
        buffer = not f["buffering"]
        f["buffering"] = True
    with turns:
        turns.notify_all()
    if jumped:
        log(f"hls {key}: its player jumped to piece {head} (from {old})")
        drop_far(key, head, span)
        track = f.get("audio")
        if track and track[1] == (f.get("variant") == "orig"):
            for m in (head, head + 1):
                if m < total:
                    threading.Thread(target=quietly, args=(audio_piece, key, m, *track), daemon=True).start()
    f["wake"].set()
    if time.time() - f["claimed"] > 600:
        f["claimed"] = time.time()
        threading.Thread(target=quietly, args=(claim_reader, key), daemon=True).start()
    if buffer:
        threading.Thread(target=quietly, args=(buffer_film, key), daemon=True).start()
    if start:
        if not f.get("fill_end"):
            log(f"hls {key}: converting the whole film in the background, on {len(owners)} copies")
        for k, owner in enumerate(owners):
            threading.Thread(target=fill_film, args=(key, k, owner, len(owners)), daemon=True).start()


audio_fill = threading.BoundedSemaphore(AUDIO_RUNS)
sound_asks = threading.BoundedSemaphore(SOUND_ASKS)
sound_off = {}


def fill_audio(key, m):









    f, info = films.get(key), probed.get(key)
    if f is None or info is None:
        return
    with hls_lock:
        due = f.setdefault("sound_due", {})
        due.setdefault(m, time.time())
        if f.get("sounding"):
            return
        f["sounding"] = True

    def track():
        a, orig = f.get("audio") or (info["a"], False)
        return None if a < 0 or orig else a

    def make(ns):
        try:
            a = track()
            ns = [n for n in ns if a is not None and not piece_has(f"{key}:ca{a}", n) and in_ram(key, n, a, False)]

            try:
                if len(ns) > 1 and info.get("pos"):
                    return audio_run(key, ns, a)
            except Exception as err:
                log(f"hls {key}: the sound of pieces {ns[0]}-{ns[-1]}: {err}")
            for n in ns:
                quietly(audio_piece, key, n, a, False)
        finally:
            audio_fill.release()

    def ask(owner, ns):
        a = track()
        ns = [n for n in ns if a is not None and not piece_has(f"{key}:ca{a}", n)]
        try:
            for n, data in (sound_of(key, owner, ns, a) if ns else {}).items():
                if not piece_has(f"{key}:ca{a}", n):
                    piece_put(f"{key}:ca{a}", n, data)
        except Exception as err:
            sound_off[owner] = time.time() + 60
            log(f"hls {key}: the sound of pieces {ns[0]}-{ns[-1]} from copy {owner}: {err}")
        finally:
            sound_asks.release()
        rest = [n for n in ns if not piece_has(f"{key}:ca{a}", n)]
        if rest:
            audio_fill.acquire()
            make(rest)
    while True:
        time.sleep(1)
        now, where, owners = time.time(), f.get("fill", {}).get("where", {}), f.get("owners") or []
        with turns:
            landing = any(landing_pending(k) for k in list(films))
        with hls_lock:
            theirs, rest = {}, []
            for n, at in due.items():
                o, made = where.get(n), (key, n) in pieces or (key, n) in mem_pieces
                if o is None and made and owners:
                    o = piece_owner(key, n, REPLICA)
                if o is not None and o != REPLICA and sound_off.get(o, 0) < now and info.get("pos"):
                    theirs.setdefault(o, []).append((at, n))
                else:
                    rest.append((from_player(key, n), at, n, made or n in f.get("done", ())))
            asks = [(o, [n for _, n in sorted(x)[:AUDIO_BATCH]]) for o, x in theirs.items() if len(x) >= AUDIO_BATCH or now - min(x)[0] >= SOUND_HOLD]
        for o, ns in asks:
            if not sound_asks.acquire(blocking=False):
                break
            with hls_lock:
                for n in ns:
                    due.pop(n, None)
            threading.Thread(target=ask, args=(o, ns), daemon=True).start()
        mine, gone, a = [], [], track()
        for _, at, n, made in sorted(rest)[:4 * AUDIO_BATCH]:
            if a is None or in_ram(key, n, a, False):
                mine.append(n)
            elif made or now - at > SOUND_WAIT:
                gone.append(n)
        mine = mine[:AUDIO_BATCH]
        with hls_lock:
            for n in gone:
                due.pop(n, None)
        if mine and not landing and audio_fill.acquire(blocking=False):
            with hls_lock:
                for n in mine:
                    due.pop(n, None)
            threading.Thread(target=make, args=(mine,), daemon=True).start()
        with hls_lock:
            if not due:
                f["sounding"] = False
                return


def sound_of(key, owner, ns, a):


    st, _, body = conns.request("GET", f"{url_of(owner)}/hls/{films[key]['token']}/ca{a}/sound?ns={','.join(map(str, ns))}&{film_q(key)}",
                                headers={"X-Tor-Key": CHUNK_KEY}, timeout=30)
    if st != 200:
        raise RuntimeError(f"HTTP {st} {body[:120].decode(errors='replace')}")
    sizes, _, data = body.partition(b"\n")
    got, at = {}, 0
    for n, size in json.loads(sizes):
        if n in ns and is_ts(data[at:at + size]):
            got[n] = data[at:at + size]
        at += size
    return got


def sound_here(key, token, ns, a):





    if key not in probed:
        def ask():
            st, _, body = conns.request("GET", f"{url_of(src_meta[key]['reader'])}/hls/{token}/probe", headers={"X-Tor-Key": CHUNK_KEY}, timeout=20)
            info = json.loads(gzip.decompress(body)).get("info") if st == 200 else None
            if info:
                probed.setdefault(key, info)
        once("sound-probe", key, 0, ask)
    info = probed.get(key)
    if not info or not info.get("pos") or a not in [x["i"] for x in info["audios"]]:
        return {}
    ns = [n for n in ns if 0 <= n < len(info["cuts"]) - 1 and in_ram(key, n, a, True)]
    if not ns:
        return {}
    with audio_fill:
        return audio_run(key, ns, a, keep=False) if len(ns) > 1 else {ns[0]: audio_make(key, ns[0], a, False)[1]}


def fill_next(f, key, me, group=False):









    fl, head, total = f["fill"], f["head"], f["total"]
    beyond = max(head, min(f["buffer_end"], head + FILL_FROM))
    span = max(FILL_RUNS, int(FILL_CHUNK * total / (probed.get(key, {}).get("size") or 1)))

    def free(m):
        return ((m >= beyond or m < head) and m not in f["done"] and m not in fl["taken"] and (key, m) not in pieces
                and (key, m) not in mem_pieces and (key, m) not in mac_jobs)

    def stretch(c):
        return sorted((x for x in range(c * span, min(total, c * span + span)) if free(x)), key=lambda x: x < head)
    info = probed.get(key) or {}
    pos = info.get("pos") if group and info.get("pend") and (not MUXED or info.get("a", -1) < 0) else None

    def pick(m):
        if not group:
            return m
        a = b = m
        while pos and b - a + 1 < RUN_MAX and b + 1 < total and pos[b + 1] == pos[m] and free(b + 1):
            b += 1
        while pos and b - a + 1 < RUN_MAX and a > 0 and pos[a - 1] == pos[m] and free(a - 1):
            a -= 1
        return list(range(a, b + 1))
    c = fl["claim"].get(me)
    m = stretch(c) if c is not None else []
    if m:
        return pick(m[0])
    others, first = set(fl["claim"].values()) - {c}, None
    for x in itertools.chain(range(beyond, total), range(head - 1, -1, -1)):
        if free(x):
            if x // span not in others:
                fl["claim"][me] = x // span
                return pick(stretch(x // span)[0])
            first = x if first is None else first
    fl["claim"].pop(me, None)
    return None if first is None else pick(stretch(first // span)[-1])


def fill_film(key, k, owner, count):







    f = films[key]
    if "fill" not in f:
        try:
            with open(f"{SEG_DIR}/{key}/where") as fh:
                where = {int(a): int(b) for a, b, t in (line.split() for line in fh if len(line.split()) == 3)
                         if time.time() - float(t) < 6 * 3600}
        except (OSError, ValueError):
            where = {}
        with hls_lock:
            if "fill" not in f:
                f["fill"] = {"taken": {}, "where": where, "claim": {}}
                f["done"] |= set(where)
    fl = f["fill"]
    state, over, threads = {"ok": time.time(), "stale": 0}, threading.Event(), []

    def fill(idx):
        while time.time() - f["seen"] < FILM_IDLE and not over.is_set():
            if idx and not watched(key):
                time.sleep(5)
                continue
            with hls_lock:
                run = fill_next(f, key, owner, True)
                for x in run or []:
                    fl["taken"][x] = owner
                left = bool(fl["taken"])
            if run is None and left:
                if idx:
                    break
                time.sleep(1)
                continue
            if run is None:
                over.set()
                break
            m, late, asked = run[0], {}, run
            timer = threading.Timer(FILL_STALE * max(1, len(run) / 4), stale, args=(late, idx))
            timer.start()
            t0 = time.time()
            try:
                q = piece_q(key, m) + (run_q(key, run) if len(run) > 1 else "")
                while True:
                    st, _, body = conns.request("GET", f"{url_of(owner)}/hls/{f['token']}/s{m}?{q}&prep=wait",
                                                headers={"X-Tor-Key": CHUNK_KEY}, timeout=1800)
                    if st != 524 or time.time() - t0 > 1800 or time.time() - f["seen"] > FILM_IDLE:
                        break
            except Exception:
                st = None
            timer.cancel()
            if st in (200, 204) and len(run) > 1 and f'"run": {run[-1]}'.encode() not in (body or b""):
                run = run[:1]
            with hls_lock:
                late["over"] = True
                for x in asked:
                    fl["taken"].pop(x, None)
                if st in (200, 204):
                    f["done"].update(run)
                    fl["where"].update((x, owner) for x in run)
                    state["ok"] = time.time()
                else:
                    fl["claim"].pop(owner, None)
                state["stale"] -= 1 if "t" in late else 0
            if st in (200, 204):
                for x in run:
                    threading.Thread(target=quietly, args=(fill_audio, key, x), daemon=True).start()
                where_keep(key, run, owner)
            elif time.time() - state["ok"] > FILL_STALE:
                over.set()
            else:
                time.sleep(5)
            if "t" in late:
                return

    def stale(late, idx):
        with hls_lock:
            if "over" in late or state["stale"] >= FILL_RUNS or over.is_set():
                return
            state["stale"] += 1
            late["t"] = time.time()
            fl["claim"].pop(owner, None)
            t = threading.Thread(target=fill, args=(idx,), daemon=True)
            t.start()
            threads.append(t)
    try:
        for i in range(FILL_RUNS):
            threads.append(threading.Thread(target=fill, args=(i,), daemon=True))
            threads[-1].start()
        while threads:
            threads.pop(0).join()
    finally:
        with hls_lock:
            f["filling"] -= 1
            f["fill_end"] = time.time()
            fl["claim"].pop(owner, None)


def uncached(path):



    return re.sub(r"\.([A-Za-z0-9]{1,5})\Z", r"_\1", path)


def scrape(ihs, timeout=1.5):


    def ask(host, port):
        got, sock = {}, None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            addr = (socket.gethostbyname(host), port)
            tid = random.getrandbits(32)
            sock.sendto(struct.pack(">QII", 0x41727101980, 0, tid), addr)
            action, rtid, conn = struct.unpack(">IIQ", sock.recv(16))
            if action != 0 or rtid != tid:
                return got
            for i in range(0, len(ihs), 70):
                part, tid = ihs[i:i + 70], random.getrandbits(32)
                sock.sendto(struct.pack(">QII", conn, 2, tid) + b"".join(bytes.fromhex(h) for h in part), addr)
                data = sock.recv(8 + 12 * len(part))
                if struct.unpack(">II", data[:8]) != (2, tid):
                    break
                for k, h in enumerate(part[:(len(data) - 8) // 12]):
                    seeds, _, leech = struct.unpack(">III", data[8 + 12 * k:20 + 12 * k])
                    got[h] = (seeds, leech)
        except Exception:
            pass
        finally:
            if sock:
                sock.close()
        return got
    out = {}
    with concurrent.futures.ThreadPoolExecutor(len(SCRAPERS)) as pool:
        for got in pool.map(lambda hp: ask(*hp), SCRAPERS):
            for h, (seeds, leech) in got.items():
                a = out.get(h, (0, 0))
                out[h] = (max(a[0], seeds), max(a[1], leech))
    return out


def torrentio(kind, sid):

    req = urllib.request.Request(f"{TORRENTIO}/stream/{kind}/{sid}.json", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("streams", [])


def resolution(stream):

    name = (stream.get("name") or "").lower()
    for rank, marks in enumerate((("4k", "2160"), ("1080",), ("720",), ("480", "sd"))):
        if any(m in name for m in marks):
            return rank
    return 4


def ranked(found):



    rows = [{"s": s, "ih": s["infoHash"].lower(), "idx": s["fileIdx"] if s.get("fileIdx") is not None else -1}
            for s in found if s.get("infoHash")]
    live = scrape([r["ih"] for r in rows])
    try:
        held = {k[len(JOB):] for k in kv.keys(JOB)}
    except Exception:
        held = set()
    for n, r in enumerate(rows):
        r["seeds"], r["leech"] = live.get(r["ih"], (None, None))
        r["fleet"], r["n"] = hid(r["ih"]) in held, n
    return sorted(rows, key=lambda r: (not r["fleet"], resolution(r["s"]), -(r["seeds"] or 0), r["n"]))


probes = {}
probe_lock = threading.Lock()


def first_piece(ih, idx, deadline):


    t0, ends = time.time(), {}
    while time.time() < deadline:
        job = current_job(ih, idx)
        if not job:
            time.sleep(0.5)
            continue
        n = min(SEG, job["size"])
        spans = [(0, n - 1), (job["size"] - min(2**20, n), job["size"] - 1)]
        wait = max(0.0, min(5.0, deadline - time.time()))
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            got = list(pool.map(lambda sp: ends.get(sp) or from_helper(job, sp[0], sp[1], wait=wait), spans))
        ends.update({sp: True for sp, data in zip(spans, got) if data is not None})
        if len(ends) == 2:
            return time.time() - t0
    return None


def probe(title, rows):




    with probe_lock:
        p = probes.get(title)
        fresh = not p or time.time() - p["t"] > PROBED_TTL
        if fresh:
            p = probes[title] = {"t": time.time(), "ready": {}, "done": threading.Event()}
    if not fresh:
        p["done"].wait(PROBE_WAIT + 5)
        return dict(p["ready"])
    deadline = time.time() + PROBE_WAIT
    cands = [r for r in rows if r["seeds"] != 0][:PROBE]

    def one(r):
        took = first_piece(r["ih"], r["idx"], deadline)
        if took is not None:
            p["ready"][r["ih"]] = took
            if len(p["ready"]) <= PROBE_WANT:
                warm(r["ih"], r["idx"], level=2)
            if len(p["ready"]) >= PROBE_WANT:
                p["done"].set()
    for r in cands:
        warm(r["ih"], r["idx"], level=1)
        threading.Thread(target=one, args=(r,), daemon=True).start()
    p["done"].wait(max(0.0, deadline - time.time()))
    p["done"].set()
    log(f"{title}: {len(p['ready'])} of {len(cands)} streams start fast, in {time.time() - (deadline - PROBE_WAIT):.1f}s")
    return dict(p["ready"])


def ready_best(kind, sid, why, quiet=False):

    try:
        best = ranked(torrentio(kind, sid))
    except Exception as err:
        if not quiet:
            raise
        log(f"{why}: {sid}: {err.__class__.__name__}: {err}")
        return
    if best and best[0]["seeds"] != 0:
        warm(best[0]["ih"], best[0]["idx"])
        log(f"{why}: readied {sid} ({best[0]['seeds']} seeders now)")


MANIFEST = {"id": "fleet.tor", "version": "1.0.0", "name": "Frame torrents",
            "description": "Torrentio's results, streamed by the fleet: many servers fetch each file at once and keep it in RAM. "
                           "The streams that start fastest first.",
            "resources": ["stream"], "types": ["movie", "series"], "idPrefixes": ["tt"], "catalogs": []}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def base(self):
        host = self.headers.get("Host") or f"127.0.0.1:{PAGE_PORT}"
        return f"{'http' if PEERS else 'https'}://{host}"

    def do_HEAD(self):
        self.do_GET()

    def do_PUT(self):
        p = urllib.parse.urlparse(self.path).path
        if p.startswith("/kv/"):
            return self.kv("PUT", p)
        self.send(405, {"error": "GET only"})

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path).path
        if p.startswith("/kv/"):
            return self.kv("DELETE", p)
        self.send(405, {"error": "GET only"})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/kv-sync", "/kv-keeper"):
            return self.kv_peer(p, {})
        self.send(405, {"error": "GET only"})

    def kv(self, method, p):


        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        if not KV_PASSWORD or not hmac.compare_digest(self.headers.get("x-password") or "", KV_PASSWORD):
            return self.send(401, {"error": "the fleet's copies only"})
        k = keeper()
        if k == REPLICA and not store.serving and not store.retired:
            store.ready.wait(5)
        if not store.serving:
            self.close_connection = True
            if k is None or k == REPLICA:
                return self.send(503, {"error": "the store isn't kept here"}, extra={"Connection": "close"})
            return self.send(421, {"keeper": k}, extra={"Connection": "close"})
        key = urllib.parse.unquote(p[len("/kv/"):]) if p.startswith("/kv/") else None
        im = self.headers.get("If-Match")
        st, data, h = store.answer(method, key, body, int(im) if im and im.isdigit() else None)
        self.send(st, data, "application/octet-stream" if key else "application/json", extra=h)

    def kv_peer(self, p, query):



        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY):
            return self.send(403, {"error": "the fleet's copies only"})
        body = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw or b"{}")
        if self.command == "GET":
            out = store.since((query.get("lineage") or [""])[0], int((query.get("seq") or ["0"])[0] or 0))
        elif p == "/kv-keeper":
            k, kid = int(body["keeper"]), body.get("id")
            if k < REPLICA or (k == REPLICA and kid != BOOT):
                hint(k)
                store.keeper_id = (k, kid)
                if store.serving:
                    step_down(k)
                    store.retired = k == REPLICA
            elif keeper() is None or k <= keeper():
                store.keeper_id = (k, kid)
            out = dict(store.state(), id=BOOT)
        else:
            sender, sid = int(body.get("sender", 0)), body.get("id")
            kid = store.keeper_id
            if kid and kid[0] == sender and kid[1] != sid:
                return self.send(200, {"ok": False, "retire": True})
            if store.serving and sender > REPLICA:
                return self.send(200, {"ok": False, "keeper": REPLICA})
            if store.serving:
                step_down(sender)
            store.keeper_id = (sender, sid)
            ok = store.apply(body)
            out = {"ok": ok, "lineage": store.lineage, "seq": store.seq, "keeper": keeper()}
        self.send(200, gzip.compress(json.dumps(out).encode(), 1), "application/json")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path)
        p = path.path
        try:
            if p == "/kv" or p.startswith("/kv/"):
                return self.kv("GET", p)
            if p == "/kv-sync":
                return self.kv_peer(p, urllib.parse.parse_qs(path.query, keep_blank_values=True))
            if p in ("/", "/index.html"):
                return self.send(200, PAGE.replace("__BASE__", PUBLIC_URL or self.base()), "text/html; charset=utf-8")
            if p == "/healthz":
                return self.send(200, {"ok": True, "app": APP, "replica": REPLICA, "code": CODE})
            if p == "/status":
                return self.send(200, status())
            if p == "/api/log":
                return self.send(200, "\n".join(recent) + "\n", "text/plain; charset=utf-8")
            if p == "/manifest.json":
                return self.send(200, MANIFEST)
            if p == "/browser/manifest.json":
                return self.send(200, MANIFEST_BROWSER)
            if p in ("/watch", "/browser", "/browser/"):
                return self.send(200, watch_page(), "text/html; charset=utf-8", extra={"Cache-Control": "no-cache"})
            if p in ("/api/search", "/api/meta", "/api/fleet"):
                return self.watch_api(p, urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/gw/([a-z0-9.-]+)/(.+)", p)
            if m:
                return self.gw(m.group(1), m.group(2), path.query)
            m = re.fullmatch(r"/(file|dl)/([A-Za-z0-9_-]+)/([^/]+)", p)
            if m:
                return self.file(m.group(2), m.group(3), m.group(1) == "dl")
            if p in ("/manifest.webmanifest", "/sw.js") or re.fullmatch(r"/pwa/[a-z0-9-]+\.png", p):
                name = p.split("/")[-1]
                try:
                    data = web_file(name)
                except (KeyError, OSError):
                    return self.send(404, {"error": "not found"})
                return self.send(200, data, {"webmanifest": "application/manifest+json", "js": "text/javascript; charset=utf-8"}.get(name.split(".")[-1], "image/png"),
                                 extra={"Cache-Control": "no-cache" if name == "sw.js" else "public, max-age=86400"})
            m = re.fullmatch(r"/browser/stream/(movie|series)/([^/]+)\.json", p)
            if m:
                return self.browser(m.group(1), m.group(2), "as_is" in urllib.parse.parse_qs(path.query))
            if p in ("/browser/facts", "/facts"):
                return self.facts(urllib.parse.parse_qs(path.query), p == "/facts")
            m = re.fullmatch(r"/hls/([A-Za-z0-9_-]+)/(index|cmedia|orig|omedia|[oc]a\d+)\.m3u8", p)
            if m:
                return self.hls_lists(m.group(1), m.group(2), urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/hls/([A-Za-z0-9_-]+)/([oc])a(\d+)/(init|\d+)", p)
            if m:
                return self.hls_audio(m.group(1), m.group(2) == "o", int(m.group(3)), -1 if m.group(4) == "init" else int(m.group(4)),
                                      urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/hls/([A-Za-z0-9_-]+)/ca(\d+)/sound", p)
            if m:
                return self.hls_sound(m.group(1), int(m.group(2)), urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/hls/([A-Za-z0-9_-]+)/s(\d+)", p)
            if m:
                return self.hls_piece(m.group(1), int(m.group(2)), urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/hls/([A-Za-z0-9_-]+)/o(init|\d+)", p)
            if m:
                return self.hls_piece(m.group(1), -1 if m.group(2) == "init" else int(m.group(2)), urllib.parse.parse_qs(path.query), orig=True)
            m = re.fullmatch(r"/hls/([A-Za-z0-9_-]+)/at(\d+)", p)
            if m:
                return self.hls_at(m.group(1), int(m.group(2)), urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/hls/([A-Za-z0-9_-]+)/probe", p)
            if m:
                return self.probe(m.group(1))
            m = re.fullmatch(r"/hls/([A-Za-z0-9_-]+)/h(\d+)", p)
            if m:
                return self.hls_hint(m.group(1), int(m.group(2)), urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/src/([0-9a-f]{16})", p)
            if m:
                return self.src(m.group(1), urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/blk/([A-Za-z0-9_-]+)/(\d+)", p)
            if m:
                return self.blk(m.group(1), int(m.group(2)), urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/stream/(movie|series)/([^/]+)\.json", p)
            if m:
                return self.streams(m.group(1), m.group(2))
            m = re.fullmatch(r"/chunk/([0-9a-f]{40})", p)
            if m:
                return self.chunk(m.group(1), urllib.parse.parse_qs(path.query))
            m = re.fullmatch(r"/play/([0-9a-fA-F]{40})/(-?\d+)(?:/.*)?", p)
            if m:
                return self.play(m.group(1).lower(), int(m.group(2)), urllib.parse.parse_qs(path.query))
            self.send(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def streams(self, kind, sid):



        try:
            rows = ranked(torrentio(kind, sid))
        except Exception as err:
            return self.send(200, {"streams": [], "error": str(err)})
        m = re.fullmatch(r"(tt\d+):(\d+):(\d+)", sid)
        if kind == "series" and m:
            nxt = f"{m.group(1)}:{m.group(2)}:{int(m.group(3)) + 1}"
            threading.Thread(target=ready_best, args=("series", nxt, f"after {sid}", True), daemon=True).start()
        ready = probe(f"{kind}/{sid}", rows)
        rows.sort(key=lambda r: (r["ih"] not in ready, ready.get(r["ih"], 0)))
        out = []
        for r in rows:
            s, ih, idx = r["s"], r["ih"], r["idx"]
            name = (s.get("behaviorHints") or {}).get("filename") or "video"
            live = f"\n🟢 {r['seeds']} seeding now · {r['leech']} downloading" if r["seeds"] is not None else ""
            fast = f"\n⚡ starts at once (in the fleet after {ready[ih]:.0f}s)" if ih in ready else ""
            out.append({"name": ("Fleet ⚡" if ih in ready else "Fleet") + "\n" + (s.get("name") or "").replace("Torrentio\n", ""),
                        "title": s.get("title", "") + live + fast,
                        "url": f"{PUBLIC_URL or self.base()}/play/{ih}/{idx}/{urllib.parse.quote(uncached(name))}",
                        "behaviorHints": {"notWebReady": True, "bingeGroup": (s.get("behaviorHints") or {}).get("bingeGroup", "fleet")}})
        self.send(200, {"streams": out}, extra={"Cache-Control": "max-age=60"})

    def chunk(self, ih, query):


        if not hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY):
            return self.send(403, {"error": "the fleet's copies only"})
        try:
            off, n = int(query["off"][0]), int(query["n"][0])
            wait = min(10.0, float((query.get("wait") or ["0"])[0]))
        except (KeyError, ValueError):
            return self.send(400, {"error": "off and n"})
        try:
            data = local_chunk(ih, off, min(n, 16 * 2**20), wait)
        except Exception as err:
            log(f"{ih[:8]}: reading a chunk: {err.__class__.__name__}: {err}")
            data = None
        if data is None:
            return self.send(404, {"error": "not here (yet)"})
        self.send(200, data, "application/octet-stream")

    def here(self, **params):

        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.urlencode({**dict(urllib.parse.parse_qsl(u.query)), **params})
        return uncached(u.path) + (f"?{q}" if q else "")

    def watch_api(self, p, query):

        try:
            if p == "/api/fleet":
                return self.send(200, {"copies": len(copies())})
            if p == "/api/search":
                q = (query.get("q") or [""])[0].strip()
                return self.send(200, title_search(q) if len(q) >= 2 else [], extra={"Cache-Control": "max-age=300"})
            kind, tid = (query.get("type") or [""])[0], (query.get("id") or [""])[0]
            if kind not in ("movie", "series") or not re.fullmatch(r"tt\d+", tid):
                return self.send(400, {"error": "type and id"})
            return self.send(200, title_meta(kind, tid), extra={"Cache-Control": "max-age=600"})
        except Exception as err:
            return self.send(503, {"error": f"{err.__class__.__name__}: {err}"})

    def gw(self, host, rest, query):


        if host not in GW_HOSTS or ".." in rest or not re.fullmatch(r"[A-Za-z0-9._~%@=:,+-][A-Za-z0-9._~%@=:,+/-]*", rest) \
                or not re.fullmatch(r"[A-Za-z0-9._~%@=&,+-]*", query):
            return self.send(404, {"error": "not one of this page's sources"})
        try:
            got = gateway(host, rest, query)
        except Exception as err:
            return self.send(503, {"error": f"{host}: {err.__class__.__name__}: {err}"}, extra={"Cache-Control": "no-store"})
        cc = f"public, max-age={got['ttl']}"
        if got["etag"] in [t.strip() for t in (self.headers.get("If-None-Match") or "").split(",")]:
            self.send_response(304)
            self.send_header("ETag", got["etag"])
            self.send_header("Cache-Control", cc)
            self.end_headers()
            return
        body, extra = got["body"], {"Cache-Control": cc, "ETag": got["etag"], "Vary": "Accept-Encoding"}
        if got["gz"]:
            if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                extra["Content-Encoding"] = "gzip"
            else:
                body = gzip.decompress(body)
        self.send(got["status"], body, got["type"], extra=extra)

    def browser(self, kind, sid, as_is=False):


        try:
            out = browser_streams(kind, sid, PUBLIC_URL or self.base(), as_is)
        except Exception as err:
            return self.send(200, {"streams": [], "error": str(err)})
        self.send(200, {"streams": out, "r": REPLICA}, extra={"Cache-Control": "max-age=60"})


    def facts(self, query, own):




        if own and not hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY):
            return self.send(403, {"error": "the fleet's copies only"})
        keys = [k for k in (query.get("k") or [""])[0].split(",") if re.fullmatch(r"[0-9a-f]{16}", k)][:BROWSER_MAX + 50]
        at = (query.get("r") or [""])[0]
        self.send(200, facts_lookup(keys, ask=not own, at=int(at) if at.isdigit() else None), extra={"Cache-Control": "no-store"})

    def hls_lists(self, token, which, query):








        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        master, orig = which in ("index", "orig"), which[0] == "o"
        own = hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY)
        reader = REPLICA if master else int((query.get("r") or [REPLICA])[0])
        if master and not own:
            if key not in probed:
                lookup_begin(key)
            reader = reader_of(key) or claim_reader(key)
        direct = master and (query.get("d") or [""])[0] == "1"
        if reader != REPLICA:
            try:
                st, h, body = conns.request("GET", f"{url_of(reader)}/hls/{token}/{which}.m3u8?r={reader}" + ("&d=1" if direct else ""),
                                            headers={"X-Tor-Key": CHUNK_KEY}, timeout=60)
            except Exception as err:
                return self.send(503, {"error": f"the reader, copy {reader}: {err}"}, extra={"Cache-Control": "no-store"})
            return self.send(st if st < 500 else 503, body, h.get("Content-Type") or "application/vnd.apple.mpegurl", extra={"Cache-Control": "no-store"})
        src_meta[key]["reader"] = REPLICA
        facts_urgent(key)
        for attempt in range(3):
            try:
                info = probe_source(key)
                break
            except SourceBroken as err:
                log(f"hls {key}: {err}")
                return self.send(503, {"error": f"this file can't play: {err}"}, extra={"Cache-Control": "no-store"})
            except Exception as err:
                log(f"hls {key}: {err.__class__.__name__}: {err}")
                if attempt == 2:
                    return self.send(503, {"error": str(err)}, extra={"Cache-Control": "no-store"})
                time.sleep(2)
        why = orig_unfit(info) if orig else None
        if why:
            return self.send(404, {"error": f"this version can't play as it is: {why}"}, extra={"Cache-Control": "no-store"})
        cuts = info["ocuts"] if orig else info["cuts"]
        if master:
            film_heard(key, token, 0, "orig" if orig else None)
            first_pieces(key, info, orig, True)
            group = ',AUDIO="a"' if info["audios"] else ""
            at = f"{url_of(REPLICA)}/hls/{token}/" if direct else ""


            lines = ["#EXTM3U", "#EXT-X-VERSION:7" if orig else "#EXT-X-VERSION:4", "#EXT-X-INDEPENDENT-SEGMENTS"] + \
                    [x.replace('URI="', 'URI="' + at) for x in audio_renditions(info, orig)]
            if orig:
                bw = int(info["size"] * 8 / max(1, info["duration"]))
                lines += [f'#EXT-X-STREAM-INF:BANDWIDTH={int(bw * 1.6)},AVERAGE-BANDWIDTH={bw},CODECS="{orig_codecs(info)}",'
                          f'RESOLUTION={info["w"]}x{info["h"]},VIDEO-RANGE={info["range"]},FRAME-RATE={info["fps"]:.3f}{group}', f"{at}omedia.m3u8?r={REPLICA}"]
            else:
                w, h = (1920, info["h"] * 1920 // max(1, info["w"]) // 2 * 2) if info["w"] > 1920 else (info["w"], info["h"])
                lines += [f'#EXT-X-STREAM-INF:BANDWIDTH=14000000,AVERAGE-BANDWIDTH=9000000,CODECS="avc1.640029{",mp4a.40.2" if info["audios"] else ""}",'
                          f'RESOLUTION={w}x{h},FRAME-RATE={info["fps"]:.3f}{group}', f"{at}cmedia.m3u8?r={REPLICA}"]
        else:
            a = None if which in ("omedia", "cmedia") else int(which[2:])
            if a is not None and a not in [x["i"] for x in info["audios"]]:
                return self.send(404, {"error": f"no audio track {a}"})
            longest = max(y - x for x, y in zip(cuts, cuts[1:]))
            lines = ["#EXTM3U", "#EXT-X-VERSION:7" if orig else "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{math.ceil(longest)}",
                     "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS"]
            if orig:
                lines.append(f'#EXT-X-MAP:URI="{"oinit" if a is None else which + "/init"}?r={REPLICA}"')
            name = {"omedia": "o", "cmedia": "s"}.get(which, which + "/")
            for i in range(len(cuts) - 1):
                lines += [f"#EXTINF:{cuts[i + 1] - cuts[i]:.3f},", f"{name}{i}?r={REPLICA}"]
            lines.append("#EXT-X-ENDLIST")
        self.send(200, "\n".join(lines) + "\n", "application/vnd.apple.mpegurl", extra={"Cache-Control": "no-store"})

    def begin_piece(self, ctype):





        self.send_response(200)
        for k, v in (("Content-Type", ctype), ("Cache-Control", "no-store"), ("Access-Control-Allow-Origin", "*"), ("Transfer-Encoding", "chunked")):
            self.send_header(k, v)
        self.end_headers()
        self.early = {"lock": threading.Lock(), "done": threading.Event(), "bytes": EARLY_TS if ctype == "video/mp2t" else EARLY_MP4}
        if self.early["bytes"]:
            self.chunk(self.early["bytes"])
            threading.Thread(target=self.keep_early, daemon=True).start()
        else:
            self.wfile.flush()

    def chunk(self, data):

        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def keep_early(self):

        e = self.early
        while not e["done"].wait(EARLY_EVERY):
            with e["lock"]:
                if e["done"].is_set():
                    return
                try:
                    self.chunk(e["bytes"])
                except OSError:
                    return

    def end_piece(self, data):

        e = self.early
        with e["lock"]:
            e["done"].set()
            if data is None:
                self.close_connection = True
            else:
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n")

    def hls_audio(self, token, orig, a, n, query):



        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        try:
            reader = int(query["r"][0])
        except (KeyError, ValueError):
            return self.send(400, {"error": "r"})
        own = hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY)
        pk, ctype = f"{key}:{'o' if orig else 'c'}a{a}", "audio/mp4" if orig else "video/mp2t"
        data = piece_get(pk, n) if reader == REPLICA else None
        early = data is None and not own and self.command == "GET"
        if early:
            self.begin_piece(ctype)
        t0, how, code, err = time.time(), "audio", 503, None
        if data is None and reader != REPLICA:
            how = "audio from the reader"
            try:
                st, _, body = conns.request("GET", f"{url_of(reader)}/hls/{token}/{'o' if orig else 'c'}a{a}/{'init' if n < 0 else n}?r={reader}",
                                            headers={"X-Tor-Key": CHUNK_KEY}, timeout=120)
                if st == 200 and body:
                    data = body
                else:
                    code, err = st if 400 <= st < 500 else 503, f"the reader, copy {reader}: HTTP {st} {body[:200].decode(errors='replace')}"
            except Exception as e:
                err = f"the reader, copy {reader}: {e.__class__.__name__}: {e}"
        elif data is None:
            try:
                info = probe_source(key)
                if a not in [x["i"] for x in info["audios"]]:
                    code, err = 404, f"no audio track {a}"
                elif n >= len(info["ocuts"] if orig else info["cuts"]) - 1:
                    code, err = 404, "past the film's end"
                else:
                    f = films.get(key)
                    if f is not None:
                        f["audio"] = (a, orig)
                    data = patiently(key, max(0, n), lambda: audio_piece(key, n, a, orig) if n >= 0 else
                                     init_piece(key, pk, lambda m: audio_piece(key, m, a, orig)))
                    err = None if data is not None else f"audio piece {n} of track {a}: not made"
            except Exception as e:
                log(f"hls {key}: audio piece {n} of track {a}: {e}")
                err = str(e)
        if data is None:
            return self.end_piece(None) if early else self.send(code, {"error": err}, extra={"Cache-Control": "no-store"})
        took = time.time() - t0
        if n >= 0:
            bstats["served"].append((int(time.time()), n, how, round(took, 2)))
        if took > 2:
            log(f"hls {key}: audio piece {n} of track {a} for a player took {took:.1f}s ({how}{'; passed on' if own else ''})")
        if early:
            return self.end_piece(data)
        self.send(200, data, ctype, extra={"Cache-Control": "no-store"})

    def hls_sound(self, token, a, query):



        if not hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY):
            return self.send(403, {"error": "the fleet's copies only"})
        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        m = src_meta[key]
        try:
            ns = [int(x) for x in query["ns"][0].split(",")][:2 * AUDIO_BATCH]
            m["reader"] = int(query["r"][0])
            for name, q, kind in (("size", "z", int), ("duration", "d", float), ("start", "s", float)):
                if m.get(name) is None and query.get(q):
                    m[name] = kind(query[q][0])
        except (KeyError, ValueError):
            return self.send(400, {"error": "ns, r"})
        try:
            got = sound_here(key, token, ns, a)
        except Exception as err:
            return self.send(502, {"error": f"{err.__class__.__name__}: {err}"})
        self.send(200, json.dumps([[n, len(d)] for n, d in got.items()]).encode() + b"\n" + b"".join(got.values()), "application/octet-stream")

    def hls_piece(self, token, n, query, orig=False):




        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        try:
            reader = int(query["r"][0])
        except (KeyError, ValueError):
            return self.send(400, {"error": "r"})
        own = hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY)
        prep = (query.get("prep") or [""])[0]
        if own and prep in ("get", "1", "wait"):
            try:
                a, hdr, w = int(query["a"][0]), int(query["h"][0]), int(query["w"][0])
            except (KeyError, ValueError):
                return self.send(400, {"error": "a, h, w"})
            cut = query_cut(query)
            if cut is None:
                return self.send(400, {"error": "t, l"})
            m = src_meta[key]
            m["reader"] = reader
            if query.get("z") and not m.get("size"):
                m["size"] = int(query["z"][0])
            if query.get("d") and not m.get("duration"):
                m["duration"] = float(query["d"][0])
            if query.get("s") and m.get("start") is None:
                m["start"] = float(query["s"][0])
            if prep == "1":
                if piece_get(key, n) is None:
                    threading.Thread(target=quietly, args=(convert, key, n, a, hdr, w, 1, cut), daemon=True).start()
                return self.send(202, {"ok": True})
            try:
                if query.get("run"):
                    ns, cuts = query_run(n, cut, query)
                    convert_run(key, ns, a, hdr, w, cuts, 1 if prep == "get" else 2)
                    if prep == "wait":
                        return self.send(200, {"ok": True, "run": ns[-1]})
                    return self.send(200, piece_get(key, n) or convert(key, n, a, hdr, w, 1, cut), "video/mp2t", extra={"X-Tor-Run": str(ns[-1])})
                data = convert(key, n, a, hdr, w, 1 if prep == "get" else 2, cut)
            except Exception as err:
                return self.send(502, {"error": str(err)})
            return self.send(200, data, "video/mp2t") if prep == "get" else self.send(200, {"ok": True})
        heard = time.time() if not own else when_asked(query)
        if not own and n >= 0 and not (reader == REPLICA and key in films):
            threading.Thread(target=quietly, args=(tell_reader, token, key, n, reader, "orig" if orig else "conv", heard), daemon=True).start()
        elif n >= 0 and reader == REPLICA and heard:

            try:
                film_heard(key, token, n, "orig" if orig else "conv", heard)
            except Exception as err:
                log(f"hls {key}: {err}")
        data = piece_get(key + ORIG if orig else key, n)
        early = data is None and not own and self.command == "GET"
        if early:
            self.begin_piece("video/mp4" if orig else "video/mp2t")
        how, t0, err = "ready", time.time(), None
        if data is None and reader != REPLICA:
            how = "reader"
            try:
                piece = ("oinit" if n < 0 else f"o{n}") if orig else f"s{n}"
                st, _, body = conns.request("GET", f"{url_of(reader)}/hls/{token}/{piece}?r={reader}&t={heard:.3f}", headers={"X-Tor-Key": CHUNK_KEY}, timeout=120)
                data, err = (body, None) if st == 200 and body else (None, f"the reader, copy {reader}: HTTP {st}")
            except Exception as e:
                err = f"the reader, copy {reader}: {e.__class__.__name__}: {e}"
        elif data is None:
            try:
                info = probe_source(key)
                if n >= len(info["cuts"]) - 1:
                    raise RuntimeError("past the film's end")
                if key not in films:
                    film_heard(key, token, max(0, n), "orig" if orig else None)
                if orig:
                    data, how = patiently(key, max(0, n), lambda: orig_piece(key, n) if n >= 0 else
                                          init_piece(key, key + ORIG, lambda m: orig_piece(key, m))), "here"
                else:
                    data, how = patiently(key, n, lambda: reader_piece(key, n, muxed(info), info["hdr"], info["w"], piece_cut(key, n)))
            except Exception as e:
                err = str(e)
        if data is None:
            log(f"hls {key}: piece {n}: {err}")
            if early:
                return self.end_piece(None)
            return self.send(503, {"error": err}, extra={"Cache-Control": "no-store"})

        f = films.get(key) if reader == REPLICA else None
        landed = f is not None and f.get("landing") == n
        if landed:
            f["landing"] = None
        took = time.time() - t0
        bstats["served"].append((int(time.time()), n, how if not own else how + " (passed on)", round(took, 2)))
        if took > 2 or landed:
            log(f"hls {key}: piece {n} for a player took {took:.1f}s ({how}{'; passed on' if own else ''}{'; landed' if landed else ''})")
        if early:
            return self.end_piece(data)
        self.send(200, data, "video/mp4" if orig else "video/mp2t", extra={"Cache-Control": "no-store"})

    def hls_hint(self, token, n, query):




        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        try:
            reader = int(query["r"][0])
        except (KeyError, ValueError):
            return self.send(400, {"error": "r"})
        orig = (query.get("v") or [""])[0] == "orig"
        if reader != REPLICA:
            try:
                st, _, body = conns.request("GET", f"{url_of(reader)}/hls/{token}/h{n}?r={reader}" + ("&v=orig" if orig else ""),
                                            headers={"X-Tor-Key": CHUNK_KEY}, timeout=5)
            except Exception as err:
                return self.send(503, {"error": f"the reader, copy {reader}: {err}"}, extra={"Cache-Control": "no-store"})
            return self.send(st if st < 500 else 503, body, extra={"Cache-Control": "no-store"})
        now = time.time()
        with hls_lock:
            lately = [t for t in hinted.get(key, []) if now - t < 1]
            taken = len(lately) < HINTS
            hinted[key] = lately + [now] if taken else lately
        info = probed.get(key)
        if not taken or info is None or n >= len(info["ocuts" if orig else "cuts"] or []) - 1 or piece_has(key + ORIG if orig else key, n):
            return self.send(204, b"", extra={"Cache-Control": "no-store"})
        f = films.get(key)
        if orig:
            threading.Thread(target=quietly, args=(orig_piece, key, n), daemon=True).start()
        elif mac_start(key, n, 1, True) is None and f is not None and n not in f["done"]:
            threading.Thread(target=quietly, args=(fetch_piece, key, n), daemon=True).start()
        track = f.get("audio") if f else None
        if track and track[1] == orig:
            threading.Thread(target=quietly, args=(audio_piece, key, n, *track), daemon=True).start()
        self.send(202, {"readying": n}, extra={"Cache-Control": "no-store"})

    def hls_at(self, token, n, query):

        if not hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY):
            return self.send(403, {"error": "the fleet's copies only"})
        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        try:
            film_heard(key, token, n, (query.get("v") or [None])[0], when_asked(query) or None)
        except Exception as err:
            return self.send(502, {"error": str(err)})
        self.send(200, {"ok": True})

    def probe(self, token):


        if not hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY):
            return self.send(403, {"error": "the fleet's copies only"})
        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        f = films.get(key)
        warm = time.time() - src_meta[key].get("warm", 0) < FILM_IDLE
        body = gzip.compress(json.dumps({"reader": (f is not None and time.time() - f["seen"] < FILM_IDLE) or warm, "info": probed.get(key)}).encode(), 1)
        self.send(200, body, "application/octet-stream", extra={"Cache-Control": "no-store"})

    def blk(self, token, b, query):



        if not hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY):
            return self.send(403, {"error": "the fleet's copies only"})
        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        src_meta[key]["reader"] = REPLICA
        urgent = (query.get("u") or ["1"])[0] == "1"
        n = int(query["n"][0]) if (query.get("n") or [""])[0].isdigit() else None
        try:
            data = src_block(key, b, urgent, n)
        except Exception as err:
            return self.send(502, {"error": str(err)})
        for nb in range(b + 1, b + (5 if urgent else 1)):
            if nb * SRC_BLOCK < (src_meta[key].get("size") or 0) and wanted_block(key, nb, urgent):
                threading.Thread(target=quietly, args=(src_block, key, nb, urgent, n), daemon=True).start()
        with send_turn(key, n, urgent):
            self.send(200, data, "application/octet-stream")

    def file(self, token, name, attach):






        opened = open_token(token)
        if not opened:
            return self.send(404, {"error": "unknown stream"})
        key, _ = opened
        fname = re.sub(r"_([A-Za-z0-9]{1,5})\Z", r".\1", urllib.parse.unquote(name))
        rng = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range") or "")
        conn = r = None
        for attempt in (0, 1):
            try:
                m = src_lookup(key, again=bool(attempt))
                size = m["size"]
                start = int(rng.group(1)) if rng else 0
                end = min(int(rng.group(2)), size - 1) if rng and rng.group(2) else size - 1
                if start >= size:
                    return self.send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                if self.command == "HEAD" or not attach:
                    break
                u = urllib.parse.urlsplit(m["url"])
                conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=60)
                conn.request("GET", (u.path or "/") + (f"?{u.query}" if u.query else ""), headers={"Range": f"bytes={start}-{end}", "User-Agent": "Mozilla/5.0"})
                r = conn.getresponse()
                if r.status == 206:
                    break
                conn.close()
                raise RuntimeError(f"the source answered HTTP {r.status}")
            except Exception as err:
                if attempt:
                    return self.send(503, {"error": str(err)}, extra={"Cache-Control": "no-store"})
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", FILE_TYPES.get(fname.rsplit(".", 1)[-1].lower()) or mimetypes.guess_type(fname)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if attach:
            plain = fname.encode("ascii", "replace").decode().replace('"', "")
            self.send_header("Content-Disposition", f"attachment; filename=\"{plain}\"; filename*=UTF-8''{urllib.parse.quote(fname)}")
        self.end_headers()
        if self.command == "HEAD":
            return
        if not attach:
            pos = start
            while pos <= end:
                b = pos // SRC_BLOCK
                for nb in range(b + 1, b + 3):
                    if nb * SRC_BLOCK <= size - 1 and wanted_block(key, nb):
                        threading.Thread(target=quietly, args=(src_block, key, nb, True, None), daemon=True).start()
                piece = src_block(key, b, True, None)[pos - b * SRC_BLOCK:end + 1 - b * SRC_BLOCK]
                if not piece:
                    break
                self.wfile.write(piece)
                pos += len(piece)
            return
        try:
            left = end - start + 1
            while left > 0:
                chunk = r.read(min(1 << 18, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        finally:
            conn.close()

    def src(self, key, query):



        if not hmac.compare_digest(self.headers.get("X-Tor-Key") or "", CHUNK_KEY):
            return self.send(403, {"error": "the fleet's copies only"})
        if key not in src_meta:
            return self.send(404, {"error": "unknown source"})
        urgent = (query.get("u") or ["1"])[0] == "1"
        n = int(query["n"][0]) if (query.get("n") or [""])[0].isdigit() else None
        try:
            size = src_meta[key].get("size") or src_lookup(key)["size"]
        except Exception as err:
            return self.send(502, {"error": str(err)})
        rng = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range") or "")
        start = int(rng.group(1)) if rng else 0
        end = min(int(rng.group(2)), size - 1) if rng and rng.group(2) else size - 1
        if start >= size:
            return self.send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        rate = size / src_meta[key]["duration"] if src_meta[key].get("duration") else 2e6
        lead = max(1, min(5, math.ceil(SRC_LEAD * rate / SRC_BLOCK)))
        pe = (query.get("pe") or [""])[0]
        f = films.get(key) if n is not None else None
        landing = f is not None and f.get("landing") == n and time.time() - f.get("jumped", 0) < JUMP_ALONE
        likely = end if n is None else min(end, int(pe) + (0 if landing else SRC_BLOCK) if pe.isdigit() else start + int((PIECE_S + 2) * rate * 1.25))


        if landing:
            land_now(key, n)
        pos = start
        while pos <= end:
            b = pos // SRC_BLOCK
            t0 = time.time()
            piece = land_part(key, pos, min(end + 1, (b + 1) * SRC_BLOCK), True)

            if piece is None:
                if n is not None:
                    urgent = piece_urgent(key, n, urgent)
                for nb in range(b + 1, b + 1 + ((lead if urgent else 1) if n is None or start else 0)):

                    if nb * SRC_BLOCK <= likely and wanted_block(key, nb, urgent):
                        threading.Thread(target=quietly, args=(src_block, key, nb, urgent, n), daemon=True).start()
                data = src_block(key, b, urgent, n)
                piece = data[pos - b * SRC_BLOCK:end + 1 - b * SRC_BLOCK]
            if n is not None:
                src_waits[(key, n)] = src_waits.get((key, n), 0.0) + time.time() - t0
            if not piece:
                break
            self.wfile.write(piece)
            pos += len(piece)

    def play(self, ih, idx, query):




        path = urllib.parse.urlparse(self.path).path
        if uncached(path) != path:
            return self.send(302, b"", "text/plain", {"Location": self.here(), "Cache-Control": "no-store"})
        job, state = current_job(ih, idx), {}
        if not job or time.time() - job["t"] > LEASE:
            state = front_state(ih, idx)
        until = time.time() + WAIT
        while not job and time.time() < until and state.get("state") != "failed":
            time.sleep(0.5)
            job = current_job(ih, idx)
            if not job:
                state = front_state(ih, idx)
        if job:
            return self.stitch(ih, idx, job)
        if state.get("state") == "failed":
            if state.get("for_metadata"):
                return self.send(504, {"error": state.get("why")}, extra={"Cache-Control": "no-store"})
            return self.proxy(ih, idx)
        waits = int((query.get("w") or ["0"])[0]) + 1
        if waits < WAITS:
            return self.send(302, b"", "text/plain", {"Location": self.here(w=str(waits)), "Cache-Control": "no-store"})
        return self.send(504, {"error": f"no metadata from the swarm in {WAIT * WAITS // 60} minutes: no peers to be found"}, extra={"Cache-Control": "no-store"})

    def proxy(self, ih, idx):

        u = urllib.parse.urlparse(TS_URL)
        c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=600)
        try:
            headers = {k: v for k, v in self.headers.items() if k.lower() in ("range", "user-agent", "accept", "if-range")}
            c.request(self.command, f"/stream?link={urllib.parse.quote(magnet(ih), safe='')}&index={idx + 1}&play", headers=headers)
            r = c.getresponse()
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag"):
                    self.send_header(k, v)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = r.read1(1 << 20)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            c.close()

    def stitch(self, ih, idx, job):



        total = job["size"]
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        start, end = 0, total - 1
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), total - 1) if m.group(2) else total - 1
            else:
                start = max(0, total - int(m.group(2)))
        if start >= total or start > end:
            return self.send(416, b"", "text/plain", {"Content-Range": f"bytes */{total}"})
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", mimetypes.guess_type(job["name"])[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        if self.command == "HEAD":
            return
        pl, cp = job["piece_length"], job["chunk"]
        bounds = []
        pos = start
        while pos <= end:
            c = ((job["offset"] + pos) // pl - job["first"]) // cp
            chunk_end = (job["first"] + (c + 1) * cp) * pl - job["offset"] - 1
            stop = min(end, pos + (FIRST_SEG if pos == start else SEG) - 1, chunk_end)
            bounds.append((pos, stop))
            pos = stop + 1
        served = serving.setdefault(ih, {"name": job["name"], "helpers": 0, "torrserver": 0})
        reported, reread, sent, t0, first_byte = 0, time.time(), 0, time.time(), None
        edge = job["chunk"] * job["piece_length"]
        if edge <= start < job["size"] - edge:
            reported = time.time()
            threading.Thread(target=report_playhead, args=(ih, idx, start), daemon=True).start()
        with concurrent.futures.ThreadPoolExecutor(AHEAD) as pool:
            pending = collections.deque()
            it = iter(bounds)
            streaming["n"] += 1
            try:
                for b in it:
                    pending.append(pool.submit(segment, ih, job["file"], job, b[0], b[1], served))
                    if len(pending) >= AHEAD:
                        break
                while pending:
                    data = pending.popleft().result()
                    nxt = next(it, None)
                    if nxt:
                        if time.time() - reread > 5:
                            job, reread = current_job(ih, idx) or job, time.time()
                        pending.append(pool.submit(segment, ih, job["file"], job, nxt[0], nxt[1], served))
                    self.wfile.write(data)
                    sent += len(data)
                    first_byte = first_byte or time.time() - t0


                    if sent >= 12 * 2**20 and time.time() - reported > 20:
                        reported = time.time()
                        at = nxt[0] if nxt else end
                        threading.Thread(target=report_playhead, args=(ih, idx, at), daemon=True).start()
            finally:
                streaming["n"] -= 1
                for p in pending:
                    p.cancel()
                log(f"{ih[:8]}: sent {sent / 1e6:.0f} MB from {start / job['size'] * 100:.1f}% in {time.time() - t0:.1f}s "
                    f"(first byte {first_byte or 0:.2f}s)")


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Frame torrents</title><style>
:root{color-scheme:light dark;--fg:#1a1a1a;--mute:#6b6b6b;--line:#e3e3e3;--bg:#fff;--accent:#2a5bd7}
@media(prefers-color-scheme:dark){:root{--fg:#eaeaea;--mute:#9a9a9a;--line:#2b2b2b;--bg:#121212;--accent:#7aa2ff}}
body{margin:0;padding:16px;font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}h1{font-size:18px;margin:0 0 8px}
code{font:13px ui-monospace,Menlo,monospace;word-break:break-all}a{color:var(--accent)}pre{font:12px/1.4 ui-monospace,Menlo,monospace;white-space:pre-wrap;border-top:1px solid var(--line);padding-top:8px}
.s{color:var(--mute)}</style></head><body>
<h1>Frame torrents</h1>
<p>Add to Stremio: <code>__BASE__/manifest.json</code> · <a id="inst" href="#">install</a></p>
<p class="s">Whatever plays is fetched by many servers at once, each a different part, and kept in their RAM.</p>
<p>Watch in the browser: <a href="__BASE__/watch">__BASE__/watch</a> (the gdrive addon's big streams, converted as they play)</p>
<pre id="st">…</pre>
<script>
async function tick(){try{const s=await (await fetch("status")).json();document.getElementById("st").textContent=JSON.stringify(s,null,2)}catch(e){}}
document.getElementById("inst").href="__BASE__/manifest.json".replace(/^https?:/,"stremio:");tick();setInterval(tick,3000);
</script></body></html>"""


WATCH_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0d10">
<meta name="color-scheme" content="dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Frame">
<meta name="description" content="Explore movies and series, compare available versions, and pick up where you left off.">
<title>Frame · Movies &amp; Series</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/pwa/apple-touch-icon.png?v=4">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%238054f5'/%3E%3Cpath transform='translate(10.8 8.5) scale(1.15)' d='M0 1.2v10.6a1 1 0 0 0 1.5.86l8.9-5.3a1 1 0 0 0 0-1.72L1.5.34A1 1 0 0 0 0 1.2z' fill='%23ffffff'/%3E%3C/svg%3E">
<style>
  :root {

    --bg: #0b0d10; --s1: #14181e; --s2: #1b2129; --s3: #252d38;
    --line: rgba(255,255,255,.08);
    --t1: #f3f5f7; --t2: #b4bcc8; --t3: #8994a4;
    --acc: #8054f5; --acc-hi: #946dff; --acc-tx: #cbbaff; --focus: #cbbaff; --acc-soft: rgba(128,84,245,.18);
    --danger: #ff7a7a;

    --glass: rgba(20,24,30,.92); --glass-bar: rgba(11,13,16,.94); --blur: blur(12px); --blur-bar: blur(16px);
    --edge: inset 0 0 0 0 transparent;

    --g: max(16px, env(safe-area-inset-left)); --gr: max(16px, env(safe-area-inset-right)); --gap: 8px; --nav-h: 56px;
    --top: env(safe-area-inset-top); --bot: env(safe-area-inset-bottom); --sal: env(safe-area-inset-left);
    --tab-h: 62px; --tab-b: max(12px, calc(var(--bot) - 10px)); --chrome-b: calc(var(--tab-h) + var(--tab-b) + 20px);

    --r-xs: 6px; --r-sm: 10px; --r-md: 14px; --r-lg: 20px; --r-xl: 38px;

    --e-out: cubic-bezier(.22, 1, .36, 1); --e-io: cubic-bezier(.65, 0, .35, 1); --e-sheet: cubic-bezier(.32, .72, 0, 1);
    --e-in: cubic-bezier(.4, 0, 1, 1); --e-spring: cubic-bezier(.34, 1.36, .64, 1);
    --sp-snappy: linear(0, 0.021 2.5%, 0.088 5.5%, 0.25 10.5%, 0.534 18.8%, 0.688 24.3%, 0.805 29.8%, 0.892 35.8%, 0.956 43%, 0.994 52.8%, 1.006 71%, 1);
    --sp-snappy-ms: 280ms;
    --sp-nav: linear(0, 0.019 2.3%, 0.085 5.3%, 0.286 11.5%, 0.509 18.5%, 0.654 24.3%, 0.767 30.3%, 0.857 37.3%, 0.923 45.8%, 0.969 57.8%, 0.994 79%, 1);
    --sp-nav-ms: 280ms;
    --sp-sheet: linear(0, 0.019 2.5%, 0.087 5.8%, 0.262 11.5%, 0.522 19.8%, 0.675 25.8%, 0.79 31.8%, 0.879 38.5%, 0.943 46.8%, 0.984 58%, 1.001 79%, 1);
    --sp-sheet-ms: 320ms;
    --sp-bouncy: linear(0, 0.02 2%, 0.079 4.3%, 0.211 7.5%, 0.629 16.3%, 0.786 20.3%, 0.906 24.3%, 0.989 28.2%, 1.043 32.8%, 1.067 38.5%, 1.053 47.8%, 1.004 66%, 0.997 90%, 1);
    --sp-bouncy-ms: 320ms;
    --sp-smooth: var(--sp-nav); --press-in: cubic-bezier(.2, .9, .3, 1);
    --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, "Segoe UI", Roboto, sans-serif;
  }

  @supports not (transition-timing-function: linear(0, 1)) {
    :root { --sp-snappy: cubic-bezier(.4, .9, .1, 1); --sp-nav: cubic-bezier(.3, .6, .1, 1); --sp-sheet: cubic-bezier(.35, .65, .1, 1); --sp-bouncy: cubic-bezier(.45, 1.5, .1, 1); }
  }
  @supports not ((-webkit-backdrop-filter: blur(1px)) or (backdrop-filter: blur(1px))) { :root { --glass: rgba(20,24,30,.94); --glass-bar: rgba(11,13,16,.94); } }
  @media (prefers-reduced-transparency: reduce) { :root { --glass: rgba(20,24,30,.97); --glass-bar: rgba(11,13,16,.97); --blur: none; --blur-bar: none; } }
  @media (min-width: 414px) and (max-width: 759px) { :root { --g: max(20px, env(safe-area-inset-left)); --gr: max(20px, env(safe-area-inset-right)); } }
  @media (min-width: 760px) { :root { --g: max(32px, env(safe-area-inset-left)); --gr: max(32px, env(safe-area-inset-right)); --gap: 12px; --nav-h: 64px; } }
  @media (min-width: 1100px) { :root { --g: max(56px, env(safe-area-inset-left)); --gr: max(56px, env(safe-area-inset-right)); } }
  @media (pointer: coarse) and (max-height: 520px) { :root { --nav-h: 48px; --tab-h: 52px; } }

  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html { background: var(--bg); color-scheme: dark; -webkit-text-size-adjust: 100%; scrollbar-gutter: stable; }
  html.lock { overflow: hidden; }
  html.pl-on { scrollbar-gutter: auto; }
  html:has(> body > #v-player:not(.hidden)) { scrollbar-gutter: auto; }
  body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--t1); overflow-x: hidden;
    font: 400 15px/1.45 var(--font); -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
  }
  main { overflow-x: clip; }

  html, .sscroll { scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.22) transparent; }
  @supports not (scrollbar-color: auto) { @media (hover: hover) and (pointer: fine) {
    ::-webkit-scrollbar { width: 10px; height: 10px; background: transparent; }
    ::-webkit-scrollbar-thumb { border: 3px solid transparent; border-radius: 5px; background: rgba(255,255,255,.22) padding-box; }
  } }
  ::selection { background: rgba(128,84,245,.42); color: #fff; }
  a { color: inherit; text-decoration: none; }
  button, input, select { font: inherit; color: inherit; }
  button { background: none; border: 0; padding: 0; cursor: pointer; }
  img, svg { display: block; }
  .hidden { display: none !important; }
  :focus { outline: none; }
  :focus-visible { outline: 2px solid var(--acc-hi); outline-offset: 2px; }

  .btn, .ibtn, .vchip, .pill, .card, .cw, .res, .ep, .ver, .lrow, .brand, .arrow { -webkit-touch-callout: none; -webkit-user-select: none; user-select: none; }


  .img { opacity: 0; transition: opacity 240ms var(--e-out); }
  .img.in { opacity: 1; }
  .breathe { animation: breathe 1.4s var(--e-io) infinite alternate; }
  @keyframes breathe { from { opacity: .45; } }
  html.pl-on .breathe { animation: none; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes fadeIn { from { opacity: 0; } }
  @keyframes fadeOut { to { opacity: 0; } }
  @keyframes viewIn { from { opacity: 0; transform: translate3d(0, 12px, 0); } }

  .view { position: relative; z-index: 2; min-height: 100vh; min-height: 100lvh; background: var(--bg); }
  .view.enter { animation: viewIn 220ms var(--e-out) both; }

  .view.parked { content-visibility: hidden; contain-intrinsic-size: 0 0; min-height: 0; }
  @supports not (content-visibility: hidden) { .view.parked { display: none; } }
  .mv-in-push { animation: mvPushIn var(--sp-nav-ms) var(--sp-nav) both; }
  .mv-out-push { animation: mvPushOut var(--sp-nav-ms) var(--sp-nav) both; }
  .mv-in-pop { animation: mvPopIn var(--sp-nav-ms) var(--sp-nav) both; }
  .mv-out-pop { animation: mvPopOut var(--sp-nav-ms) var(--sp-nav) both; }
  .mv-out-fade { animation: fadeOut 160ms var(--e-in) both; }
  .mv-edge::before { content: ""; position: absolute; top: 0; bottom: 0; left: -24px; width: 24px; pointer-events: none; background: linear-gradient(to left, rgba(0,0,0,.4), rgba(0,0,0,0)); }
  @keyframes mvPushIn { from { transform: translate3d(100%, 0, 0); } }
  @keyframes mvPushOut { to { transform: translate3d(-30%, 0, 0); opacity: .75; } }
  @keyframes mvPopIn { from { transform: translate3d(-30%, 0, 0); opacity: .75; } }
  @keyframes mvPopOut { to { transform: translate3d(100%, 0, 0); } }
  .eshade { position: fixed; inset: 0; z-index: 1; background: #000; pointer-events: none; }

  .sent { position: absolute; left: 0; top: 8px; width: 1px; height: 1px; pointer-events: none; visibility: hidden; }
  .tl .sent { display: none; }
  .pin, .vmark { display: block; height: 1px; margin-bottom: -1px; visibility: hidden; pointer-events: none; }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: 1ms !important; animation-iteration-count: 1 !important; transition-duration: 1ms !important; animation-delay: 0ms !important; }
  }


  #nav {
    position: fixed; z-index: 50; top: 0; left: 0; right: 0; height: calc(var(--nav-h) + var(--top));
    padding: var(--top) var(--gr) 0 var(--g); display: flex; align-items: center; gap: 10px;
  }
  #nav::before, #nav::after { content: ""; position: absolute; left: 0; right: 0; top: 0; z-index: -1; pointer-events: none; transition: opacity 200ms var(--e-out); }
  #nav::before { height: 160%; background: linear-gradient(rgba(11,13,16,.62), rgba(11,13,16,0)); }
  #nav::after {
    height: calc(100% + 16px); background: var(--glass-bar); opacity: 0; -webkit-backdrop-filter: var(--blur-bar); backdrop-filter: var(--blur-bar);
    -webkit-mask-image: linear-gradient(#000 calc(100% - 16px), transparent); mask-image: linear-gradient(#000 calc(100% - 16px), transparent);
  }
  #nav.solid::before { opacity: 0; }
  #nav.solid::after { opacity: 1; }
  #nav.tall::after { height: 100%; -webkit-mask-image: none; mask-image: none; }
  #nav.now::before, #nav.now::after, #nav.now #navTitle { transition: none; }
  #nav.navx > :not(#navTitle) { animation: fadeIn 220ms var(--e-out) both; }
  .brand { display: flex; align-items: center; gap: 10px; flex: none; height: 44px; font-size: 19px; font-weight: 700; letter-spacing: -.02em; }
  .mark { width: 28px; height: 28px; border-radius: 8px; background: var(--acc); display: grid; place-items: center; }
  .mark svg { width: 11px; height: 13px; margin-left: 2px; fill: #fff; }
  #navTitle { display: none; }
  .ibtn { position: relative; flex: none; width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; color: var(--t1); isolation: isolate; }
  .ibtn::before { content: ""; position: absolute; inset: 0; border-radius: inherit; background: rgba(255,255,255,.1); opacity: 0; transition: opacity 200ms var(--e-out); z-index: -1; }
  .ibtn:active::before { opacity: 1; transition-duration: 60ms; }
  @media (hover: hover) { .ibtn:hover::before { opacity: 1; } }
  .ibtn.round::before { opacity: 1; }
  .ibtn.round:active::before { background: rgba(255,255,255,.18); }
  .ibtn svg { width: 22px; height: 22px; }
  .ibtn.on { color: var(--acc-tx); }

  .ibtn.glass { background: rgba(20,24,30,.42); -webkit-backdrop-filter: var(--blur); backdrop-filter: var(--blur); box-shadow: var(--edge); transition: transform var(--sp-snappy-ms) var(--sp-snappy); }
  .ibtn.glass::before { background: rgba(255,255,255,.14); }
  .ibtn.glass:active { transform: scale(1.08); transition: transform 120ms var(--press-in); }
  .ibtn.glass svg { width: 20px; height: 20px; }
  #tBack { margin: 0 2px 0 -6px; }
  @media (max-width: 879px) { #nav.ontitle .brand .word { display: none; } }


  #tabbar {
    position: fixed; z-index: 55; left: 0; right: 0; top: calc(var(--top) + (var(--nav-h) - 44px) / 2); height: 44px;
    display: flex; justify-content: center; pointer-events: none; -webkit-touch-callout: none; -webkit-user-select: none; user-select: none;
  }
  #tabbar > * { pointer-events: auto; }
  .tb-cap { position: relative; display: flex; padding: 3px; border-radius: 22px; background: var(--glass); -webkit-backdrop-filter: var(--blur); backdrop-filter: var(--blur); box-shadow: var(--edge); }
  .tb { position: relative; z-index: 1; width: 96px; display: flex; align-items: center; justify-content: center; border-radius: 19px; color: var(--t2); font: 600 15px/20px var(--font); letter-spacing: -.016em; -webkit-touch-callout: none; }
  .tb::before { content: ""; position: absolute; inset: -3px 0; }
  .tb::after { content: ""; position: absolute; inset: 0; border-radius: inherit; background: rgba(255,255,255,.08); opacity: 0; transition: opacity 200ms var(--e-out); }
  .tb:active::after { opacity: 1; transition-duration: 60ms; }
  .tb svg { display: none; }
  .tb.on { color: var(--t1); }
  @media (hover: hover) { .tb:hover { color: var(--t1); } }
  .tb-pill { position: absolute; z-index: 0; top: 3px; bottom: 3px; left: 3px; width: 96px; border-radius: 19px; background: rgba(255,255,255,.12);
    transform: translate3d(calc(var(--i, 0) * 100%), 0, 0); transition: transform var(--sp-snappy-ms) var(--sp-snappy), opacity 200ms var(--e-out); }
  .tb-cap.none .tb-pill { opacity: 0; }
  .tb-mini { display: none; }
  .tb-search { position: absolute; top: 0; right: var(--gr); width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; color: var(--t1);
    background: var(--glass); -webkit-backdrop-filter: var(--blur); backdrop-filter: var(--blur); box-shadow: var(--edge); -webkit-touch-callout: none;
    transition: transform var(--sp-snappy-ms) var(--sp-snappy); }
  .tb-search svg { width: 20px; height: 20px; }
  .tb-search.on { color: var(--acc-tx); }
  .tb-search.on svg { stroke-width: 2.6; }
  .tb-search:active { transform: scale(1.08); transition: transform 120ms var(--press-in); }


  .ltitle { margin: 0; font: 700 34px/41px var(--font); letter-spacing: .011em; }
  .lt-over { display: none; }
  .lt-page { padding: 8px var(--gr) 6px var(--g); }


  .btn {
    position: relative; display: inline-flex; align-items: center; justify-content: center; gap: 8px; height: 50px; padding: 0 24px;
    border-radius: 25px; background: var(--acc); color: #fff; font: 600 17px/22px var(--font); letter-spacing: -.025em;
    white-space: nowrap; isolation: isolate; box-shadow: inset 0 1px 0 rgba(255,255,255,.2); transition: transform var(--sp-snappy-ms) var(--sp-snappy);
  }
  .btn::before, .btn::after { content: ""; position: absolute; inset: 0; border-radius: inherit; opacity: 0; z-index: -1; transition: opacity 200ms var(--e-out); }
  .btn::before { background: #fff; }
  .btn::after { background: #000; }
  .btn:active { transform: scale(.97); transition: transform 90ms var(--press-in); }
  .btn:active::after { opacity: .16; transition-duration: 60ms; }
  @media (hover: hover) { .btn:hover:not(:active)::before { opacity: .08; } }
  .btn svg { width: 18px; height: 18px; flex: none; }
  .btn.sec { background: rgba(255,255,255,.14); -webkit-backdrop-filter: var(--blur-bar); backdrop-filter: var(--blur-bar); box-shadow: var(--edge); }
  .btn.flat { background: var(--s3); box-shadow: none; }


  .hero { position: relative; height: max(500px, min(76svh, 680px)); overflow: hidden; background: var(--s1); }
  .bg { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 50% 22%; }

  .bg.sm { filter: blur(20px); transform: scale(1.12); }
  .bg.sm.plain { filter: none; transform: none; }
  .scrim { position: absolute; inset: 0; background: linear-gradient(to top, var(--bg) 0%, rgba(11,13,16,.9) 20%, rgba(11,13,16,.42) 48%, rgba(11,13,16,0) 72%); }
  .hero .info { position: absolute; left: var(--g); right: var(--gr); bottom: 26px; transition: opacity 240ms var(--e-out); }
  .hero.out .info, .hero.out .bg { opacity: 0; transition: opacity 160ms var(--e-in); }
  .tslot { position: relative; height: 104px; margin-bottom: 14px; }
  .tslot .logo, .tslot .bigname, .tl .tlogo, .tl .tname { position: absolute; left: 0; bottom: 0; }
  .logo, .tlogo { width: auto; height: auto; object-fit: contain; object-position: left bottom; opacity: 0; transition: opacity 300ms var(--e-out); }
  .logo { max-width: min(74%, 420px); max-height: 104px; }
  .bigname, .tname { right: 0; display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; opacity: 0; transition: opacity 240ms var(--e-out); }
  .bigname { margin: 0; -webkit-line-clamp: 2; font: 700 clamp(28px, 8vw, 34px)/1.12 var(--font); letter-spacing: .011em; }
  .tslot.nm .bigname, .tl.nm .tname, .tslot.lg .logo, .tl.lg .tlogo { opacity: 1; }
  .tslot.lg .bigname, .tl.lg .tname { opacity: 0; }

  .meta { display: flex; flex-wrap: wrap; align-items: center; row-gap: 2px; margin-left: -20px; clip-path: inset(0 0 0 20px); font: 500 13px/18px var(--font); letter-spacing: -.006em; color: var(--t2); }
  .meta > span { position: relative; margin-left: 20px; }
  .meta > span::before { content: "·"; position: absolute; margin-left: -13px; color: var(--t3); font-weight: 400; }
  .meta .r { color: var(--t1); font-weight: 600; }
  .desc { margin: 10px 0 0; max-width: 560px; font: 400 15px/20px var(--font); letter-spacing: -.016em; color: var(--t2); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .acts { display: flex; gap: 10px; margin-top: 18px; }
  .acts:empty { display: none; }
  @media (max-width: 759px) { .hero .acts .btn, .tbody .acts .btn { flex: 1; } }
  @media (min-width: 760px) { .bigname { -webkit-line-clamp: 3; font: 800 clamp(34px, 5.4vw, 60px)/1.02 var(--font); letter-spacing: -.03em; } }
  @media (min-width: 1100px) {
    .hero { height: min(80vh, 760px); }
    .hero .scrim { background: linear-gradient(to top, var(--bg) 0%, rgba(11,13,16,0) 40%), linear-gradient(to right, rgba(11,13,16,.92) 0%, rgba(11,13,16,.55) 32%, rgba(11,13,16,0) 62%); }
    .hero .info { right: auto; width: 560px; bottom: 136px; }
    .tslot { height: 150px; margin-bottom: 18px; }
    .logo { max-width: 440px; max-height: 150px; }
    .meta { font-size: 14px; }
    .desc { font-size: 16px; line-height: 22px; }
    .acts { margin-top: 22px; }
    .hero + .rows { margin-top: -96px; }
  }

  .rows { position: relative; padding-bottom: 56px; }
  .row { margin-top: 28px; content-visibility: auto; contain-intrinsic-size: auto calc(44px + (100vw - var(--g) - var(--gap) * 3) / 3.4 * 1.5); }
  .row:first-child { margin-top: 6px; }
  .row h2 { margin: 0 var(--gr) 2px var(--g); font: 700 20px/25px var(--font); letter-spacing: .019em; }
  .strip { position: relative; }
  .sc {
    display: flex; gap: var(--gap); overflow-x: auto; overflow-y: hidden; overscroll-behavior-x: contain; scrollbar-width: none;
    scroll-snap-type: x mandatory; scroll-padding-inline: var(--g) var(--gr); padding: 8px var(--gr) 8px var(--g);
  }
  .sc::-webkit-scrollbar { display: none; }

  .sc .head, .sc .tail { flex: none; width: 1px; height: 1px; align-self: center; pointer-events: none; }
  .sc .head { margin-right: calc(-1px - var(--gap)); }
  .sc .tail { margin-left: calc(-1px - var(--gap)); }
  .card {
    position: relative; flex: none; display: block; width: calc((100vw - var(--g) - var(--gap) * 3) / 3.4); aspect-ratio: 2 / 3;
    border-radius: var(--r-sm); overflow: hidden; background: var(--s1); scroll-snap-align: start; transition: transform var(--sp-snappy-ms) var(--sp-snappy);
  }
  .card img { width: 100%; height: 100%; object-fit: cover; }
  .card::after { content: ""; position: absolute; inset: 0; border-radius: inherit; outline: 1px solid rgba(255,255,255,.06); outline-offset: -1px; pointer-events: none; }

  .card:active { transform: scale(.96); transition: transform 90ms var(--press-in) 50ms; }
  .card .nm { position: absolute; left: 8px; right: 8px; bottom: 8px; font: 600 12px/16px var(--font); }
  @media (hover: hover) { .card:hover:not(:active) { transform: scale(1.04); transition: transform 240ms var(--e-out); } }
  .arrow {
    position: absolute; top: 50%; z-index: 3; width: 44px; height: 44px; margin-top: -22px; border-radius: 50%; background: var(--s2); color: var(--t1);
    display: none; place-items: center; opacity: 0; transition: opacity 200ms var(--e-out), transform var(--sp-snappy-ms) var(--sp-snappy);
  }
  .arrow svg { width: 20px; height: 20px; }
  .arrow.l { left: 14px; } .arrow.r { right: 14px; }
  .arrow:active { transform: scale(.92); transition: transform 90ms var(--press-in); }
  @media (hover: hover) and (min-width: 760px) { .arrow { display: grid; } .strip:hover .arrow { opacity: 1; } .strip.nol .arrow.l, .strip.nor .arrow.r { visibility: hidden; } }


  .cw { position: relative; flex: none; display: block; width: calc((100vw - var(--g) - var(--gap)) / 1.32); scroll-snap-align: start; transition: transform var(--sp-snappy-ms) var(--sp-snappy), opacity 200ms var(--e-out); }
  .cw:active { transform: scale(.97); transition: transform 90ms var(--press-in) 50ms; }
  .cw.gone { opacity: 0; transform: scale(.92); transition: opacity 180ms var(--e-in), transform 220ms var(--e-in); }
  .cw .im { position: relative; aspect-ratio: 16 / 9; border-radius: var(--r-md); overflow: hidden; background: var(--s1); }
  .cw .im img { width: 100%; height: 100%; object-fit: cover; }
  .pb { display: block; height: 3px; border-radius: 2px; background: var(--s3); overflow: hidden; }
  .pb i { display: block; height: 100%; border-radius: 2px; background: var(--acc-hi); }
  .cw .pb { margin-top: 8px; }
  .cw .x { position: absolute; top: 8px; right: 8px; width: 28px; height: 28px; border-radius: 50%; background: rgba(11,13,16,.64); color: var(--t1); display: grid; place-items: center; transition: opacity 200ms var(--e-out); }
  .cw .x::after { content: ""; position: absolute; inset: -8px; }
  .cw .x svg { width: 14px; height: 14px; }
  .cw .hov { position: absolute; left: 50%; top: 50%; width: 48px; height: 48px; margin: -24px 0 0 -24px; border-radius: 50%; background: var(--acc); color: #fff; display: grid; place-items: center; opacity: 0; transform: scale(.9); transition: opacity 200ms var(--e-out), transform 240ms var(--e-out); }
  .cw .hov svg { width: 18px; height: 18px; margin-left: 2px; }
  .cw .tx { padding: 7px 2px 0; }
  .cw .nm { font: 600 15px/20px var(--font); letter-spacing: -.016em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cw .sub { margin-top: 1px; font: 400 13px/18px var(--font); letter-spacing: -.006em; color: var(--t3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  @media (hover: hover) { .cw .x { opacity: 0; } .cw:hover .x, .cw:hover .hov, .cw:focus-within .x, .cw:focus-within .hov { opacity: 1; } .cw:hover .hov, .cw:focus-within .hov { transform: none; } }
  @media (hover: none) { .cw .x { display: none; } }

  .skrow h2 i { display: inline-block; width: 150px; height: 16px; border-radius: 4px; background: var(--s1); vertical-align: -1px; }
  .skrow .sc { pointer-events: none; }

  @media (min-width: 760px) {
    .card { width: calc((100vw - var(--g) - var(--gap) * 5) / 5.4); border-radius: 12px; }
    .cw { width: calc((100vw - var(--g) - var(--gap) * 3) / 3.3); }
    .row h2 { font-size: 22px; line-height: 28px; letter-spacing: .016em; }
    .row { contain-intrinsic-size: auto calc(46px + (100vw - var(--g) - var(--gap) * 5) / 5.4 * 1.5); }
  }
  @media (min-width: 1100px) {
    .card { width: calc((100vw - var(--g) - var(--gap) * 7) / 7.4); }
    .cw { width: calc((100vw - var(--g) - var(--gap) * 4) / 4.4); }
    .row { margin-top: 34px; contain-intrinsic-size: auto calc(46px + (100vw - var(--g) - var(--gap) * 7) / 7.4 * 1.5); }
    .row:first-child { margin-top: 0; }
  }


  .backdrop { position: relative; height: max(400px, min(60svh, 560px)); overflow: hidden; background: var(--s1); }
  .tbody { position: relative; margin-top: -126px; padding: 0 var(--gr) 64px var(--g); }
  .tl { position: relative; height: 122px; }
  .tlogo { max-width: min(80%, 460px); max-height: 108px; margin-bottom: 14px; }
  .tname { margin: 0 0 10px; -webkit-line-clamp: 3; font: 700 28px/34px var(--font); letter-spacing: .013em; }
  .tdesc { margin: 16px 0 0; max-width: 640px; font: 400 15px/20px var(--font); letter-spacing: -.016em; color: var(--t2); }
  .tdesc:empty { display: none; }
  .credits { margin-top: 12px; font: 400 13px/18px var(--font); letter-spacing: -.006em; color: var(--t2); }
  .credits div + div { margin-top: 3px; }
  .credits span { color: var(--t3); margin-right: 6px; }
  .sect { display: flex; align-items: baseline; gap: 8px; margin: 32px 0 12px; font: 700 22px/28px var(--font); letter-spacing: .016em; scroll-margin-top: calc(var(--nav-h) + var(--top) - 4px); }
  .sect .n { font: 600 12px/16px var(--font); letter-spacing: 0; color: var(--t3); font-variant-numeric: tabular-nums; }

  #tVersions, #tSeries { min-height: calc(100vh - var(--nav-h) - var(--top)); min-height: calc(100lvh - var(--nav-h) - var(--top)); }
  @media (min-width: 1100px) {
    .backdrop { height: min(82vh, 780px); }
    .backdrop .scrim { background: linear-gradient(to top, var(--bg) 0%, rgba(11,13,16,0) 42%), linear-gradient(to right, rgba(11,13,16,.94) 0%, rgba(11,13,16,.6) 34%, rgba(11,13,16,0) 64%); }
    .tbody { margin-top: -430px; }
    .thead { max-width: 600px; min-height: 360px; }
    .tl { height: 168px; }
    .tlogo { max-width: 460px; max-height: 150px; margin-bottom: 18px; }
    .tname { font: 800 52px/1.05 var(--font); letter-spacing: -.025em; }
    .meta { font-size: 14px; }
    .tdesc { font-size: 16px; line-height: 22px; }
    .sect { margin-top: 44px; }
  }


  .vidxw { position: sticky; top: calc(var(--nav-h) + var(--top)); z-index: 5; margin: 0 calc(var(--gr) * -1) 0 calc(var(--g) * -1); background: var(--bg); }
  .vidxw.stuck { background: var(--glass-bar); -webkit-backdrop-filter: var(--blur-bar); backdrop-filter: var(--blur-bar); }
  .vidxw.stuck::after, .sbar.stuck::after { content: ""; position: absolute; left: 0; right: 0; top: 100%; height: 16px; background: linear-gradient(var(--glass-bar), rgba(11,13,16,0)); pointer-events: none; }
  .vidx { display: flex; gap: 8px; overflow-x: auto; scrollbar-width: none; padding: 8px var(--gr) 10px var(--g); }
  .vidx::-webkit-scrollbar { display: none; }
  .vchip {
    position: relative; flex: none; display: inline-flex; align-items: center; gap: 7px; height: 36px; padding: 0 14px; border-radius: 18px;
    background: var(--s1); color: var(--t2); font: 600 15px/20px var(--font); letter-spacing: -.016em; isolation: isolate; transition: transform var(--sp-snappy-ms) var(--sp-snappy);
  }
  .vchip::before { content: ""; position: absolute; inset: 0; border-radius: inherit; background: var(--acc-soft); opacity: 0; z-index: -1; transition: opacity 220ms var(--e-out); }
  .vchip::after, .pill::after { content: ""; position: absolute; inset: -4px -2px; }
  .vchip .n { font: 600 12px/16px var(--font); letter-spacing: 0; color: var(--t3); font-variant-numeric: tabular-nums; }
  .vchip.on { color: var(--acc-tx); }
  .vchip.on::before { opacity: 1; }
  .vchip.on .n { color: var(--acc-tx); opacity: .8; }
  .vchip:active { transform: scale(.95); transition: transform 90ms var(--press-in); }
  .vsec { margin-top: 24px; scroll-margin-top: calc(var(--nav-h) + var(--top) + 60px); }
  .vsec-h { display: flex; align-items: center; gap: 8px; }
  .vsec-h b { display: inline-flex; align-items: center; gap: 8px; font: 600 17px/22px var(--font); letter-spacing: -.025em; }
  .pict { width: 18px; height: 18px; flex: none; color: var(--acc-tx); }
  .vchip .pict { width: 16px; height: 16px; color: inherit; }
  .vk .pict { display: inline-block; width: 16px; height: 16px; margin: 0 6px -2px 0; color: var(--acc-tx); }
  .vk .vok { display: inline-block; width: 15px; height: 15px; margin: 0 0 -2px 5px; color: var(--acc-tx); }
  .vk .vok svg { display: block; width: 100%; height: 100%; }
  .vs .vc { color: var(--t2); }
  .vsec-h .n { padding: 1px 8px; border-radius: 999px; background: var(--s1); font: 600 12px/16px var(--font); color: var(--t3); font-variant-numeric: tabular-nums; }
  .vsec-d { margin: 4px 0 12px; max-width: 720px; font: 400 13px/18px var(--font); letter-spacing: -.006em; color: var(--t3); }
  .vgroup { background: var(--s1); border-radius: var(--r-lg); overflow: hidden; }
  .ver { position: relative; display: flex; align-items: center; gap: 14px; padding: 13px 14px 13px 16px; cursor: pointer; isolation: isolate; }
  .ver + .ver::before, .vmore::before { content: ""; position: absolute; top: 0; left: 16px; right: 0; height: 1px; background: var(--line); }
  .ver::after, .vmore::after { content: ""; position: absolute; inset: 0; background: #1b1827; opacity: 0; z-index: -1; pointer-events: none; transition: opacity 160ms var(--e-out); }
  .ver:active:not(:has(.dl:active))::after, .vmore:active::after { opacity: 1; transition-duration: 50ms; }
  @media (hover: hover) { .ver:hover::after, .vmore:hover::after { opacity: .6; } }
  .ver:focus-visible { outline-offset: -2px; }
  .vt { flex: 1; min-width: 0; }
  .vk { font: 600 17px/22px var(--font); letter-spacing: -.025em; }
  .vs { margin-top: 2px; font: 400 15px/20px var(--font); letter-spacing: -.016em; color: var(--t2); }
  .vs b { color: var(--t1); font-weight: 600; font-variant-numeric: tabular-nums; }

  .vf { display: flex; margin-top: 3px; font: 400 12px/16px var(--font); color: var(--t3); white-space: nowrap; -webkit-user-select: text; user-select: text; }
  .vf .h { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .vf .t { flex: none; }
  .go { position: relative; flex: none; width: 36px; height: 36px; border-radius: 50%; background: var(--acc-soft); color: var(--acc-tx); display: grid; place-items: center; isolation: isolate; transition: transform var(--sp-snappy-ms) var(--sp-snappy); }
  .go::before { content: ""; position: absolute; inset: 0; border-radius: inherit; background: var(--acc); opacity: 0; z-index: -1; transition: opacity 200ms var(--e-out); }
  .ver:active:not(:has(.dl:active)) .go { color: #fff; transform: scale(.88); transition: transform 90ms var(--press-in); }
  .ver:active:not(:has(.dl:active)) .go::before { opacity: 1; transition-duration: 60ms; }
  @media (hover: hover) { .ver:hover:not(:has(.dl:hover)) .go { color: #fff; } .ver:hover:not(:has(.dl:hover)) .go::before { opacity: 1; } }
  .go svg { width: 14px; height: 14px; margin-left: 2px; }
  .ver .dl { position: relative; flex: none; width: 44px; height: 44px; margin: -6px -8px -6px -4px; border-radius: 50%; color: var(--t3); display: grid; place-items: center; isolation: isolate; transition: transform var(--sp-snappy-ms) var(--sp-snappy); }
  .ver .dl::before { content: ""; position: absolute; inset: 3px; border-radius: inherit; background: var(--s3); opacity: 0; z-index: -1; transition: opacity 200ms var(--e-out); }
  .ver .dl svg { width: 18px; height: 18px; }
  .ver button.dl { background: none; border: 0; padding: 0; font: inherit; cursor: pointer; -webkit-tap-highlight-color: transparent; }
  .ver .dl.cp { margin-right: -4px; }
  .ver .dl:active { color: var(--t1); transform: scale(.88); transition: transform 90ms var(--press-in); }
  .ver .dl:active::before { opacity: 1; transition-duration: 60ms; }
  @media (hover: hover) { .ver .dl:hover { color: var(--t1); } .ver .dl:hover::before { opacity: 1; } }
  .vmore { position: relative; display: flex; width: 100%; height: 48px; align-items: center; justify-content: center; gap: 6px; color: var(--acc-tx); font: 600 15px/20px var(--font); letter-spacing: -.016em; isolation: isolate; }
  .vmore svg { width: 16px; height: 16px; }
  .vload .ver { pointer-events: none; }
  .vload .bar { height: 12px; border-radius: 4px; background: var(--s3); }
  .vload .th { aspect-ratio: 16 / 9; border-radius: var(--r-sm); background: var(--s1); }
  .vload .go { background: var(--s3); }
  .vload .go::before { display: none; }
  .vmsg { padding: 6px 0 4px; }
  .vmsg b { display: block; font: 600 15px/20px var(--font); color: var(--t2); }
  .vmsg p { margin: 4px 0 0; max-width: 720px; font: 400 13px/18px var(--font); color: var(--t3); overflow-wrap: anywhere; }
  @media (min-width: 1100px) {
    .tbody .vgroup { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; background: none; border-radius: 0; overflow: visible; }
    .tbody .ver { background: var(--s1); border-radius: var(--r-md); }
    .tbody .ver::after { border-radius: inherit; }
    .tbody .ver + .ver::before, .tbody .vmore::before { display: none; }
    .tbody .vmore { grid-column: 1 / -1; justify-self: start; width: auto; padding: 0 20px; background: var(--s1); border-radius: var(--r-md); }
    .tbody .vmore::after { border-radius: inherit; }
  }


  .seasons { display: flex; gap: 8px; overflow-x: auto; scrollbar-width: none; margin: -4px calc(var(--gr) * -1) 0 calc(var(--g) * -1); padding: 6px var(--gr) 14px var(--g); }
  .seasons::-webkit-scrollbar { display: none; }
  .pill {
    position: relative; flex: none; height: 36px; padding: 0 16px; border-radius: 18px; background: var(--s1); color: var(--t2);
    font: 600 15px/20px var(--font); letter-spacing: -.016em; isolation: isolate; transition: transform var(--sp-snappy-ms) var(--sp-snappy);
  }
  .pill::before { content: ""; position: absolute; inset: 0; border-radius: inherit; background: var(--acc-soft); opacity: 0; z-index: -1; transition: opacity 220ms var(--e-out); }
  .pill.on { color: var(--acc-tx); }
  .pill.on::before { opacity: 1; }
  .pill:active { transform: scale(.95); transition: transform 90ms var(--press-in) 50ms; }
  .ep { position: relative; display: grid; grid-template-columns: minmax(0, 42%) minmax(0, 1fr); gap: 12px; align-items: start; padding: 8px 0; cursor: pointer; isolation: isolate; }
  .ep::after { content: ""; position: absolute; top: 0; bottom: 0; left: calc(var(--g) * -1); right: calc(var(--gr) * -1); background: rgba(255,255,255,.05); opacity: 0; z-index: -1; transition: opacity 220ms var(--e-out); }
  .ep:active::after { opacity: 1; transition-duration: 50ms; transition-delay: 50ms; }
  .ep .th { position: relative; aspect-ratio: 16 / 9; border-radius: var(--r-sm); overflow: hidden; background: var(--s1); transition: transform var(--sp-snappy-ms) var(--sp-snappy); }
  .ep:active .th { transform: scale(.97); transition: transform 90ms var(--press-in) 50ms; }
  .ep .th img { width: 100%; height: 100%; object-fit: cover; }
  .ep .pb { margin-top: 6px; }
  .ep .th .hov { position: absolute; left: 50%; top: 50%; width: 40px; height: 40px; margin: -20px 0 0 -20px; border-radius: 50%; background: var(--acc); color: #fff; display: grid; place-items: center; opacity: 0; transform: scale(.9); transition: opacity 200ms var(--e-out), transform 240ms var(--e-out); }
  .ep .th .hov svg { width: 15px; height: 15px; margin-left: 2px; }
  @media (hover: hover) { .ep:hover .th .hov { opacity: 1; transform: none; } .ep:hover::after { opacity: .6; } }
  .ep .tt { font: 600 15px/20px var(--font); letter-spacing: -.016em; }
  .ep .tt .no { margin-right: 6px; color: var(--t3); font-variant-numeric: tabular-nums; }
  .ep .sm { margin-top: 2px; font: 400 13px/18px var(--font); letter-spacing: -.006em; color: var(--t3); }
  .ep .sm .left { color: var(--acc-tx); white-space: nowrap; }
  .ep .sm .left:empty { display: none; }
  .ep .sm .bits:not(:empty) + .left::before { content: "·"; margin: 0 5px; color: var(--t3); }
  .ep p { margin: 6px 0 0; font: 400 13px/18px var(--font); letter-spacing: -.006em; color: var(--t2); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .ep.on .th { outline: 2px solid var(--acc-hi); outline-offset: 2px; }
  .ep.on .tt .no { color: var(--acc-tx); }
  @media (min-width: 760px) { .eps { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px 16px; } .eps .ep { grid-template-columns: 1fr; gap: 10px; padding: 0; } .eps .ep::after { display: none; } }
  @media (min-width: 1100px) { .eps { grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 30px 16px; } .eps .ep p { -webkit-line-clamp: 3; } }


  .sheet { position: fixed; inset: 0; z-index: 70; visibility: hidden; transition: visibility 0s linear 340ms; }
  .sheet.open { visibility: visible; transition-delay: 0s; }
  .sheet:not(.open) { pointer-events: none; }
  .sheet.now, .sheet.now .dim, .sheet.now .panel { transition: none !important; }
  .sheet .dim { position: absolute; inset: 0; background: rgba(4,3,8,.64); opacity: 0; transition: opacity 320ms var(--e-sheet); }
  .sheet.open .dim { opacity: 1; transition: opacity 300ms linear; }
  .panel {
    position: absolute; left: 0; right: 0; bottom: 0; height: calc(100svh - 44px - var(--top)); display: flex; flex-direction: column;
    background: var(--s2); border-radius: 22px 22px 0 0; transform: translate3d(0, 100%, 0); transition: transform 320ms var(--e-sheet);
  }
  .sheet.open .panel { transform: translate3d(0, 0, 0); transition: transform var(--sp-sheet-ms) var(--sp-sheet); }
  .shead { flex: none; padding: 8px 16px 4px; touch-action: none; cursor: grab; }
  .grab { width: 36px; height: 5px; border-radius: 3px; background: rgba(255,255,255,.22); margin: 0 auto 12px; }
  .shrow { display: flex; align-items: center; gap: 12px; }
  .shthumb { flex: none; width: 88px; aspect-ratio: 16 / 9; border-radius: var(--r-xs); overflow: hidden; background: var(--s3); }
  .shthumb img { width: 100%; height: 100%; object-fit: cover; }
  .shttl { flex: 1; min-width: 0; }
  .shttl .sm { display: block; font: 400 13px/18px var(--font); letter-spacing: -.006em; color: var(--t2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .shttl b { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font: 600 17px/22px var(--font); letter-spacing: -.025em; }
  @media (max-width: 359px) { .shthumb { display: none; } }
  .sscroll { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 0 16px calc(28px + var(--bot)); }
  .sscroll .vidxw { top: 0; margin: 0 -16px; background: var(--s2); }
  .sscroll .vidx { padding: 12px 16px 10px; }
  .sscroll .vgroup, .sscroll .vsec-h .n, .sscroll .vchip, .sscroll .vload .th { background: var(--s3); }
  .sscroll .vload .bar, .sscroll .vload .go { background: rgba(255,255,255,.1); }
  .sscroll .vsec { scroll-margin-top: 64px; }
  .sscroll .vmsg { padding-top: 16px; }
  @media (max-width: 759px), (pointer: coarse) and (max-height: 520px) {

    .panel { left: max(8px, env(safe-area-inset-left)); right: max(8px, env(safe-area-inset-right)); bottom: 8px; max-width: 720px; margin: 0 auto;
      height: calc(100svh - 52px - var(--top)); border-radius: var(--r-xl); overflow: hidden;
      transform: translate3d(0, calc(100% + 8px), 0); box-shadow: 0 -8px 40px rgba(0,0,0,.45); }
    .shead { padding: 6px 16px 10px; }
    .grab { margin-bottom: 10px; background: rgba(255,255,255,.28); }
    .shrow { padding-right: 52px; }
    #shClose { position: absolute; top: 16px; right: 16px; background: rgba(255,255,255,.1); }
    #shClose::before { display: none; }
  }
  @media (pointer: coarse) and (max-height: 520px) { .panel { height: calc(100svh - 20px); } .shthumb { width: 72px; } }
  @media (min-width: 760px) and (max-width: 1099px) and (min-height: 521px) {
    .panel { left: 50%; right: auto; width: min(720px, 92vw); bottom: 4vh; height: min(86vh, 900px); border-radius: 24px; transform: translate3d(-50%, 110%, 0); }
    .sheet.open .panel { transform: translate3d(-50%, 0, 0); }
    .shead { padding: 18px 16px 6px; cursor: default; }
    .grab { display: none; }
  }
  @media (min-width: 1100px) {
    .panel { left: auto; top: 12px; right: 12px; bottom: 12px; width: 540px; height: auto; border-radius: 24px; transform: translate3d(calc(100% + 24px), 0, 0); }
    .sheet.open .panel { transition-duration: var(--sp-sheet-ms); }
    .shead { padding: 18px 20px 6px; cursor: default; }
    .grab { display: none; }
    .sscroll { padding: 0 20px 28px; }
    .sscroll .vidxw { margin: 0 -20px; }
    .sscroll .vidx { padding: 12px 20px 10px; }
  }


  .page { padding-top: calc(var(--nav-h) + var(--top)); }
  .lsect { margin: 20px var(--gr) 8px var(--g); font: 700 20px/25px var(--font); letter-spacing: .019em; }
  .lgroup { margin: 0 var(--gr) 0 var(--g); max-width: 720px; background: var(--s1); border-radius: var(--r-lg); overflow: hidden; }
  .lrow { position: relative; display: flex; align-items: center; gap: 12px; min-height: 80px; padding: 10px 2px 10px 12px; cursor: pointer; isolation: isolate;
    transition: opacity 180ms var(--e-in), transform 220ms var(--e-in); }
  .lrow + .lrow::before { content: ""; position: absolute; top: 0; left: 136px; right: 0; height: 1px; background: var(--line); }
  .lrow::after { content: ""; position: absolute; inset: 0; background: rgba(255,255,255,.06); opacity: 0; z-index: -1; transition: opacity 220ms var(--e-out); }
  .lrow:active:not(:has(.x:active))::after { opacity: 1; transition-duration: 50ms; transition-delay: 50ms; }
  @media (hover: hover) { .lrow:hover::after { opacity: .6; } }
  .lrow.gone { opacity: 0; transform: scale(.96); }
  .lrow .th { flex: none; width: 112px; aspect-ratio: 16 / 9; border-radius: 8px; overflow: hidden; background: var(--s3); }
  .lrow .th img { width: 100%; height: 100%; object-fit: cover; }
  .lrow .tx { flex: 1; min-width: 0; }
  .lrow .nm { font: 600 17px/22px var(--font); letter-spacing: -.025em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .lrow .sub { margin-top: 1px; font: 400 15px/20px var(--font); letter-spacing: -.016em; color: var(--t3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .lrow .pb { margin-top: 7px; }
  .lrow .x { position: relative; flex: none; width: 44px; height: 44px; display: grid; place-items: center; color: var(--t3); transition: transform var(--sp-snappy-ms) var(--sp-snappy); }
  .lrow .x:active { transform: scale(.88); transition: transform 90ms var(--press-in); }
  .lrow .x svg { width: 22px; height: 22px; }
  #libList .row:first-child { margin-top: 20px; }

  .cuv { grid-column: 1 / -1; display: flex; flex-direction: column; align-items: center; text-align: center; gap: 6px; padding: 12vh 32px 24px; }
  .cuv svg { width: 48px; height: 48px; color: var(--t3); margin-bottom: 8px; }
  .cuv b { font: 700 20px/25px var(--font); letter-spacing: .019em; }
  .cuv p { margin: 0; max-width: 300px; font: 400 15px/20px var(--font); letter-spacing: -.016em; color: var(--t2); overflow-wrap: anywhere; }
  .cuv .btn { margin-top: 14px; height: 44px; padding: 0 20px; font-size: 15px; background: var(--s3); box-shadow: none; }
  .vmsg + .cuv, .sscroll .cuv { padding-top: 6vh; }
  .view.nohero .hero { display: none; }
  .view.nohero .rows { margin-top: 0; padding-top: calc(var(--nav-h) + var(--top)); }
  .tdesc .again { display: block; margin-top: 6px; min-height: 44px; color: var(--acc-tx); font-weight: 600; }
  .aiv { width: 18px; height: 18px; flex: none; color: var(--t2); animation: spin 900ms steps(8, end) infinite; }


  .sbar { position: sticky; top: calc(var(--nav-h) + var(--top)); z-index: 5; display: flex; align-items: center; padding: 8px var(--gr) 12px var(--g); background: var(--bg); }
  .sbar.stuck { background: var(--glass-bar); -webkit-backdrop-filter: var(--blur-bar); backdrop-filter: var(--blur-bar); }
  .field { position: relative; flex: 1; min-width: 0; max-width: 720px; }
  .field input {
    width: 100%; height: 44px; padding: 0 44px 0 40px; border: 0; border-radius: 22px; background: var(--s2); color: var(--t1);
    font: 400 17px/22px var(--font); letter-spacing: -.025em; -webkit-appearance: none; appearance: none;
  }
  .field input::placeholder { color: var(--t3); }
  .field input { caret-color: var(--acc-hi); }
  .field input:focus, .field input:focus-visible { outline: none; }
  .field input:focus-visible { box-shadow: inset 0 0 0 1px rgba(255,255,255,.16); }
  .field input::-webkit-search-cancel-button { -webkit-appearance: none; display: none; }
  .field .ic { position: absolute; left: 13px; top: 13.5px; width: 17px; height: 17px; color: var(--t3); pointer-events: none; }
  .field .clr { position: absolute; right: 0; top: 0; width: 44px; height: 44px; color: var(--t3); }
  .field .clr::before { display: none; }
  .field .clr svg { width: 17px; height: 17px; }
  .scancel { flex: none; height: 44px; padding-left: 14px; color: var(--acc-tx); font: 400 17px/22px var(--font); letter-spacing: -.025em;
    opacity: 0; transform: translate3d(12px, 0, 0); pointer-events: none; transition: opacity 160ms var(--e-out), transform var(--sp-snappy-ms) var(--sp-snappy); }
  .sbar:not(.act) .scancel { position: absolute; right: var(--gr); }
  .sbar.act .scancel { opacity: 1; transform: none; pointer-events: auto; }
  .note { display: flex; align-items: center; gap: 8px; min-height: 18px; margin: 0 var(--gr) 12px var(--g); font: 400 13px/18px var(--font); letter-spacing: -.006em; color: var(--t3); }
  .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px 10px; padding: 0 var(--gr) 56px var(--g); }
  .res { display: block; min-width: 0; }
  .grid .gtail { grid-column: 1 / -1; height: 1px; margin-top: -18px; pointer-events: none; }
  .res .card { width: auto; }
  .res .nm2 { margin-top: 8px; font: 600 13px/18px var(--font); letter-spacing: -.006em; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
  .res .yr { font: 400 12px/16px var(--font); color: var(--t3); }
  .res:active .card { transform: scale(.96); transition: transform 90ms var(--press-in) 50ms; }
  @media (min-width: 760px) { .grid { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 28px 16px; } .sbar { padding-top: 16px; } }


  @media (max-width: 759px), (pointer: coarse) and (max-height: 520px) {
    #nav .brand { display: none; }
    #nav::before { height: calc(var(--top) + 20px); background: linear-gradient(rgba(11,13,16,.55), rgba(11,13,16,0)); }
    #navTitle { display: block; position: absolute; left: 64px; right: 64px; top: var(--top); height: var(--nav-h); text-align: center; pointer-events: none;
      font: 600 17px/22px var(--font); letter-spacing: -.025em; padding-top: calc((var(--nav-h) - 22px) / 2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      opacity: 0; transform: translate3d(0, 6px, 0); transition: opacity 180ms var(--e-out), transform 260ms var(--e-out); }
    #nav.solid #navTitle { opacity: 1; transform: none; }
    .view > .sent { top: 40px; }
    #v-title > .sent { display: none; }
    .tl .sent { display: block; top: auto; bottom: calc(var(--nav-h) + var(--top)); }
    .hero .lt-over { position: absolute; z-index: 2; left: var(--g); right: var(--gr); top: calc(var(--top) + 8px); display: flex; align-items: center; gap: 10px;
      text-shadow: 0 1px 18px rgba(0,0,0,.4); pointer-events: none; }
    .hero .scrim { background: linear-gradient(rgba(11,13,16,.6), rgba(11,13,16,.26) calc(var(--top) + 64px), rgba(11,13,16,0) calc(var(--top) + 124px)),
      linear-gradient(to top, var(--bg) 0%, rgba(11,13,16,.9) 20%, rgba(11,13,16,.42) 48%, rgba(11,13,16,0) 72%); }
    .lt-over .mark { width: 34px; height: 34px; border-radius: 9px; }
    .lt-over .mark svg { width: 13px; height: 15px; margin-left: 2px; }
    .page { padding-top: 0; }
    .lt-page { padding-top: calc(var(--top) + 8px); }
    main { padding-bottom: var(--chrome-b); }
    .rows, .grid, .tbody { padding-bottom: 8px; }
    #tabbar { top: auto; bottom: var(--tab-b); left: max(14px, env(safe-area-inset-left)); right: max(14px, env(safe-area-inset-right)); height: var(--tab-h);
      justify-content: stretch; gap: 10px; transition: transform 320ms var(--sp-nav), opacity 200ms var(--e-out); }
    .tb-cap { flex: 1; padding: 4px; border-radius: 31px; box-shadow: var(--edge), 0 10px 30px rgba(0,0,0,.35); }
    .tb { flex: 1; width: auto; flex-direction: column; gap: 2px; border-radius: 27px; color: var(--t1); font: 600 10px/12px var(--font); letter-spacing: .01em; }
    .tb svg { display: block; width: 26px; height: 26px; }
    .tb .f { display: none; }
    .tb.on .o { display: none; }
    .tb.on .f { display: block; }
    .tb.on { color: var(--acc-tx); }
    .tb::after { transform: scale(.86); transition: opacity 200ms var(--e-out), transform var(--sp-snappy-ms) var(--sp-snappy); }
    .tb:active::after { transform: none; transition-duration: 60ms, 120ms; }
    .tb-pill { top: 4px; bottom: 4px; left: 4px; width: calc((100% - 8px) / 4); border-radius: 27px; background: rgba(255,255,255,.1); }
    .tb-search { position: relative; top: auto; right: auto; flex: none; width: var(--tab-h); height: var(--tab-h); box-shadow: var(--edge), 0 10px 30px rgba(0,0,0,.35); }
    .tb-search svg { width: 24px; height: 24px; }
    .tb-search.on::before { content: ""; position: absolute; inset: 4px; border-radius: 50%; background: rgba(255,255,255,.12); }

    html.kb #tabbar { opacity: 0; }
    html.kb #tabbar, html.kb #tabbar > * { pointer-events: none; }

    .tb-cap { transition: opacity 180ms var(--e-out); }
    .tb-mini { position: absolute; left: 0; bottom: 0; width: var(--tab-h); height: var(--tab-h); border-radius: 50%; display: grid; place-items: center; color: var(--acc-tx);
      background: var(--glass); -webkit-backdrop-filter: var(--blur); backdrop-filter: var(--blur); box-shadow: var(--edge), 0 10px 30px rgba(0,0,0,.35);
      opacity: 0; visibility: hidden; transition: opacity 180ms var(--e-out), visibility 0s linear 180ms; }
    .tb-mini svg { width: 26px; height: 26px; }
    .tb-mini svg.o { display: none; }
    #tabbar.min .tb-cap { opacity: 0; pointer-events: none; }
    #tabbar.min .tb-mini { opacity: 1; visibility: visible; transition-delay: 0s; }
  }

  @media (pointer: coarse) and (max-height: 520px) {
    #tabbar { left: max(14px, calc(50% - 300px)); right: max(14px, calc(50% - 300px)); }
    .tb { flex-direction: row; gap: 6px; font-size: 12px; line-height: 16px; }
    .tb svg, .tb-search svg { width: 22px; height: 22px; }
    .hero { height: calc(100svh - var(--tab-h) - var(--tab-b) - 8px); }
    .hero .info { right: auto; width: min(520px, 60vw); bottom: 16px; }
    .tslot { height: 64px; margin-bottom: 8px; }
    .logo { max-height: 64px; }
    .hero .desc { -webkit-line-clamp: 2; }
    .hero .acts { margin-top: 12px; }
    .hero .acts .btn { flex: none; }
    .backdrop { height: max(260px, 88svh); }
    .backdrop .scrim { background: linear-gradient(to top, var(--bg) 0%, rgba(11,13,16,0) 42%), linear-gradient(to right, rgba(11,13,16,.94) 0%, rgba(11,13,16,.6) 38%, rgba(11,13,16,0) 66%); }
    .tbody { margin-top: calc(12svh - var(--tab-h) - var(--tab-b) - 182px); }
    .thead { max-width: min(560px, 62vw); }
    .tl { height: 88px; }
    .tlogo { max-height: 76px; margin-bottom: 12px; }
  }


  #v-player {
    --glass: rgba(23,28,35,.42); --glass-solid: rgba(23,28,35,.78); --glass-opaque: rgba(23,28,35,.95); --glass-edge: transparent;
    --glass-fx: blur(24px) saturate(1.8); --trk: rgba(255,255,255,.26); --trk-buf: rgba(255,255,255,.44); --trk-fill: var(--acc-hi);
    --p-spring: var(--sp-snappy, cubic-bezier(.4, .9, .1, 1)); --p-bouncy: var(--sp-bouncy, cubic-bezier(.45, 1.5, .1, 1));
    position: fixed; inset: 0; z-index: 90; background: #000; display: flex; flex-direction: column; outline: none;
  }
  @media (prefers-reduced-transparency: reduce) { #v-player { --glass: var(--glass-opaque); --glass-solid: var(--glass-opaque); --glass-fx: none; } }
  .pframe {
    position: relative; flex: none; width: 100%; aspect-ratio: 16 / 9; margin-top: var(--top); overflow: hidden; background: #000;
    touch-action: none; -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
  }
  .pframe video, .pcover, .psnap { position: absolute; inset: 0; width: 100%; height: 100%; }
  .pframe video { display: block; object-fit: contain; background: #000; transform-origin: 50% 50%; transition: transform 300ms var(--e-out); }
  .pcover, .psnap { pointer-events: none; }
  .pcover { object-fit: cover; opacity: 0; transition: opacity 360ms var(--e-out), visibility 0s; }
  .pcover.ok { opacity: .42; }
  #v-player.live .pcover { opacity: 0; visibility: hidden; transition: opacity 360ms var(--e-out), visibility 0s linear 360ms; }
  .psnap { object-fit: contain; }
  .pdim { position: absolute; inset: 0; background: #000; opacity: 0; pointer-events: none; }
  #v-player:not(.m-full) .pdim { visibility: hidden; }
  .pgest { position: absolute; inset: 0; }
  .pwhere { position: absolute; left: 0; right: 0; top: 50%; display: none; justify-content: center; align-items: center; gap: 8px; margin-top: 52px; font-size: 13px; font-weight: 500; color: var(--t2); pointer-events: none; }
  .pwhere svg { width: 18px; height: 18px; }
  #v-player.pip .pwhere, #v-player.air .pwhere { display: flex; }
  #v-player.pip .pmid .gb.l, #v-player.air .pmid .gb.l { opacity: .4; }


  .pctl {
    position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: space-between; padding: 8px 10px 6px;
    pointer-events: none; isolation: isolate; transition: opacity 200ms var(--e-out), visibility 0s;
  }
  .pctl::before, .pctl::after { content: ""; position: absolute; left: 0; right: 0; z-index: -1; pointer-events: none; }
  .pctl::before { top: 0; height: min(132px, 46%); background: linear-gradient(rgba(0,0,0,.5), rgba(0,0,0,0)); }
  .pctl::after { bottom: 0; height: min(168px, 62%); background: linear-gradient(rgba(0,0,0,0), rgba(0,0,0,.62)); }
  .pctl button, .pctl .scrub, .pctl .vol { pointer-events: auto; }
  #v-player.hide .pctl { opacity: 0; visibility: hidden; transition: opacity 260ms var(--e-in), visibility 0s linear 260ms; }
  #v-player.hide.idle .pframe { cursor: none; }
  .ptop, .pmid, .prow, .pbot { transition: opacity 160ms var(--e-out); }
  #v-player.gest .ptop, #v-player.gest .pmid, #v-player.gest .prow, #v-player.gest .pend, #v-player.hudon .ptop { opacity: 0; pointer-events: none; }
  #v-player.gest:not(.gx) .pbot { opacity: 0; pointer-events: none; }


  .gb, .gp {
    position: relative; flex: none; color: var(--t1); isolation: isolate;
    background: var(--glass); -webkit-backdrop-filter: var(--glass-fx); backdrop-filter: var(--glass-fx);
    box-shadow: inset 0 0 0 .5px var(--glass-edge), 0 6px 20px rgba(0,0,0,.16);
    transition: transform 420ms var(--p-spring);
  }
  .gb { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; }
  .gp { display: inline-flex; align-items: center; gap: 7px; height: 40px; padding: 0 16px 0 12px; border-radius: 20px; font-size: 14px; font-weight: 600; white-space: nowrap; }
  .gb::before, .gp::before { content: ""; position: absolute; inset: -4px; border-radius: inherit; }
  .gb::after, .gp::after { content: ""; position: absolute; inset: 0; border-radius: inherit; background: #fff; opacity: 0; z-index: -1; transition: opacity 180ms var(--e-out); }
  .gb:active, .gp:active { transform: scale(.9); transition-duration: 100ms; transition-timing-function: var(--e-out); }
  .gb:active::after, .gp:active::after { opacity: .16; transition-duration: 60ms; }
  @media (hover: hover) { .gb:hover:not(:active)::after, .gp:hover:not(:active)::after { opacity: .08; } }
  .gb svg { width: 22px; height: 22px; }
  .gp svg { width: 20px; height: 20px; flex: none; }
  .gp .nm { max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
  .gp.acc { background: var(--acc); -webkit-backdrop-filter: none; backdrop-filter: none; box-shadow: none; color: #fff; }
  .gb.l { width: 56px; height: 56px; } .gb.l svg { width: 30px; height: 30px; }
  .gb.xl { width: 72px; height: 72px; } .gb.xl svg { width: 34px; height: 34px; }
  .gb.bare { width: 36px; height: 36px; background: none; -webkit-backdrop-filter: none; backdrop-filter: none; box-shadow: none; }
  .gb.bare svg { width: 22px; height: 22px; filter: drop-shadow(0 1px 3px rgba(0,0,0,.55)); }
  .gb.l .gt { display: grid; place-items: center; transition: transform 300ms var(--p-bouncy); }
  #pB10.turn .gt { transform: rotate(-30deg); transition-duration: 90ms; transition-timing-function: var(--e-out); }
  #pF10.turn .gt { transform: rotate(30deg); transition-duration: 90ms; transition-timing-function: var(--e-out); }
  #pPlay .pg { position: absolute; inset: 0; transition: opacity 150ms var(--e-out); }
  #pPlay .gi { position: absolute; inset: 0; display: grid; place-items: center; transition: opacity 150ms var(--e-out); }
  #pPlay .i-play svg { margin-left: 3px; }
  #pPlay .i-play, #pPlay .i-again, #v-player.paused #pPlay .i-pause, #v-player.ended #pPlay .i-pause { opacity: 0; }
  #v-player.paused:not(.ended) #pPlay .i-play, #v-player.ended #pPlay .i-again { opacity: 1; }
  #v-player.ended .pmid .gb.l { opacity: 0; pointer-events: none; }
  .pend { position: absolute; left: 0; right: 0; top: 50%; margin-top: 48px; text-align: center; font-size: 13px; font-weight: 600; text-shadow: 0 1px 10px rgba(0,0,0,.8); opacity: 0; pointer-events: none; }
  #v-player.ended:not(.upn) .pend { opacity: 1; }
  #v-player.nexting #pNextBtn, #v-player.nexting #dNext { opacity: .45; pointer-events: none; }


  .ptop { display: flex; align-items: center; gap: 12px; }
  .pttl { flex: 1; min-width: 0; text-shadow: 0 1px 12px rgba(0,0,0,.45); }
  .pttl b { display: block; font-size: 17px; line-height: 1.3; font-weight: 650; letter-spacing: -.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pttl span { display: block; font-size: 13px; line-height: 1.35; color: var(--t2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pgrp { display: flex; gap: 8px; margin-left: auto; }
  .pmid { position: absolute; left: 0; right: 0; top: 50%; display: flex; align-items: center; justify-content: center; gap: 30px; transform: translateY(-50%); pointer-events: none; }
  .pbot { display: flex; flex-direction: column; gap: 6px; }
  .prow { display: flex; align-items: center; gap: 8px; }
  .prow .sp { flex: 1; }
  .pseek { display: flex; align-items: center; gap: 10px; }
  .ptime { flex: none; min-width: var(--tw, 5ch); font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; text-shadow: 0 1px 6px rgba(0,0,0,.6); }
  .ptime.r { text-align: right; color: var(--t2); }


  .scrub { position: relative; flex: 1; min-width: 0; height: 32px; touch-action: none; cursor: pointer; }
  .scrub .trk { position: absolute; left: 0; right: 0; top: 13px; height: 6px; border-radius: 3px; overflow: hidden; background: var(--trk); transition: transform 220ms var(--e-out); contain: layout paint; }
  .scrub .trk i { position: absolute; inset: 0; transform-origin: 0 50%; transform: scaleX(0); }
  .scrub .trk .buf { background: var(--trk-buf); }
  .scrub .trk .fill { background: var(--trk-fill); }
  .scrub .kpos, .scrub .bpos { position: absolute; left: 0; top: 16px; height: 0; pointer-events: none; }
  .scrub .kpos { right: 0; }
  .scrub .bpos { width: 0; }
  .scrub .knob { position: absolute; left: -8px; top: -8px; width: 16px; height: 16px; border-radius: 50%; background: #fff; box-shadow: 0 1px 6px rgba(0,0,0,.45); opacity: 0; transform: scale(.4); transition: transform 300ms var(--p-bouncy), opacity 140ms var(--e-out); }
  .scrub .bub {
    position: absolute; left: 0; bottom: 14px; padding: 5px 9px; border-radius: 9px; transform: translateX(-50%); white-space: nowrap; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums;
    background: var(--glass-solid); -webkit-backdrop-filter: var(--glass-fx); backdrop-filter: var(--glass-fx); box-shadow: inset 0 0 0 .5px var(--glass-edge);
    opacity: 0; visibility: hidden; transition: opacity 140ms var(--e-out), visibility 0s linear 140ms; pointer-events: none;
  }
  .scrub.on .trk, .scrub.hov .trk, #v-player.gx .scrub .trk { transform: scaleY(1.6); }
  .scrub.on .knob, .scrub.hov .knob, #v-player.gx .scrub .knob { opacity: 1; transform: none; }
  .scrub.on .bub, .scrub.hov .bub { opacity: 1; visibility: visible; transition-delay: 0s; }
  #v-player.gx .scrub .bub { opacity: 0; visibility: hidden; }
  @media (pointer: coarse) { .scrub { height: 44px; margin: -6px 0; } .scrub .trk { top: 19px; } .scrub .kpos, .scrub .bpos { top: 22px; } }
  .scrub:focus-visible, .vol .vsl:focus-visible { outline: none; }
  .scrub:focus-visible .trk, .vol .vsl:focus-visible .trk { outline: 2px solid var(--acc-hi); outline-offset: 3px; }
  .scrub:focus-visible .knob { opacity: 1; transform: none; }


  .vol { display: flex; align-items: center; gap: 4px; }
  .vol .vsl { position: relative; width: 84px; height: 32px; cursor: pointer; touch-action: none; }
  .vol .vsl .trk { position: absolute; left: 0; right: 0; top: 14px; height: 4px; border-radius: 2px; overflow: hidden; background: var(--trk); }
  .vol .vsl .trk i { position: absolute; inset: 0; background: var(--t1); transform-origin: 0 50%; }
  @media (pointer: coarse) { .vol { display: none; } }


  .pbusy { position: absolute; left: 50%; top: 50%; width: 0; height: 0; pointer-events: none; opacity: 0; visibility: hidden; transition: opacity 180ms var(--e-out), visibility 0s linear 180ms; }
  .pbusy::before { content: ""; position: absolute; left: -30px; top: -30px; width: 60px; height: 60px; border-radius: 50%; background: rgba(0,0,0,.34); opacity: 0; transition: opacity 200ms var(--e-out); }
  #v-player.hide .pbusy::before { opacity: 1; }
  .pbusy .aspin { position: absolute; left: -18px; top: -18px; width: 36px; height: 36px; color: #fff; animation: spin 800ms steps(8) infinite; animation-play-state: paused; }
  .pbusy .aspin svg { width: 100%; height: 100%; }
  .pbusy .cap { position: absolute; top: 46px; left: 0; transform: translateX(-50%); width: max-content; max-width: calc(100vw - 40px); text-align: center; font-size: 13px; font-weight: 500; text-shadow: 0 1px 10px rgba(0,0,0,.8); opacity: 0; transition: opacity 240ms var(--e-out); }
  #v-player.busy .pbusy { opacity: 1; visibility: visible; transition: opacity 180ms var(--e-out), visibility 0s; }
  #v-player.busy .pbusy .aspin { animation-play-state: running; }
  #v-player.busy #pPlay .pg { opacity: 0; }
  #v-player.slow .pbusy .cap { opacity: 1; }


  .phud {
    position: absolute; left: 50%; top: 12px; display: flex; align-items: center; gap: 10px; height: 40px; padding: 0 16px 0 12px; border-radius: 20px;
    background: var(--glass-solid); -webkit-backdrop-filter: var(--glass-fx); backdrop-filter: var(--glass-fx); box-shadow: inset 0 0 0 .5px var(--glass-edge), 0 10px 30px rgba(0,0,0,.25);
    transform: translateX(-50%); opacity: 0; visibility: hidden; transition: opacity 180ms var(--e-out), visibility 0s linear 180ms; pointer-events: none; white-space: nowrap;
  }
  .phud.on { opacity: 1; visibility: visible; transition: opacity 90ms var(--e-out), visibility 0s; }
  .phud svg { width: 22px; height: 22px; flex: none; }
  .phud .hb { position: relative; width: 120px; height: 5px; border-radius: 3px; overflow: hidden; background: var(--trk); }
  .phud .hb i { position: absolute; inset: 0; background: var(--t1); transform-origin: 0 50%; }
  .phud b { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .phud.big { height: auto; padding: 9px 20px 10px; flex-direction: column; gap: 0; border-radius: 18px; }
  .phud.big b { font-size: 26px; font-weight: 700; letter-spacing: -.01em; line-height: 1.15; }
  .phud.big small { font-size: 13px; font-weight: 500; color: var(--t2); font-variant-numeric: tabular-nums; }


  .prip { position: absolute; top: -30%; bottom: -30%; width: 52%; border-radius: 50%; background: rgba(255,255,255,.1); pointer-events: none; opacity: 0; visibility: hidden; transition: opacity 280ms var(--e-out), visibility 0s linear 280ms; }
  .prip.l { left: -16%; } .prip.r { right: -16%; }
  .prip.on { opacity: 1; visibility: visible; transition: opacity 90ms var(--e-out), visibility 0s; }
  .prip .rc { position: absolute; top: 50%; display: flex; flex-direction: column; align-items: center; gap: 6px; transform: translate(-50%, -50%); font-size: 13px; font-weight: 600; white-space: nowrap; text-shadow: 0 1px 8px rgba(0,0,0,.5); }
  .prip.l .rc { left: 62%; } .prip.r .rc { left: 38%; }
  #v-player:not(.hide) .prip .rc { top: calc(50% + 54px); }
  .prip .arr { display: flex; gap: 1px; }
  .prip .arr i { display: block; opacity: .3; } .prip .arr svg { width: 13px; height: 13px; }
  .prip.on .arr i { animation: arr 720ms linear infinite; }
  .prip.on .arr i:nth-child(2) { animation-delay: 120ms; } .prip.on .arr i:nth-child(3) { animation-delay: 240ms; }
  .prip.r .arr { flex-direction: row; } .prip.l .arr { flex-direction: row-reverse; }
  @keyframes arr { 0%, 100% { opacity: .3; } 35% { opacity: 1; } }
  .tapc { position: absolute; width: 96px; height: 96px; margin: -48px 0 0 -48px; border-radius: 50%; background: rgba(255,255,255,.26); pointer-events: none; animation: tapc 460ms var(--e-out) forwards; }
  @keyframes tapc { from { transform: scale(.25); opacity: 1; } to { transform: scale(1.7); opacity: 0; } }


  .perr {
    position: absolute; left: 50%; top: 50%; width: min(380px, calc(100% - 32px)); padding: 18px 18px 16px; border-radius: 20px; text-align: center;
    background: var(--glass-solid); -webkit-backdrop-filter: var(--glass-fx); backdrop-filter: var(--glass-fx); box-shadow: inset 0 0 0 .5px var(--glass-edge), 0 18px 50px rgba(0,0,0,.4);
    transform: translate(-50%, -50%);
  }
  .perr > svg { width: 28px; height: 28px; margin: 0 auto 8px; color: var(--danger); }
  .perr b { display: block; font-size: 17px; font-weight: 650; }
  .perr p { margin: 4px 0 0; font-size: 13px; line-height: 1.4; color: var(--t2); overflow-wrap: anywhere; }
  .perr p:empty { display: none; }
  .perr .acts { display: flex; justify-content: center; gap: 8px; margin-top: 14px; }
  .perr .acts button { position: relative; height: 38px; padding: 0 16px; border-radius: 19px; font-size: 14px; font-weight: 600; background: rgba(255,255,255,.12); transition: transform 420ms var(--p-spring); }
  .perr .acts button::before { content: ""; position: absolute; inset: -3px -2px; }
  .perr .acts button:active { transform: scale(.94); transition-duration: 100ms; }
  .perr .acts .pri { background: var(--acc); color: #fff; }
  #v-player.err .pmid, #v-player.err .pbusy, #v-player.err .pend { display: none; }


  .pupn {
    position: absolute; right: max(20px, env(safe-area-inset-right)); bottom: calc(max(10px, var(--bot)) + 106px); display: flex; gap: 12px; width: min(380px, calc(100% - 40px)); padding: 10px; border-radius: 18px;
    background: var(--glass-solid); -webkit-backdrop-filter: var(--glass-fx); backdrop-filter: var(--glass-fx); box-shadow: inset 0 0 0 .5px var(--glass-edge), 0 18px 50px rgba(0,0,0,.35);
  }
  #v-player.hide .pupn { background: var(--glass-opaque); -webkit-backdrop-filter: none; backdrop-filter: none; }
  .pupn img { flex: none; width: 132px; aspect-ratio: 16 / 9; border-radius: 10px; object-fit: cover; background: var(--s3); }
  .pupn .t { min-width: 0; padding-right: 26px; }
  .pupn small { display: block; font-size: 12px; font-weight: 600; color: var(--t2); }
  .pupn b { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin-top: 2px; font-size: 15px; font-weight: 650; line-height: 1.25; }
  .pupn .upgo { position: relative; overflow: hidden; display: inline-flex; align-items: center; gap: 6px; height: 36px; margin-top: 8px; padding: 0 16px 0 12px; border-radius: 18px; background: rgba(255,255,255,.14); font-size: 14px; font-weight: 600; isolation: isolate; transition: transform 420ms var(--p-spring); }
  .pupn .upgo:active { transform: scale(.94); transition-duration: 100ms; }
  .pupn .upgo svg { width: 16px; height: 16px; }
  .pupn .upgo::before { content: ""; position: absolute; inset: 0; background: rgba(128,84,245,.25); transform-origin: 0 50%; transform: scaleX(0); z-index: -1; }
  .pupn.count .upgo::before { animation: count 8s linear forwards; }
  @keyframes count { to { transform: scaleX(1); } }
  .pupn .x { position: absolute; top: 8px; right: 8px; width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center; color: var(--t2); background: rgba(255,255,255,.1); }
  .pupn .x::before { content: ""; position: absolute; inset: -9px; border-radius: 50%; }
  .pupn .x svg { width: 12px; height: 12px; }
  @media (pointer: coarse) { .pupn .upgo { height: 44px; border-radius: 22px; } }
  #v-player.upn #pNextBtn, #v-player.upn #pNext, #v-player.m-inline.upn #dNext { display: none; }

  .pupn.upline { top: calc(var(--top) + 62px); bottom: auto; right: max(16px, env(safe-area-inset-right)); width: min(400px, calc(100% - 32px)); padding: 6px; align-items: center; gap: 10px; border-radius: 16px; }
  .pupn.upline img { width: 76px; border-radius: 8px; }
  .pupn.upline .t { flex: 1; display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 10px; align-items: center; padding-right: 40px; }
  .pupn.upline small, .pupn.upline b { grid-column: 1; }
  .pupn.upline b { -webkit-line-clamp: 1; margin-top: 0; font-size: 14px; }
  .pupn.upline .upgo { grid-column: 2; grid-row: 1 / span 2; margin-top: 0; }
  .pupn.upline .x { top: 50%; margin-top: -13px; right: 9px; }
  #v-player.m-rot .pupn.upline { top: max(62px, calc(env(safe-area-inset-right) + 50px)); right: max(16px, env(safe-area-inset-bottom)); }
  .presume { position: absolute; left: max(16px, env(safe-area-inset-left)); bottom: 76px; background: var(--glass-opaque); -webkit-backdrop-filter: none; backdrop-filter: none; }
  #v-player.m-rot .presume { left: max(16px, env(safe-area-inset-top)); }


  .pmenu {
    position: absolute; right: 12px; bottom: 64px; z-index: 5; width: 290px; max-width: calc(100% - 24px); max-height: calc(100% - 24px); overflow-y: auto; overscroll-behavior: contain; padding: 6px;
    scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.22) transparent;
    border-radius: 20px; background: var(--glass-solid); -webkit-backdrop-filter: var(--glass-fx); backdrop-filter: var(--glass-fx); box-shadow: inset 0 0 0 .5px var(--glass-edge), 0 18px 50px rgba(0,0,0,.45);
    transform-origin: 100% 100%; opacity: 0; transform: scale(.92); visibility: hidden; transition: opacity 160ms var(--e-out), transform 260ms var(--p-spring), visibility 0s linear 260ms;
  }
  .pmenu.open { opacity: 1; transform: none; visibility: visible; transition-delay: 0s; }
  .pmenu .h { padding: 8px 12px 4px; font-size: 13px; font-weight: 600; color: var(--t3); }
  .mi { position: relative; display: flex; align-items: center; gap: 10px; width: 100%; min-height: 44px; padding: 0 12px 0 10px; border-radius: 14px; text-align: left; font-size: 15px; font-weight: 500; isolation: isolate; }
  .mi::after { content: ""; position: absolute; inset: 0; border-radius: inherit; background: rgba(255,255,255,.08); opacity: 0; z-index: -1; transition: opacity 140ms var(--e-out); }
  .mi:active::after { opacity: 1; transition-duration: 40ms; }
  @media (hover: hover) { .mi:hover::after { opacity: 1; } }
  .mi:focus-visible { outline-offset: -2px; }
  .mi svg { width: 20px; height: 20px; flex: none; }
  .mi .ck { width: 18px; height: 18px; color: var(--acc-tx); visibility: hidden; }
  .mi.on { color: var(--acc-tx); font-weight: 600; }
  .mi.on .ck { visibility: visible; }
  .mi .tx { flex: none; max-width: 62%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mi small { flex: 1 1 0; min-width: 0; padding-left: 12px; text-align: right; font-size: 13px; font-weight: 500; color: var(--t3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pmenu hr { height: 1px; margin: 6px 10px; border: 0; background: var(--line); }
  .speeds { display: flex; gap: 4px; padding: 2px 6px 8px; }
  .speeds button { flex: 1; height: 32px; border-radius: 10px; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--t2); background: rgba(255,255,255,.07); }
  .speeds button.on { color: #fff; background: var(--acc); }
  @media (pointer: coarse) { .speeds button { height: 44px; } }


  #v-player.m-inline .pttl, #v-player.m-inline .prow, #v-player.m-inline .vol { display: none; }
  #v-player.m-inline .pctl::before { height: 38%; } #v-player.m-inline .pctl::after { height: 46%; }
  #v-player.m-inline .gb { width: 36px; height: 36px; } #v-player.m-inline .gb svg { width: 20px; height: 20px; }
  #v-player.m-inline .gb.l { width: 46px; height: 46px; } #v-player.m-inline .gb.l svg { width: 27px; height: 27px; }
  #v-player.m-inline .gb.xl { width: 58px; height: 58px; } #v-player.m-inline .gb.xl svg { width: 28px; height: 28px; }
  #v-player.m-inline .gb.bare svg { width: 22px; height: 22px; }
  #v-player.m-inline .pmid { gap: 28px; }
  #v-player.m-inline .pend { margin-top: 38px; }
  #v-player.m-inline .perr { padding: 10px 14px 12px; } #v-player.m-inline .perr > svg { display: none; } #v-player.m-inline .perr b { font-size: 15px; }
  #v-player.m-inline .perr p { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font-size: 12px; }
  #v-player.m-inline .perr .acts { margin-top: 8px; } #v-player.m-inline .perr .acts button { height: 38px; padding: 0 14px; font-size: 13px; }
  #v-player.m-inline .perr { top: calc(50% + 20px); }
  #v-player.m-inline .pupn, #v-player.m-inline.hide .pupn { position: relative; right: auto; bottom: auto; width: auto; margin-top: 14px; padding: 10px; border-radius: 16px; background: var(--s1); -webkit-backdrop-filter: none; backdrop-filter: none; box-shadow: none; }
  #v-player.m-inline .pupn img { width: 112px; }
  #v-player.m-inline .presume { left: 10px; bottom: 44px; height: 36px; padding: 0 12px 0 10px; font-size: 13px; }
  #v-player.m-inline .presume svg { width: 16px; height: 16px; }
  #v-player.m-inline .pbusy .cap { top: 38px; }
  #v-player.m-inline .pwhere { margin-top: 40px; }
  @media (min-width: 600px) {
    #v-player.m-inline .gb { width: 40px; height: 40px; } #v-player.m-inline .gb svg { width: 22px; height: 22px; }
    #v-player.m-inline .gb.l { width: 56px; height: 56px; } #v-player.m-inline .gb.l svg { width: 30px; height: 30px; }
    #v-player.m-inline .gb.xl { width: 72px; height: 72px; } #v-player.m-inline .gb.xl svg { width: 34px; height: 34px; }
    #v-player.m-inline .pmid { gap: 44px; }
    #v-player.m-inline .pctl { padding: 12px 16px 8px; }
    #v-player.m-inline .pend, #v-player.m-inline .pwhere { margin-top: 50px; } #v-player.m-inline .pbusy .cap { top: 46px; }
  }

  #v-player.m-full .pframe { position: absolute; inset: 0; width: auto; height: auto; aspect-ratio: auto; margin: 0; }
  #v-player.m-full .pdock { display: none; }
  #v-player.m-full .pctl { padding: calc(12px + var(--top)) max(16px, env(safe-area-inset-right)) max(10px, var(--bot)) max(16px, env(safe-area-inset-left)); }
  #v-player.m-full .pseek .fsb { display: none; }
  #v-player.m-full .pmid { gap: 44px; }
  #v-player.m-full .phud { top: calc(14px + var(--top)); }
  @media (pointer: coarse) { #v-player.fs .ptop { padding-top: 44px; } }
  @media (min-width: 1100px) {
    #v-player.m-full .pctl { padding: 14px 32px 18px; }
    #v-player.m-full .gb { width: 44px; height: 44px; }
    #v-player.m-full .gb.bare { width: 40px; height: 40px; }
    #v-player.m-full .gb.l { width: 64px; height: 64px; } #v-player.m-full .gb.l svg { width: 34px; height: 34px; }
    #v-player.m-full .gb.xl { width: 84px; height: 84px; } #v-player.m-full .gb.xl svg { width: 40px; height: 40px; }
    #v-player.m-full .pmid { gap: 64px; }
    #v-player.m-full .pttl b { font-size: 19px; }
    #v-player.m-full .pttl span { font-size: 14px; }
    #v-player.m-full .ptime { font-size: 13px; }
    #v-player.m-full .phud { top: 26px; }
    #v-player.m-full .pbusy .aspin { left: -22px; top: -22px; width: 44px; height: 44px; }
    #v-player.m-full .pbusy::before { left: -36px; top: -36px; width: 72px; height: 72px; }
    #v-player.m-full .pbusy .cap { top: 58px; font-size: 14px; }
    #v-player.m-full .pend { margin-top: 60px; font-size: 14px; }
    #v-player.m-full .pupn:not(.upline) { bottom: 124px; right: 32px; }
    #v-player.m-full .presume { bottom: 96px; left: 32px; }
  }

  #v-player.m-rot .pframe { position: fixed; top: 0; left: 0; right: auto; bottom: auto; width: var(--vh, 100vh); height: var(--vw, 100vw); transform-origin: 0 0; transform: rotate(90deg) translate3d(0, -100%, 0); }
  #v-player.m-rot .pctl { padding: max(12px, env(safe-area-inset-right)) max(16px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)) max(16px, env(safe-area-inset-top)); }
  #v-player.m-rot .phud { top: max(14px, env(safe-area-inset-right)); }


  .pdock { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 16px var(--g) calc(24px + var(--bot)); background: var(--bg); scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.22) transparent; }
  @media (min-width: 760px) { .pdock > * { max-width: 720px; } }
  .pdock h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -.015em; line-height: 1.2; }
  .pdock .sub { margin-top: 3px; font-size: 13px; color: var(--t3); }
  .dacts { display: flex; gap: 8px; margin: 14px calc(var(--g) * -1) 0; padding: 0 var(--g) 2px; overflow-x: auto; scrollbar-width: none; overscroll-behavior-x: contain; }
  .dacts::-webkit-scrollbar { display: none; }
  .db { position: relative; flex: none; display: inline-flex; align-items: center; gap: 7px; height: 38px; padding: 0 15px 0 12px; border-radius: 19px; background: var(--s2); font-size: 14px; font-weight: 600; white-space: nowrap; isolation: isolate; cursor: pointer; transition: transform 420ms var(--p-spring); }
  .db::after { content: ""; position: absolute; inset: 0; border-radius: inherit; background: #fff; opacity: 0; z-index: -1; transition: opacity 180ms var(--e-out); }
  .db:active { transform: scale(.95); transition-duration: 90ms; } .db:active::after { opacity: .08; transition-duration: 50ms; }
  @media (hover: hover) { .db:hover:not(:active)::after { opacity: .05; } }
  @media (pointer: coarse) { .db { height: 44px; border-radius: 22px; } }
  .db.acc { background: var(--acc); color: #fff; }
  .db svg { width: 18px; height: 18px; flex: none; }
  .db .dn { width: 14px; height: 14px; margin-right: -3px; color: var(--t2); }
  .db .nm { max-width: 44vw; overflow: hidden; text-overflow: ellipsis; }
  .db select { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; border: 0; font-size: 16px; -webkit-appearance: none; appearance: none; cursor: pointer; }
  #v-player.shield .pdock, #v-player.shield .pctl button, #v-player.shield .pctl .scrub, #v-player.shield .pctl .vol { pointer-events: none; }
  @media (max-width: 399px) { #dCopy span { display: none; } #dCopy { width: 44px; padding: 0; justify-content: center; } }
  .pver { margin-top: 18px; padding: 12px 14px; border-radius: 16px; background: var(--s1); }
  .pver .hd { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
  .pver .chg { position: relative; font-size: 15px; font-weight: 600; color: var(--acc-tx); }
  .pver .chg::before { content: ""; position: absolute; inset: -12px -8px; }
  .pver .vk { display: flex; align-items: center; gap: 2px; }
  .lbl { margin-bottom: 6px; font-size: 13px; line-height: 18px; font-weight: 600; letter-spacing: -.006em; color: var(--t3); }
  .upnext { margin-top: 24px; }
  .upnext .ep { padding: 6px 0; grid-template-columns: minmax(0, 42%) minmax(0, 1fr); gap: 12px; }
  .spin { flex: none; width: 16px; height: 16px; border-radius: 50%; border: 2px solid rgba(255,255,255,.18); border-top-color: var(--acc-hi); animation: spin .8s linear infinite; }
  @media (prefers-reduced-motion: reduce) {
    .pbusy .aspin, .spin { animation-duration: 800ms !important; animation-iteration-count: infinite !important; }
    .pupn.count .upgo::before { animation-duration: 8s !important; }
  }


  .toast {
    position: fixed; left: 50%; top: calc(var(--top) + 10px); z-index: 100; display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 10px 18px 10px 14px;
    width: max-content; max-width: calc(100vw - 32px); border-radius: 22px; background: rgba(23,28,35,.72); color: var(--t1);
    -webkit-backdrop-filter: var(--blur); backdrop-filter: var(--blur); box-shadow: var(--edge), 0 10px 30px rgba(0,0,0,.4);
    font: 600 15px/20px var(--font); letter-spacing: -.016em; pointer-events: none; opacity: 0; visibility: hidden;
    transform: translate3d(-50%, -16px, 0) scale(.96); transition: transform 200ms var(--e-in), opacity 200ms var(--e-out), visibility 0s linear 200ms;
  }
  .toast.show { opacity: 1; visibility: visible; transform: translate3d(-50%, 0, 0); transition: transform var(--sp-snappy-ms) var(--sp-snappy), opacity 160ms var(--e-out), visibility 0s; }
  #v-player:not(.hidden) ~ .toast { top: calc(var(--top) + 68px); background: rgba(23,28,35,.96); -webkit-backdrop-filter: none; backdrop-filter: none; }
  #v-player.m-inline:not(.hidden) ~ .toast { top: calc(var(--top) + 56.25vw + 12px); }
  @supports not ((-webkit-backdrop-filter: blur(1px)) or (backdrop-filter: blur(1px))) { .toast { background: rgba(23,28,35,.96); } }
  .toast svg { width: 18px; height: 18px; flex: none; color: var(--acc-tx); }
  .toast.err svg { color: var(--danger); }

  /* Frame interface: borderless surfaces, confident actions, readable collections. */
  :root { --line: rgba(255,255,255,.09); --gap: 12px; --r-xl: 24px; }
  body { letter-spacing: -.012em; }
  button, a, input, select { touch-action: manipulation; }
  :focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
  .skip-link { position: fixed; top: -80px; left: var(--g); z-index: 120; padding: 12px 18px; border-radius: 10px; color: #fff; background: var(--acc); font-weight: 650; }
  .skip-link:focus { top: calc(var(--top) + 10px); }
  .mark { background: var(--acc); border-radius: 8px; }
  .mark svg { fill: #fff; }
  .brand { font-size: 22px; letter-spacing: -.06em; }
  .btn { border-radius: 12px; color: #fff; height: 48px; padding-inline: 22px; font-size: 15px; font-weight: 650; letter-spacing: -.015em; box-shadow: none; }
  .btn.sec { color: var(--t1); background: rgba(255,255,255,.09); box-shadow: none; -webkit-backdrop-filter: none; backdrop-filter: none; }
  .btn.flat, .cuv .btn { color: var(--t1); }
  .cw .hov, .ep .th .hov, .db.acc, .gp.acc, .perr .acts .pri, .speeds button.on { color: #fff; }
  .ibtn.glass { border-radius: 12px; box-shadow: none; }
  .ibtn.glass:active, .tb-search:active { transform: scale(.96); }
  #nav::before { background: linear-gradient(var(--bg), rgba(11,13,16,.65), transparent); }
  .tb-cap { border-radius: 14px; padding: 3px; background: #15191f; box-shadow: none; -webkit-backdrop-filter: none; backdrop-filter: none; }
  .tb, .tb-pill { border-radius: 11px; }
  .tb { font-size: 14px; font-weight: 550; letter-spacing: -.012em; }
  .tb-pill { background: var(--acc-soft); }
  .tb.on { color: #fff; }
  .tb-search { display: flex; justify-content: center; gap: 9px; width: 136px; border-radius: 12px; background: #15191f; box-shadow: none; -webkit-backdrop-filter: none; backdrop-filter: none; font-size: 13px; }
  .tb-search svg { width: 17px; height: 17px; }
  .tb-search kbd { display: none; place-items: center; height: 22px; min-width: 20px; border: 0; border-radius: 4px; color: var(--t3); font: 12px var(--font); }
  .logo, .tlogo { filter: brightness(0) invert(1) drop-shadow(0 2px 10px #0006); }
  .hero .desc { color: #d1d5db; line-height: 1.5; max-width: 450px; }
  .hero .meta { color: #c5cbd4; }
  .hero .acts { gap: 10px; }
  .hero .hm { background: rgba(255,255,255,.07); }
  .row { margin-top: 34px; contain-intrinsic-size: auto 350px; }
  .row h2, .lsect { font-weight: 650; letter-spacing: -.03em; }
  .row h2 { display: flex; align-items: center; margin-bottom: 10px; font-size: 21px; line-height: 28px; }
  .sc { padding-top: 4px; padding-bottom: 10px; }
  .card { width: calc((100vw - var(--g) - var(--gap) * 2) / 2.45); }
  .catalog-card { aspect-ratio: auto; background: transparent; overflow: visible; border-radius: 0; }
  .catalog-card::after { display: none; }
  .poster-frame { position: relative; aspect-ratio: 2 / 3; border-radius: 12px; overflow: hidden; background: var(--s1); box-shadow: none; }
  .poster-frame::after { display: none; content: ''; position: absolute; inset: 0; pointer-events: none; border-radius: inherit; border: 1px solid rgba(255,255,255,.08); }
  .poster-frame img { position: absolute; inset: 0; transition: opacity 240ms var(--e-out), transform 350ms var(--e-out); }
  .catalog-caption { padding: 10px 1px 0; }
  .catalog-caption b { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; line-height: 19px; font-weight: 600; letter-spacing: -.015em; }
  .catalog-caption span { display: block; margin-top: 3px; color: var(--t3); font-size: 11px; line-height: 16px; }
  .catalog-card:active { transform: scale(.98); }
  .catalog-card:focus-visible { border-radius: 12px; }
  .arrow { background: #252b33; border: 0; box-shadow: 0 4px 16px #0006; margin-top: -46px; }
  .arrow.l { left: max(8px, calc(var(--g) - 22px)); } .arrow.r { right: max(8px, calc(var(--gr) - 22px)); }
  .cw .im { border: 0; }
  .cw .tx { padding-top: 10px; }
  .cw .pb { height: 2px; }
  .page-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 38px var(--gr) 28px var(--g); }
  .page-heading .lt-page { padding: 0; font-size: 38px; line-height: 1.15; letter-spacing: -.045em; font-weight: 650; }
  .install-action { height: 42px; font-size: 13px; padding-inline: 14px; }
  .field input { height: 52px; border-radius: 12px; padding-left: 46px; background: var(--s1); border: 0; font-size: 16px; }
  .field input:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; box-shadow: none; }
  .field .ic { left: 17px; top: 17px; }
  .field .clr { height: 52px; }
  .sbar { padding-top: 0; padding-bottom: 18px; }
  .sbar.stuck { padding-top: 12px; }
  .note { margin-bottom: 18px; }
  .grid { gap: 24px 14px; }
  .res .nm2 { font-size: 13px; line-height: 19px; font-weight: 600; }
  .res .yr { margin-top: 3px; font-size: 11px; }
  .res .card { border-radius: 12px; }
  .cuv { padding-top: 9vh; gap: 10px; }
  .cuv > svg { color: var(--t2); padding: 16px; width: 76px; height: 76px; border: 0; background: var(--s1); border-radius: 20px; margin-bottom: 8px; }
  .cuv b { font-size: 23px; letter-spacing: -.035em; font-weight: 650; }
  .cuv p { line-height: 1.6; max-width: 370px; color: var(--t3); }
  .cuv .btn { background: var(--s2); }
  .lgroup { border: 0; }
  .lrow { padding: 12px 8px 12px 12px; }
  .lrow .nm { font-size: 15px; font-weight: 600; }
  .lrow .sub { font-size: 12px; margin-top: 4px; }
  .tname { letter-spacing: -.04em; }
  .thead { min-height: 0; }
  .title-about { max-width: 720px; margin-top: 4px; }
  .title-about > summary, .stream-details > summary { display: flex; align-items: center; gap: 8px; width: fit-content; min-height: 44px; list-style: none; cursor: pointer; color: var(--t2); font-size: 13px; font-weight: 500; border-radius: 6px; }
  .title-about > summary::-webkit-details-marker, .stream-details > summary::-webkit-details-marker { display: none; }
  .title-about > summary::after, .stream-details > summary::after { content: "›"; font-size: 20px; line-height: 1; color: var(--t3); transform: rotate(90deg); transition: transform 160ms var(--e-out); }
  .title-about[open] > summary::after, .stream-details[open] > summary::after { transform: rotate(-90deg); }
  .title-about > summary:active, .stream-details > summary:active { color: var(--t1); }
  .title-about[open] { margin-bottom: 18px; }
  .title-about .tdesc { margin-top: 0; }
  .title-about .credits { margin-top: 10px; }
  .tdesc { line-height: 1.6; }
  .credits { line-height: 1.6; }
  .sect { font-size: 23px; line-height: 30px; letter-spacing: -.035em; font-weight: 650; }
  .tbody .sect { margin-top: 18px; }
  #tVersions, #tSeries { min-height: 0; padding-bottom: 16px; }
  .vtoolbar { position: sticky; top: calc(var(--top) + var(--nav-h)); z-index: 6; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; min-height: 54px; margin: 0 0 12px; padding: 8px 0; background: var(--bg); }
  .sscroll .vtoolbar { top: 0; background: var(--s1); }
  #tCount { display: none; }
  .vsummary { color: var(--t2); font-size: 13px; line-height: 20px; font-variant-numeric: tabular-nums; }
  .vsummary b, .vsummary strong { color: var(--t1); font-weight: 700; }
  .vsort { display: inline-flex; align-items: center; gap: 8px; color: var(--t3); font-size: 12px; }
  .vsort select { max-width: 170px; height: 36px; border: 0; border-radius: 8px; padding: 0 9px; font-size: 12px; color: var(--t1); background: var(--s1); }
  .vhint { margin: 0 0 16px; font-size: 12px; line-height: 1.6; color: var(--t3); }
  .vgroup, .tbody .vgroup { display: block; border: 0; border-radius: 0; overflow: visible; background: transparent; }
  .ver, .tbody .ver { padding: 0 6px 0 0; gap: 2px; border-radius: 12px; background: transparent; cursor: default; }
  .vplay { display: flex; flex: 1; align-items: center; min-width: 0; min-height: 96px; gap: 18px; padding: 16px 12px 16px 0; text-align: left; color: inherit; }
  .vload .ver { min-height: 96px; padding: 18px; gap: 18px; }
  .vplay:focus-visible { outline-offset: -3px; border-radius: 10px; }
  .ver + .ver::before, .tbody .ver + .ver::before { display: none; }
  /* The press state reaches full opacity: keep its surface dark at every opacity. */
  .ver::after { background: #1b1827; border-radius: inherit; }
  .ver:focus-within::after { opacity: .65; }
  .vk { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; font-size: 15px; line-height: 22px; font-weight: 600; letter-spacing: -.02em; }
  .vk .pict { margin: 0; width: 15px; height: 15px; }
  .vbadge { display: inline-block; border: 0; padding: 0; border-radius: 0; font-size: 10px; line-height: 16px; font-weight: 650; white-space: nowrap; color: var(--t2); background: none; letter-spacing: .015em; }
  .vbadge[data-kind='orig'] { color: var(--acc-tx); }
  .vbadge[data-kind='asis'] { color: var(--t2); }
  .vbadge[data-kind='conv'] { color: var(--t2); }
  .vs { display: block; margin-top: 5px; font-size: 13px; line-height: 19px; color: var(--t3); }
  .vs b { font-weight: 600; }
  .vf { margin-top: 4px; color: var(--t3); font-size: 11px; line-height: 16px; }
  .go { width: 36px; height: 36px; border-radius: 50%; background: var(--acc-soft); color: var(--acc-tx); }
  .ver .dl { margin: 0; width: 38px; height: 44px; border-radius: 9px; }
  .ver .dl.cp { margin: 0; }
  .ver .dl svg { width: 16px; height: 16px; }
  .ver:hover:not(:has(.dl:hover)) .go, .ver:active:not(:has(.dl:active)) .go { color: #fff; }
  .vmore, .tbody .vmore { width: 100%; height: 48px; justify-content: center; border-radius: 0; font-size: 13px; background: transparent; }
  .vmore::before, .tbody .vmore::before { display: none; }
  .vmore-sentinel { display: block; height: 1px; pointer-events: none; }
  .vmsg { border: 0; padding: 18px; border-radius: 12px; background: var(--s1); }
  .vmsg p { line-height: 1.6; }
  .pill { height: 38px; border-radius: 9px; font-size: 13px; }
  .ep .th { border-radius: 12px; }
  .ep .tt { font-weight: 600; }
  .ep p { line-height: 1.55; }
  .panel { background: var(--s1); border: 0; box-shadow: 0 20px 80px #0008; }
  .shead { padding-bottom: 18px; border-bottom: 0; }
  .sscroll { padding-top: 20px; }
  .shthumb { border-radius: 9px; }
  .sscroll .vgroup { background: transparent; }
  .toast { border-radius: 12px; background: #252b33; font-size: 13px; }

  /* Browser-owned Apple video controls must receive every touch on the video. */
  #v-player.native-player .pframe, #v-player.native-player #vid { touch-action: auto; }
  #v-player.native-player .pframe { cursor: auto; }
  #v-player.native-player .pframe::before, #v-player.native-player .pframe::after,
  #v-player.native-player #pCtl, #v-player.native-player #pDim, #v-player.native-player #pGest,
  #v-player.native-player #pMenu, #v-player.native-player #pRipL, #v-player.native-player #pRipR,
  #v-player.native-player #pHud, #v-player.native-player #pRestart, #v-player.native-player #pScrub { display: none !important; }
  #v-player.native-player #vid { object-fit: contain; transform: none !important; filter: none !important; transition: none; }
  #v-player.native-player #pBusy, #v-player.native-player #pCover, #v-player.native-player #pSnap { pointer-events: none; }
  #v-player.native-player .pdock { display: block; }
  .native-actions { display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 20px; padding-bottom: 16px; border-bottom: 0; }
  .native-actions .db { height: 42px; border-radius: 10px; padding: 0 13px; font-size: 12px; }
  .native-actions #pNativeBack { margin-right: auto; }
  .native-actions #pNativePlay { background: var(--acc); color: #fff; }
  .native-status { flex-basis: 100%; width: 100%; margin: 4px 0 0; color: var(--t3); font-size: 12px; line-height: 1.5; }
  .native-status:empty { display: none; }
  .pip-return { position: fixed; right: var(--gr); bottom: calc(var(--bot) + 20px); z-index: 65; display: inline-flex; align-items: center; gap: 8px; min-height: 46px; padding: 0 18px; border-radius: 12px; background: var(--acc); color: #fff; font-size: 13px; font-weight: 650; box-shadow: 0 6px 30px #0007; }
  .pip-return svg { width: 18px; height: 18px; }
  .pdock { padding-top: 22px; }
  .pdock h1 { letter-spacing: -.035em; }
  .db { border-radius: 10px; }
  .pver { margin-top: 14px; padding: 0; border: 0; border-radius: 0; background: transparent; }
  .pver .hd { align-items: center; gap: 18px; }
  .pver .hd > #pVer { flex: 1; min-width: 0; display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 12px; }
  .pver .hd .vk, .pver .hd .vs { margin: 0; }
  .pver .chg { min-height: 44px; flex: none; font-size: 13px; }
  .pver .chg::before { inset: 0 -6px; }
  .stream-details { margin-top: 2px; }
  #pFileDetails { padding: 0 0 10px; }
  #pFileDetails .vk { font-size: 13px; line-height: 1.5; }
  #pFileDetails .vs { font-size: 12px; line-height: 1.6; }
  #pFileDetails .stream-filename { margin-top: 6px; color: var(--t3); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; white-space: normal; }
  #pFileDetails .vf { display: block; margin-top: 6px; color: var(--t3); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; white-space: normal; }
  #pFileDetails .vf .h, #pFileDetails .vf .t { display: inline; white-space: normal; }
  .stream-details #dCopy { width: auto; min-height: 44px; padding: 0 12px; font-size: 12px; }
  .stream-details #dCopy span { display: inline; }
  .dacts:not(:has(> :not(.hidden))) { display: none; }
  .pwa-notice { position: fixed; z-index: 85; right: var(--gr); bottom: max(20px, var(--bot)); width: min(380px, calc(100vw - var(--g) - var(--gr))); padding: 20px; background: #1b2129; border: 0; border-radius: 16px; box-shadow: 0 16px 60px #0009; color: var(--t1); }
  .pwa-notice b, .pwa-notice strong { display: block; padding-right: 22px; font-size: 15px; letter-spacing: -.02em; }
  .pwa-notice p { margin: 8px 0 0; font-size: 13px; line-height: 1.6; color: var(--t2); }
  .pwa-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 16px; }
  .pwa-action { min-height: 40px; padding: 0 14px; border-radius: 9px; background: var(--acc); color: #fff; font-size: 13px; font-weight: 650; }
  .pwa-close { position: absolute; top: 8px; right: 8px; width: 36px; height: 36px; display: grid; place-items: center; color: var(--t2); border-radius: 9px; }
  .pwa-close svg { width: 18px; height: 18px; }
  .pwa-close:hover { background: #ffffff0c; }
  .pwa-notice.compact:not([hidden]) { display: flex; align-items: center; gap: 12px; padding: 8px 8px 8px 16px; border-radius: 12px; box-shadow: 0 6px 24px #0006; }
  .pwa-notice.compact .pwa-copy { flex: 1; min-width: 0; }
  .pwa-notice.compact strong { padding: 0; font-size: 13px; line-height: 18px; font-weight: 550; }
  .pwa-notice.compact .pwa-actions { flex: none; flex-wrap: nowrap; gap: 4px; margin: 0; }
  .pwa-notice.compact .pwa-action { min-height: 44px; padding: 0 12px; background: transparent; color: var(--acc-tx); }
  .pwa-notice.compact .pwa-close { position: static; width: 44px; height: 44px; }
  @media (hover: hover) {
    .catalog-card:hover:not(:active) { transform: none; }
    .catalog-card:hover .poster-frame img { transform: scale(1.035); }
    .catalog-card:hover .catalog-caption b { color: #fff; }
    .res:hover .card { transform: translateY(-3px); }
    .ver:hover::after { opacity: .65; }
  }
  @media (min-width: 760px) {
    :root { --gap: 16px; --nav-h: 76px; }
    #nav::before { background: var(--bg); height: 100%; }
    #nav::after { height: 100%; border-bottom: 0; -webkit-mask-image: none; mask-image: none; }
    .hero { margin: calc(var(--nav-h) + var(--top) + 16px) var(--gr) 32px var(--g); height: clamp(450px, 52vw, 570px); border-radius: 22px; }
    .hero .scrim { background: linear-gradient(90deg, rgba(7,9,12,.9), rgba(7,9,12,.65) 33%, rgba(7,9,12,.08) 76%), linear-gradient(0deg, rgba(7,9,12,.55), transparent 60%); }
    .hero .bg { object-position: 65% 30%; }
    .hero .info { left: 34px; right: auto; width: min(52%, 500px); bottom: 38px; }
    .hero + .rows { margin-top: 0; }
    .hero .tslot { height: 110px; margin-bottom: 18px; }
    .hero .logo { max-width: 100%; max-height: 110px; }
    .hero .bigname { font-size: clamp(36px, 4.4vw, 58px); line-height: 1.04; letter-spacing: -.04em; -webkit-line-clamp: 2; }
    .hero .meta { font-size: 12px; }
    .hero .desc { font-size: 14px; }
    .hero .acts { margin-top: 24px; }
    .rows { padding-bottom: 60px; }
    .row { margin-top: 32px; }
    .card { width: calc((100vw - var(--g) - var(--gap) * 4) / 4.7); }
    .catalog-caption b { font-size: 13px; }
    .catalog-caption span { font-size: 12px; }
    .grid { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 30px 18px; }
    .page { padding-bottom: 48px; }
    .backdrop { height: 360px; }
    .backdrop .scrim { background: linear-gradient(0deg, var(--bg), rgba(11,13,16,0) 75%), linear-gradient(90deg, rgba(11,13,16,.95), rgba(11,13,16,.25) 80%); }
    .tbody { margin-top: -160px; }
    .thead { max-width: 640px; min-height: 0; }
    .tl { height: 104px; }
    .tlogo { max-height: 92px; max-width: 400px; }
    .tname { font-size: 40px; line-height: 1.1; }
    .tdesc { font-size: 15px; }
    .sect { margin-top: 40px; }
    .vgroup { max-width: 1120px; }
    .vtoolbar, .vhint { max-width: 1120px; }
    #v-player.native-player .pframe { max-height: 70svh; aspect-ratio: 16 / 9; }
    #v-player.native-player .pdock > * { max-width: 1100px; margin-left: auto; margin-right: auto; }
  }
  @media (min-width: 1100px) {
    :root { --g: max(48px, env(safe-area-inset-left)); --gr: max(48px, env(safe-area-inset-right)); }
    .hero { height: clamp(470px, 42vw, 590px); }
    .hero .info { left: 44px; bottom: 42px; width: 480px; }
    .hero .tslot { height: 125px; }
    .hero .logo { max-height: 125px; max-width: 420px; }
    .hero .desc { font-size: 15px; }
    .card { width: calc((100vw - var(--g) - var(--gap) * 6) / 6.4); }
    .cw { width: calc((100vw - var(--g) - var(--gap) * 3) / 3.5); }
    .row h2 { font-size: 23px; }
    .backdrop { height: 380px; }
    .tbody { margin-top: -180px; }
    .tl { height: 112px; }
    .tlogo { max-height: 100px; }
  }
  @media (min-width: 1800px) { .hero { max-width: 1900px; margin-left: auto; margin-right: auto; } .card { width: calc((100vw - var(--g) - var(--gap) * 8) / 8.4); } }
  @media (max-width: 759px), (pointer: coarse) and (max-height: 520px) {
    :root { --tab-h: 62px; --tab-b: max(12px, var(--bot)); --chrome-b: calc(var(--tab-h) + var(--tab-b) + 24px); }
    .hero { height: max(570px, min(78svh, 690px)); }
    .hero .info { bottom: 24px; }
    .hero .lt-over { top: calc(var(--top) + 16px); font-size: 25px; line-height: 34px; letter-spacing: -.04em; }
    .lt-over .mark { width: 29px; height: 29px; border-radius: 8px; }
    .hero .tslot { height: 92px; margin-bottom: 14px; }
    .hero .logo { max-height: 92px; max-width: 90%; }
    .hero .desc { -webkit-line-clamp: 2; font-size: 13px; line-height: 20px; }
    .hero .meta { font-size: 11px; }
    .hero .acts { margin-top: 20px; }
    .hero .acts .hw { flex: 1.35; }
    .btn { padding-inline: 15px; height: 46px; font-size: 13px; }
    .row { margin-top: 28px; contain-intrinsic-size: auto 320px; }
    .row h2 { font-size: 19px; line-height: 26px; margin-bottom: 8px; }
    .catalog-caption { padding-top: 9px; }
    .catalog-caption b { font-size: 12px; }
    .catalog-caption span { font-size: 11px; }
    #tabbar { gap: 0; padding: 4px; border-radius: 20px; background: #1a1f27f5; box-shadow: 0 8px 28px #0006; }
    .tb-cap { flex: 4; border-radius: 16px; padding: 0; background: transparent; box-shadow: none; }
    .tb { border-radius: 16px; color: var(--t3); gap: 3px; font-size: 9px; font-weight: 550; }
    .tb svg { width: 23px; height: 23px; }
    .tb-pill { top: 0; bottom: 0; left: 0; width: 25%; border-radius: 15px; background: #8054f530; }
    .tb-search { flex: 1; flex-direction: column; width: auto; height: 100%; gap: 3px; border-radius: 16px; background: transparent; box-shadow: none; color: var(--t3); font-size: 9px; font-weight: 550; line-height: 12px; }
    .tb-search-label { display: block; }
    .tb-search kbd { display: none; }
    .tb-search svg { width: 22px; height: 22px; }
    .tb-search.on::before { inset: 0; border-radius: 15px; background: var(--acc-soft); }
    .tb-mini { border-radius: 20px; background: #1a1f27f5; }
    #tabbar.min { background: transparent; box-shadow: none; }
    #tabbar.min .tb-search { background: #1a1f27f5; }
    .page-heading { padding: calc(var(--top) + 28px) var(--gr) 24px var(--g); align-items: flex-start; gap: 12px; }
    .page-heading .lt-page { font-size: 34px; }
    .install-action { width: 42px; height: 42px; padding: 0; flex: none; margin-top: 0; }
    .install-action span { display: none; }
    .field input { height: 50px; }
    .sbar { padding-bottom: 14px; }
    .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px 14px; }
    .res .nm2 { font-size: 13px; }
    .backdrop { height: clamp(240px, 36svh, 300px); }
    .tbody { margin-top: -98px; }
    .thead { min-height: 0; }
    .tl { height: 88px; }
    .tlogo { max-height: 76px; margin-bottom: 12px; }
    .tname { font-size: 28px; line-height: 1.12; }
    .tdesc { font-size: 14px; }
    .credits { font-size: 12px; }
    .sect { margin-top: 34px; font-size: 22px; }
    .vtoolbar { gap: 8px; }
    .vsummary { font-size: 11px; }
    .vsort { font-size: 11px; gap: 5px; }
    .vsort select { max-width: 150px; font-size: 11px; }
    .vhint { font-size: 11px; }
    .vplay { padding: 15px 7px 15px 0; gap: 7px; min-height: 106px; }
    .ver, .tbody .ver { padding-right: 5px; }
    .vk { font-size: 14px; gap: 6px; }
    .vs { font-size: 12px; }
    .vf { font-size: 10px; }
    .go { width: 30px; height: 32px; border-radius: 8px; }
    .go svg { width: 12px; height: 12px; }
    .ver .dl { width: 32px; }
    .ver .dl svg { width: 15px; height: 15px; }
    .vbadge { font-size: 10px; padding: 0; }
    .pip-return { bottom: calc(var(--chrome-b) + 2px); }
    .pwa-notice { bottom: calc(var(--chrome-b) + 2px); }
    .native-actions { gap: 7px; margin-bottom: 16px; }
    .native-actions .db { min-height: 42px; padding: 0 10px; font-size: 11px; }
    .native-actions #pNativeBack { margin-right: 0; }
    .native-actions svg { width: 16px; height: 16px; }
  }
  @media (max-width: 359px) { .hero .acts { gap: 8px; } .hero .acts .btn { padding-inline: 10px; font-size: 12px; } .vplay .go { display: none; } .vtoolbar { align-items: flex-start; } }
  @media (pointer: coarse) and (max-height: 520px) {
    .hero { margin: 0; border-radius: 0; height: max(380px, 90svh); }
    .hero .info { width: min(500px, 68vw); bottom: 20px; }
    .hero .tslot { height: 72px; } .hero .logo { max-height: 72px; }
    .hero .lt-over { font-size: 22px; }
    .hero .acts { margin-top: 14px; } .hero .acts .btn { flex: none; }
    .tb { flex-direction: row; font-size: 11px; }
  }

</style>
</head>
<body>
<a class="skip-link" href="#mainContent">Skip to content</a>
<header id="nav">
  <a class="ibtn glass hidden" id="tBack" href="#/" aria-label="Back"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></a>
  <a class="brand" href="#/" data-tab="home" aria-label="Frame: home"><span class="mark"><svg viewBox="0 0 11 13"><path d="M0 1.2v10.6a1 1 0 0 0 1.5.86l8.9-5.3a1 1 0 0 0 0-1.72L1.5.34A1 1 0 0 0 0 1.2z"/></svg></span><span class="word">Frame</span></a>
  <div id="navTitle" aria-hidden="true"></div>
</header>
<nav id="tabbar" aria-label="Sections">
  <div class="tb-cap"><i class="tb-pill" aria-hidden="true"></i>
    <a class="tb" href="#/" data-tab="home"><svg class="o" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4v-5.5a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1V20H5a1 1 0 0 1-1-1z"/></svg><svg class="f" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4v-5.5a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1V20H5a1 1 0 0 1-1-1z"/></svg><span>Home</span></a>
    <a class="tb" href="#/movies" data-tab="movies"><svg class="o" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="9" width="16" height="11" rx="2"/><path d="M4 9V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3M9.5 4 7 9M15 4l-2.5 5"/></svg><svg class="f" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M6 3h12a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3zM3 8.3h18v1.4H3zM9.25 3h1.5l-2.65 5.3H6.6zM14.75 3h1.5l-2.65 5.3h-1.5z"/></svg><span>Movies</span></a>
    <a class="tb" href="#/series" data-tab="series"><svg class="o" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="12.5" rx="2.5"/><path d="M8 20.5h8"/></svg><svg class="f" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5.5 3.5h13A3.5 3.5 0 0 1 22 7v7.5a3.5 3.5 0 0 1-3.5 3.5h-13A3.5 3.5 0 0 1 2 14.5V7a3.5 3.5 0 0 1 3.5-3.5zM8 19.5h8a1 1 0 0 1 0 2H8a1 1 0 0 1 0-2z"/></svg><span>Series</span></a>
    <a class="tb" href="#/library" data-tab="library"><svg class="o" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="9" width="16" height="11" rx="2.5"/><path d="M6.5 5.5h11M9 2.5h6"/></svg><svg class="f" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6.5 8h11a3.5 3.5 0 0 1 3.5 3.5v6a3.5 3.5 0 0 1-3.5 3.5h-11A3.5 3.5 0 0 1 3 17.5v-6A3.5 3.5 0 0 1 6.5 8zM6.5 4.5h11a1 1 0 0 1 0 2h-11a1 1 0 0 1 0-2zM9 1.5h6a1 1 0 0 1 0 2H9a1 1 0 0 1 0-2z"/></svg><span>Library</span></a>
  </div>
  <button class="tb-mini" id="tbMini" aria-label="Show the tabs" tabindex="-1"></button>
  <a class="tb-search" id="tbSearch" href="#/search" data-tab="search" aria-label="Search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4.05-4.05"/></svg><span class="tb-search-label">Search</span><kbd>/</kbd></a>
</nav>

<main id="mainContent" tabindex="-1">
  <section id="v-home" class="view">
    <i class="sent" aria-hidden="true"></i>
    <div class="hero out">
      <img class="bg sm" alt=""><img class="bg lg" alt="" fetchpriority="high"><div class="scrim"></div>
      <h1 class="ltitle lt-over"><span class="mark"><svg viewBox="0 0 11 13"><path d="M0 1.2v10.6a1 1 0 0 0 1.5.86l8.9-5.3a1 1 0 0 0 0-1.72L1.5.34A1 1 0 0 0 0 1.2z"/></svg></span>Frame</h1>
      <div class="info">
        <div class="tslot"><img class="logo" alt=""><h2 class="bigname"></h2></div>
        <div class="meta"></div>
        <p class="desc"></p>
        <div class="acts"><a class="btn hw" href="#/"></a><a class="btn sec hm" href="#/"></a></div>
      </div>
    </div>
    <div class="rows"></div>
  </section>

  <section id="v-library" class="view page hidden">
    <i class="sent" aria-hidden="true"></i>
    <div class="page-heading">
      <div><h1 class="ltitle lt-page">Library</h1></div>
      <button class="btn sec install-action" id="pwaInstall" hidden aria-label="Install Frame"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4"/></svg><span>Install app</span></button>
    </div>
    <div id="libList"></div>
  </section>

  <section id="v-search" class="view page hidden">
    <i class="sent" aria-hidden="true"></i>
    <div class="page-heading"><div><h1 class="ltitle lt-page">Search</h1></div></div>
    <i class="pin" aria-hidden="true"></i>
    <div class="sbar"><div class="field">
      <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
      <input id="q" type="search" placeholder="Movies and series" aria-label="Search movies and series" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="search">
      <button class="ibtn clr hidden" id="qClear" aria-label="Clear"></button>
    </div><button class="scancel" id="qCancel">Cancel</button></div>
    <div class="note" id="sNote" role="status" aria-live="polite"></div>
    <div class="grid" id="sGrid"></div>
  </section>

  <section id="v-title" class="view hidden">
    <i class="sent" aria-hidden="true"></i>
    <div class="backdrop"><img class="bg sm" id="tBgS" alt=""><img class="bg lg" id="tBg" alt="" fetchpriority="high"><div class="scrim"></div></div>
    <div class="tbody">
      <div class="thead">
        <div class="tl" id="tSlot"><i class="sent" aria-hidden="true"></i><img class="tlogo" id="tLogo" alt=""><h1 class="tname" id="tName"></h1></div>
        <div class="meta" id="tMeta"></div>
        <div class="acts" id="tActs"></div>
      </div>
      <details class="title-about" id="tAbout" hidden>
        <summary>About</summary>
        <p class="tdesc" id="tDesc"></p>
        <div class="credits" id="tCredits"></div>
      </details>
      <div id="tSeries" class="hidden"><h2 class="sect">Episodes</h2><div class="seasons" id="tSeasons"></div><div class="eps" id="tEps"></div></div>
      <div id="tVersions" class="hidden"><h2 class="sect">Streams <span class="n" id="tCount"></span></h2><div id="tList"></div></div>
    </div>
  </section>
</main>

<section id="v-player" class="hidden m-inline" tabindex="-1" aria-label="Player">
  <div class="pframe" id="pFrame">
    <video id="vid" playsinline preload="auto" x-webkit-airplay="allow"></video>
    <img class="pcover" id="pCover" alt="">
    <canvas class="psnap hidden" id="pSnap"></canvas>
    <div class="pdim" id="pDim"></div>
    <div class="pgest" id="pGest"></div>
    <div class="prip l" id="pRipL" aria-hidden="true"><div class="rc"><span class="arr"></span><span class="n"></span></div></div>
    <div class="prip r" id="pRipR" aria-hidden="true"><div class="rc"><span class="arr"></span><span class="n"></span></div></div>
    <div class="pwhere" id="pWhere"></div>
    <div class="pctl" id="pCtl">
      <div class="ptop">
        <button class="gb" id="pBack" aria-label="Back"></button>
        <div class="pttl"><b id="pTitle"></b><span id="pSub"></span></div>
        <div class="pgrp">
          <button class="gb hidden" id="pAir" aria-label="AirPlay"></button>
          <button class="gb hidden" id="pPip" aria-label="Picture in Picture"></button>
        </div>
      </div>
      <div class="pmid">
        <button class="gb l" id="pB10" aria-label="Back 10 seconds"></button>
        <button class="gb xl" id="pPlay" aria-label="Pause"></button>
        <button class="gb l" id="pF10" aria-label="Forward 10 seconds"></button>
      </div>
      <div class="pend" aria-hidden="true">Watch again</div>
      <div class="pbot">
        <div class="prow">
          <div class="vol hidden" id="pVolBox"><button class="gb bare" id="pMute" aria-label="Mute"></button>
            <div class="vsl" id="pVol" role="slider" tabindex="0" aria-label="Volume" aria-valuemin="0" aria-valuemax="100" aria-valuenow="100"><div class="trk"><i id="pVolFill"></i></div></div></div>
          <span class="sp"></span>
          <button class="gp hidden" id="pAudio" aria-haspopup="menu" aria-label="Audio"><span class="nm" id="pAudioName"></span></button>
          <button class="gp acc hidden" id="pNextBtn"><span>Next episode</span></button>
          <button class="gb" id="pMore" aria-haspopup="menu" aria-label="More"></button>
          <button class="gb" id="pFs" aria-label="Full screen"></button>
        </div>
        <div class="pseek">
          <span class="ptime" id="pCur">0:00</span>
          <div class="scrub" id="pScrub" role="slider" tabindex="0" aria-label="Position" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0">
            <div class="trk" id="pTrk"><i class="fill" id="pFill"></i></div>
            <div class="kpos" id="pKpos"><i class="knob"></i></div>
            <div class="bpos" id="pBpos"><span class="bub" id="pBub"></span></div>
          </div>
          <span class="ptime r" id="pLeft"></span>
          <button class="gb bare fsb" id="pFs2" aria-label="Full screen"></button>
        </div>
      </div>
    </div>
    <div class="pbusy" id="pBusy" role="status" aria-live="polite"><span class="aspin"></span><span class="cap" id="pBusyCap"></span></div>
    <div class="phud" id="pHud" aria-hidden="true"></div>
    <button class="gp presume hidden" id="pRestart"><span>Start from the beginning</span></button>
    <div class="perr hidden" id="pErr" role="alert"><b id="pErrT"></b><p id="pErrP"></p>
      <div class="acts"><button class="pri" id="pRetry">Try again</button><button id="pOther">Streams</button></div></div>
    <div class="pupn hidden" id="pUpn"><img id="pUpnImg" alt=""><div class="t"><small>Next episode</small><b id="pUpnT"></b>
      <button class="upgo" id="pUpnGo"><span>Play</span></button></div><button class="x" id="pUpnX" aria-label="Dismiss"></button></div>
    <div class="pmenu" id="pMenu" role="menu"></div>
  </div>
  <div class="pdock" id="pDock">
    <div class="native-actions hidden" id="pNativeActions" aria-label="Playback options">
      <button class="db" id="pNativeBack" type="button">Back</button>
      <button class="db acc hidden" id="pNativePlay" type="button">Play</button>
      <button class="db" id="pNativeFullscreen" type="button">Full screen</button>
      <button class="db hidden" id="pNativePip" type="button">Picture in Picture</button>
      <button class="db hidden" id="pNativeAirplay" type="button">AirPlay</button>
      <p class="native-status" id="pNativeStatus" role="status" aria-live="polite"></p>
    </div>
    <h1 id="pEpName"></h1><div class="sub" id="pEpSub"></div>
    <div class="dacts">
      <button class="db acc hidden" id="dNext"><span>Next episode</span></button>
      <div class="db hidden" id="dAudio"><span class="nm" id="dAudioName"></span><select id="pLang" aria-label="Audio"></select></div>
    </div>
    <div class="pver" id="pVerBox">
      <div class="hd"><div id="pVer"></div><button class="chg" id="pChange">Streams</button></div>
      <details class="stream-details" id="pStreamDetails">
        <summary>Stream details</summary>
        <div id="pFileDetails"></div>
        <button class="db" id="dCopy" aria-label="Copy stream link"><span>Copy link</span></button>
      </details>
    </div>
    <div class="upnext hidden" id="pNext"><div class="lbl">Up next</div><div id="pNextEp"></div></div>
  </div>
</section>
<button class="pip-return hidden" id="pPipReturn" type="button" aria-label="Return to playing video">Return to video</button>
<div class="sheet" id="sheet" aria-hidden="true">
  <div class="dim" id="shDim"></div>
  <div class="panel" id="shPanel" role="dialog" aria-modal="true" aria-labelledby="shTitle">
    <div class="shead" id="shHead"><div class="grab"></div>
      <div class="shrow"><div class="shthumb"><img id="shThumb" alt=""></div><div class="shttl"><span class="sm" id="shSeries"></span><b id="shTitle"></b></div>
        <button class="ibtn round" id="shClose" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M7 7l10 10M17 7L7 17"/></svg></button></div>
    </div>
    <div class="sscroll" id="shScroll"><div id="shList"></div></div>
  </div>
</div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script>
"use strict";
const byId = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const two = (n) => String(n).padStart(2, "0");
function fmtTime(s) { s = Math.floor(s || 0); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60; return (h ? h + ":" + two(m) : m) + ":" + two(s % 60); }
function leftOf(t, d) {  // (what's left to watch: "32 min left", "1 h 12 min left")
  const m = Math.max(1, Math.round((d - t) / 60)), h = Math.floor(m / 60);
  return (h ? h + " h" + (m % 60 ? " " + (m % 60) + " min" : "") : m + " min") + " left";
}
const store = {
  get(k, d) { try { const v = localStorage.getItem("fb." + k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("fb." + k, JSON.stringify(v)); } catch (e) {} },
};
const hoverable = matchMedia("(hover: hover)").matches;
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
const wideMQ = matchMedia("(min-width: 760px) and (min-height: 521px)");  // (a tablet's modal, a desktop's drawer: a phone on its side keeps the sheet)
const wide = () => wideMQ.matches;
const compactMQ = matchMedia("(max-width: 759px), (pointer: coarse) and (max-height: 520px)");  // (a phone: the tab bar below, large titles)
const slideMQ = matchMedia("(pointer: coarse)");  // (a touch screen: pages slide in and out; with a mouse they fade)
const smooth = () => (reduced.matches ? "auto" : "smooth");
const nextFrame = (f) => requestAnimationFrame(() => requestAnimationFrame(f));
function onMedia(mq, f) { if (mq.addEventListener) mq.addEventListener("change", f); else mq.addListener(f); }
function anim(el, frames, ms) {  // (a one-off entrance, transform and opacity only, that leaves nothing behind)
  if (!reduced.matches && el.animate) el.animate(frames, {duration: ms, easing: "cubic-bezier(.22,1,.36,1)", fill: "backwards"});
}
const RISE = [{opacity: 0, transform: "translate3d(0,6px,0)"}, {opacity: 1, transform: "none"}];
let lastInput = 0;  // (when the page was last scrolled or touched: heavy work waits for a quiet moment)
document.addEventListener("touchstart", () => { lastInput = performance.now(); }, {passive: true});  // (iOS Safari applies :active on touch
document.addEventListener("wheel", () => { lastInput = performance.now(); }, {passive: true});
function whenQuiet(f, after, quiet) {  // (f once nobody has scrolled or touched for quiet ms (0.8 s), in idle time where the browser
  const need = quiet || 800;
  const go = () => {
    const q = performance.now() - lastInput;
    if (q < need) { setTimeout(go, need + 50 - q); return; }
    if (!window.requestIdleCallback) { f(); return; }
    requestIdleCallback(() => { if (performance.now() - lastInput < need) go(); else f(); }, {timeout: 1000});
  };
  setTimeout(go, after || 0);
}
const plainErr = (e) => (!navigator.onLine || /fetch|network|load failed/i.test(String((e && e.message) || "")) ? "Check the connection." :
  "The service didn't answer. Try again in a moment.");  // (an error said in plain words: no "Failed to fetch", no fleet internals)
document.addEventListener("animationend", (e) => {  // (a finished entrance leaves nothing behind)
  const c = e.target.classList;
  if (c && c.contains("enter")) c.remove("enter");
});
let shield = null;  // (a layer just shown: a tap on it within 350 ms is the second half of the double tap that showed it, not a choice)
function shieldOn(el) { shield = {el: el, until: performance.now() + 350}; }
document.addEventListener("click", (e) => {
  if (shield && performance.now() < shield.until && shield.el.contains(e.target)) { e.preventDefault(); e.stopImmediatePropagation(); }
}, true);

const sv = (w) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + (w || 2.2) + '" stroke-linecap="round" stroke-linejoin="round">';
const pict = (inner) => sv(1.9).replace("<svg ", '<svg class="pict" ') + inner + "</svg>";
const ICON = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>',
  info: sv() + '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.6v.01"/></svg>',
  left: sv(2.4) + '<path d="M15 5l-7 7 7 7"/></svg>',
  right: sv(2.4) + '<path d="M9 5l7 7-7 7"/></svg>',
  down: sv(2.4) + '<path d="M6 9l6 6 6-6"/></svg>',
  dl: sv(2.2) + '<path d="M12 4v11M7.5 10.5l4.5 4.5 4.5-4.5M5 19.5h14"/></svg>',
  link: sv(2.2) + '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1.2 1.2"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2"/></svg>',
  close: sv(2.6) + '<path d="M7 7l10 10M17 7L7 17"/></svg>',
  check: sv(2.6) + '<path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  alert: sv() + '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.4v.01"/></svg>',
  xcirc: '<svg viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M12 2.5a9.5 9.5 0 1 1 0 19 9.5 9.5 0 0 1 0-19zM8.46 7.05 12 10.59l3.54-3.54a1 1 0 0 1 1.41 1.41L13.41 12l3.54 3.54a1 1 0 0 1-1.41 1.41L12 13.41l-3.54 3.54a1 1 0 0 1-1.41-1.41L10.59 12 7.05 8.46a1 1 0 0 1 1.41-1.41z"/></svg>',
  stack: sv(1.6) + '<rect x="4" y="9" width="16" height="11" rx="2.5"/><path d="M6.5 5.5h11M9 2.5h6"/></svg>',
  search: sv(1.6) + '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4.05-4.05"/></svg>',
  aiv: '<svg class="aiv" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 2.6v4.2"/><path d="M12 2.6v4.2" opacity=".86" transform="rotate(-45 12 12)"/><path d="M12 2.6v4.2" opacity=".72" transform="rotate(-90 12 12)"/><path d="M12 2.6v4.2" opacity=".6" transform="rotate(-135 12 12)"/><path d="M12 2.6v4.2" opacity=".48" transform="rotate(180 12 12)"/><path d="M12 2.6v4.2" opacity=".38" transform="rotate(135 12 12)"/><path d="M12 2.6v4.2" opacity=".3" transform="rotate(90 12 12)"/><path d="M12 2.6v4.2" opacity=".22" transform="rotate(45 12 12)"/></svg>',
  film: pict('<rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M7.5 4v16M16.5 4v16M3.5 8h4M3.5 12h4M3.5 16h4M16.5 8h4M16.5 12h4M16.5 16h4"/>'),
  asis: pict('<circle cx="12" cy="12" r="8.5"/><path d="M10.2 8.8v6.4l5-3.2z" fill="currentColor"/>'),
  raw: pict('<path d="M6.5 3.5h7.5l4.5 4.5v12.5h-12z"/><path d="M14 3.5V8h4.5M9.5 12.5h5M9.5 16h5"/>'),
  globe: pict('<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.9 5.2 3.9 8.5s-1.3 6.1-3.9 8.5M12 3.5C9.4 5.9 8.1 8.7 8.1 12s1.3 6.1 3.9 8.5"/>'),
};
function toast(msg, bad) {
  const t = byId("toast");
  t.classList.toggle("err", !!bad);
  t.innerHTML = (bad ? ICON.alert : ICON.check) + "<span>" + esc(msg) + "</span>";
  t.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.remove("show"), bad ? 4000 : 2200);
}
function cuv(icon, title, text, action) {  // (nothing to show: an icon, a line, a sentence, at times one thing to do, as iOS's
  return '<div class="cuv">' + icon + "<b>" + esc(title) + "</b>" + (text ? "<p>" + esc(text) + "</p>" : "") + (action ? '<button class="btn">' + esc(action) + "</button>" : "") + "</div>";
}

const GW = "/gw/", GW_HOSTS = ["images.metahub.space", "live.metahub.space", "episodes.metahub.space", "image.tmdb.org", "m.media-amazon.com"];
const CAT = GW + "cinemeta-catalogs.strem.io", META = GW + "v3-cinemeta.strem.io", IMG = GW + "images.metahub.space";
const FEED = location.origin + CAT + "/feed.json";  // (whole: the worker that fetches it has no address of its own to go by)
function via(url) {  // (a picture's outside address as this site's own; a metahub one named .jpg/.png: Cloudflare keeps those at its edge)
  const m = /^https:\/\/([a-z0-9.-]+)(\/[^?#]*)/.exec(url || "");
  if (!m || GW_HOSTS.indexOf(m[1]) < 0) return url;
  return GW + m[1] + (m[2].endsWith("/img") ? m[2] + (m[2].indexOf("/logo/") === 0 ? ".png" : ".jpg") : m[2]);
}
const pic = {
  poster: (id) => IMG + "/poster/small/" + id + "/img.jpg",
  bgSmall: (id) => IMG + "/background/small/" + id + "/img.jpg",
  bg: (id) => IMG + "/background/medium/" + id + "/img.jpg",
  logo: (id) => IMG + "/logo/medium/" + id + "/img.png",
};
const memo = new Map();
function getJSON(url, keepMs) {  // (once a while each: the browser's own cache keeps them across visits, as their servers say)
  const hit = memo.get(url);
  if (hit && (!keepMs || Date.now() - hit.at < keepMs)) return hit.p;
  const p = fetch(url).then(async (r) => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
    return j;
  });
  const h = {p: p, at: Date.now(), v: undefined};
  memo.set(url, h);
  p.then((j) => { h.v = j; }, () => { if (memo.get(url) === h) memo.delete(url); });
  return p;
}
function have(url, keepMs) {  // (what a fetch of url gave, if it's in and still fresh: shown at once, in the same task)
  const hit = memo.get(url);
  return hit && hit.v !== undefined && (!keepMs || Date.now() - hit.at < keepMs) ? hit.v : null;
}
const known = new Map();  // (what's known of a title already, from a catalog or a search: its page shows that at once)
function remember(m) { if (m && m.id) known.set(m.id, Object.assign({}, known.get(m.id) || {}, m)); return m; }
function catalog(type, id, genre) {
  const url = CAT + "/" + id + "/catalog/" + type + "/" + id + (genre ? "/genre=" + encodeURIComponent(genre) : "") + ".json";
  return getJSON(url).catch(() => new Promise((ok) => setTimeout(ok, 1500)).then(() => getJSON(url)))  // (its CDN's copy being made: once more)
    .then((j) => (j.metas || []).map(remember));
}
function meta(type, id) { return getJSON(META + "/meta/" + type + "/" + id + ".json").then((j) => remember(j.meta || {})); }
const VER_KEEP = 10 * 60 * 1000;
const versionsUrl = (type, sid) => "/browser/stream/" + type + "/" + sid + ".json?as_is=1";
function versions(type, sid) { return getJSON(versionsUrl(type, sid), VER_KEEP); }
function genresOf(m) { const g = m.genres || m.genre || []; return Array.isArray(g) ? g : [g]; }
function interleave(a, b) { const out = []; for (let i = 0; i < Math.max(a.length, b.length); i++) { if (a[i]) out.push(a[i]); if (b[i]) out.push(b[i]); } return out; }

function loadImg(img, src, eager, then) {  // (a large picture; then: once it shows)
  let done = false;
  const show = () => { if (done || img.getAttribute("src") !== src) return; done = true; img.classList.add("in"); if (then) then(); };
  img.classList.add("img");
  img.classList.remove("in");
  img.decoding = "async";
  img.loading = eager ? "eager" : "lazy";
  img.onload = () => { (img.decode ? img.decode() : Promise.resolve()).then(show, show); };
  img.onerror = null;
  img.src = src;
  if (img.complete && img.naturalWidth) show();
  return img;
}
function loadPic(img, src, eager) {  // (a picture in a list)
  img.decoding = "async";
  img.loading = eager ? "eager" : "lazy";
  img.src = src;
  return img;
}
function backdrop(sm, lg, id) {  // (a title's picture: metahub's medium one fading in over its small one blurred to a wash of colour, as
  const want = pic.bg(id);
  sm.classList.remove("hidden", "plain");
  loadImg(sm, pic.bgSmall(id), true);
  loadImg(lg, want, true, () => setTimeout(() => { if (lg.getAttribute("src") === want) sm.classList.add("hidden"); }, 320));
  lg.onerror = () => { if (lg.getAttribute("src") === want) sm.classList.add("plain"); };  // (no medium one: the small one as it is)
}
function titleSlot(slot, id, text) {  // (a title's logo where it has one, else its name, in a slot of its own size: the name shows if
  const img = slot.querySelector("img"), name = slot.querySelector("h1, h2"), want = pic.logo(id);
  const named = () => { clearTimeout(slot.wait); slot.classList.add("nm"); };
  name.textContent = text || "";
  slot.classList.remove("lg", "nm");
  clearTimeout(slot.wait);
  slot.wait = setTimeout(named, 350);
  img.onload = () => {
    if (img.getAttribute("src") !== want) return;
    if (img.naturalWidth < 40) { named(); return; }
    (img.decode ? img.decode() : Promise.resolve()).catch(() => {}).then(() => {
      if (img.getAttribute("src") !== want) return;
      clearTimeout(slot.wait);
      slot.classList.add("lg");
    });
  };
  img.onerror = () => { if (img.getAttribute("src") === want) named(); };
  img.src = want;
  if (img.complete && img.naturalWidth >= 40) { clearTimeout(slot.wait); slot.classList.add("lg"); }  // (at hand already)
}
document.addEventListener("error", (e) => {  // (a list's picture that doesn't exist: a poster gives way to the title's name, an
  const img = e.target, box = img && img.parentNode;
  if (!img || img.tagName !== "IMG" || !box || !box.classList) return;
  if (box.classList.contains("card") || box.classList.contains("poster-frame")) { img.remove(); if (!box.querySelector(".nm")) box.insertAdjacentHTML("beforeend", '<span class="nm">' + esc(box.dataset.name) + "</span>"); }
  else if (box.classList.contains("th") && img.closest(".ep")) img.remove();
}, true);
const warmed = new Set();
let warmT = null;
function warmLater(e, ms) {
  clearTimeout(warmT);
  const a = e.target.closest && e.target.closest('a[href^="#/title/"]');
  warmT = a ? setTimeout(() => {
    const m = /^#\/title\/(movie|series)\/(tt\d+)/.exec(a.getAttribute("href") || "");
    if (!m || warmed.has(m[2])) return;
    warmed.add(m[2]);
    meta(m[1], m[2]).catch(() => {});
    new Image().src = pic.bgSmall(m[2]);
    new Image().src = pic.bg(m[2]);
  }, ms) : null;
}
document.addEventListener("pointerdown", (e) => warmLater(e, 80), {passive: true});
document.addEventListener("pointercancel", () => clearTimeout(warmT), {passive: true});
if (hoverable) document.addEventListener("pointerover", (e) => { if (e.pointerType === "mouse") warmLater(e, 150); }, {passive: true});

const yearOf = (m) => m.releaseInfo || m.year || "";
function runtime(m) {  // ("2 h 47 min" for a film; a series' "58 min" as it is)
  const r = String(m.runtime || ""), n = parseInt(r, 10);
  if (!n || m.type === "series" || !/^\d+\s*min/.test(r)) return r;
  return ((n >= 60 ? Math.floor(n / 60) + " h " : "") + (n % 60 ? (n % 60) + " min" : "")).trim();
}
function metaLine(m) {
  const b = [], rt = runtime(m), g = genresOf(m).slice(0, 2);
  if (yearOf(m)) b.push("<span>" + esc(yearOf(m)) + "</span>");
  if (rt) b.push("<span>" + esc(rt) + "</span>");
  if (m.imdbRating && Number(m.imdbRating) > 0) b.push('<span class="r">★ ' + esc(m.imdbRating) + "</span>");
  if (g.length) b.push("<span>" + esc(g.join(", ")) + "</span>");
  return b.join("");
}

const ROWS = {
  home: [["Popular movies", "movie", "top"], ["Popular series", "series", "top"], ["Top rated movies", "movie", "imdbRating"],
         ["Top rated series", "series", "imdbRating"], ["Action", "movie", "top", "Action"], ["Science fiction", "movie", "top", "Sci-Fi"],
         ["Documentary series", "series", "top", "Documentary"], ["Comedy", "movie", "top", "Comedy"], ["Thrillers", "movie", "top", "Thriller"],
         ["Animation", "movie", "top", "Animation"], ["Crime series", "series", "top", "Crime"], ["Drama", "movie", "top", "Drama"],
         ["Family", "movie", "top", "Family"], ["Horror", "movie", "top", "Horror"], ["Documentaries", "movie", "top", "Documentary"]],
  movies: [["Popular movies", "movie", "top"], ["Top rated movies", "movie", "imdbRating"], ["Action", "movie", "top", "Action"],
           ["Science fiction", "movie", "top", "Sci-Fi"], ["Adventure", "movie", "top", "Adventure"], ["Comedy", "movie", "top", "Comedy"],
           ["Thrillers", "movie", "top", "Thriller"], ["Drama", "movie", "top", "Drama"], ["Animation", "movie", "top", "Animation"],
           ["Crime", "movie", "top", "Crime"], ["Fantasy", "movie", "top", "Fantasy"], ["Romance", "movie", "top", "Romance"],
           ["Horror", "movie", "top", "Horror"], ["Family", "movie", "top", "Family"], ["Documentaries", "movie", "top", "Documentary"],
           ["War", "movie", "top", "War"], ["History", "movie", "top", "History"]],
  series: [["Popular series", "series", "top"], ["Top rated series", "series", "imdbRating"], ["Drama", "series", "top", "Drama"],
           ["Crime", "series", "top", "Crime"], ["Comedy", "series", "top", "Comedy"], ["Science fiction", "series", "top", "Sci-Fi"],
           ["Documentary", "series", "top", "Documentary"], ["Animation", "series", "top", "Animation"], ["Mystery", "series", "top", "Mystery"],
           ["Action", "series", "top", "Action"], ["Fantasy", "series", "top", "Fantasy"], ["Thrillers", "series", "top", "Thriller"],
           ["Reality", "series", "top", "Reality-TV"], ["Family", "series", "top", "Family"]],
};
const HOMES = {home: byId("v-home")};
["movies", "series"].reduce((after, t) => {  // (movies and series: copies of home's page, with their own large titles)
  const s = HOMES.home.cloneNode(true);
  s.id = "v-" + t;
  s.classList.add("hidden");
  s.querySelector(".lt-over").textContent = t === "movies" ? "Movies" : "Series";
  s.querySelector(".bg.lg").removeAttribute("fetchpriority");
  after.after(s);
  HOMES[t] = s;
  return s;
}, HOMES.home);
const near = "IntersectionObserver" in window ? new IntersectionObserver((es) => es.forEach((e) => {
  if (e.isIntersecting) { near.unobserve(e.target); e.target.load(); }
}), {rootMargin: "900px 0px"}) : null;
let cwRow = null, cwSig = null;
const WIN = () => (matchMedia("(min-width: 760px)").matches ? 16 : 12);
function strip(row) {  // (a row's scroller, its two ends marked; arrows where there's a mouse, each only where there's somewhere to go)
  row.insertAdjacentHTML("beforeend", '<div class="strip nol"><button class="arrow l" tabindex="-1" aria-hidden="true">' + ICON.left + '</button><div class="sc"><i class="head"></i><i class="tail"></i></div>' +
    '<button class="arrow r" tabindex="-1" aria-hidden="true">' + ICON.right + "</button></div>");  // (the arrows are the mouse's: the posters
  const st = row.lastElementChild, sc = st.querySelector(".sc");
  if (hoverable && window.IntersectionObserver) {
    st.querySelector(".arrow.l").onclick = () => sc.scrollBy({left: -sc.clientWidth * 0.85, behavior: smooth()});
    st.querySelector(".arrow.r").onclick = () => sc.scrollBy({left: sc.clientWidth * 0.85, behavior: smooth()});
    const ends = new IntersectionObserver((es) => es.forEach((e) => st.classList.toggle(e.target.className === "head" ? "nol" : "nor", e.isIntersecting)), {root: sc});
    ends.observe(sc.firstElementChild);
    ends.observe(sc.lastElementChild);
  }
  return sc;
}
function fillRow(sc, list, make) {  // (a row's first cards, the next ones as its end comes near: never the whole catalog at once)
  const tail = sc.lastElementChild;
  let n = 0, io = null;
  const more = () => {
    const f = document.createDocumentFragment(), end = Math.min(list.length, n + WIN());
    for (; n < end; n++) f.appendChild(make(list[n], n));
    sc.insertBefore(f, tail);
    if (n >= list.length && io) { io.disconnect(); io = null; }
  };
  more();
  if (n >= list.length) return;
  if (!window.IntersectionObserver) { while (n < list.length) more(); return; }
  io = new IntersectionObserver((es) => {
    if (!es[es.length - 1].isIntersecting) return;
    more();
    if (io) { io.unobserve(tail); io.observe(tail); }  // (still near after these: asked again)
  }, {root: sc, rootMargin: "0px 600px 0px 0px"});
  io.observe(tail);
}
function posterImg(box, m, eager) {  // (a poster, else the title's name where it has none: see the error listener above)
  const img = loadPic(new Image(), via(m.poster) || pic.poster(m.id), eager);
  img.alt = "";
  box.dataset.name = m.name || "";
  box.appendChild(img);
}
function posterCard(m, i, eager) {  // (eager: the first screen's; the rest load as they come near)
  const a = document.createElement("a");
  a.className = "card catalog-card";
  a.href = "#/title/" + m.type + "/" + m.id;
  a.setAttribute("aria-label", m.name || "Open title");
  const frame = document.createElement("div");
  frame.className = "poster-frame";
  posterImg(frame, m, eager === true && i < 4);
  a.appendChild(frame);
  const year = String(yearOf(m)).slice(0, 4), rating = Number(m.imdbRating);
  a.insertAdjacentHTML("beforeend", '<div class="catalog-caption"><b>' + esc(m.name || "Untitled") + '</b><span>' +
    esc([year, rating > 0 ? "★ " + rating.toFixed(1) : ""].filter(Boolean).join(" · ")) + '</span></div>');
  return a;
}
function catalogRow(title, type, id, genre, now) {  // (still loading: flat blocks; then its first posters, all at once)
  const row = document.createElement("section");
  row.className = "row skrow";
  row.innerHTML = '<h2><i aria-hidden="true"></i></h2>';
  const sc = strip(row);
  sc.firstElementChild.insertAdjacentHTML("afterend", '<div class="card"></div>'.repeat(8));
  row.load = () => catalog(type, id, genre).then((list) => {
    if (!list.length) { row.remove(); return; }
    if (row.tab) HOMES[row.tab].ok = true;
    row.classList.remove("skrow");
    row.querySelector("h2").textContent = title;
    sc.replaceChildren(sc.firstElementChild, sc.lastElementChild);
    fillRow(sc, list, (m, i) => posterCard(m, i, now));
  }).catch(() => { row.remove(); if (row.tab) rowFailed(row.tab); });
  if (now || !near) afterFrame(row.load); else near.observe(row);
  return row;
}
const afterFrame = (f) => requestAnimationFrame(() => setTimeout(f));  // (work that waits for the frame showing what was tapped)
function renderHome(tab) {  // (a browse tab: shown at once, its hero and its first rows' places; the rest made after that frame. Built
  const sec = HOMES[tab];
  if (!sec.built) {
    sec.built = true;
    const rows = sec.querySelector(".rows"), list = ROWS[tab];
    if (tab === "home") { cwRow = document.createElement("section"); cwRow.className = "row cwrow hidden"; rows.appendChild(cwRow); }
    list.slice(0, 2).forEach((r) => { const row = rows.appendChild(catalogRow(r[0], r[1], r[2], r[3], true)); row.tab = tab; });
    afterFrame(() => { const f = document.createDocumentFragment(); list.slice(2).forEach((r) => f.appendChild(catalogRow(r[0], r[1], r[2], r[3], false))); rows.appendChild(f); });
    hero(tab);
  }
  if (tab === "home") renderContinue();
}
function rowFailed(tab) {  // (both of a tab's first rows failed and nothing came: the tab says so, with Try again)
  const sec = HOMES[tab];
  sec.fails = (sec.fails || 0) + 1;
  if (sec.ok || sec.fails < 2 || sec.failed) return;
  sec.failed = true;
  sec.classList.toggle("nohero", !sec.querySelector(".hero").dataset.id);
  const rows = sec.querySelector(".rows");
  rows.innerHTML = cuv(ICON.alert, "Couldn't load", "Check the connection.", "Try again");
  rows.querySelector(".cuv .btn").onclick = () => retryTab(tab);
}
function retryTab(tab) {  // (the tab made again from the start: its hero, its rows)
  const sec = HOMES[tab];
  if (!sec || !sec.failed) return;
  sec.failed = sec.built = sec.ok = false;
  sec.fails = 0;
  sec.classList.remove("nohero");
  sec.querySelector(".rows").textContent = "";
  if (tab === "home") cwSig = null;
  if (lastView === sec.id) renderHome(tab);
}
window.addEventListener("online", () => Object.keys(HOMES).forEach(retryTab));  // (the home-screen app has no reload button)
const heroes = store.get("hero", {});  // (each tab's featured title, picked last visit for this one: shown at once, its pictures fetched)
function hero(tab) {  // (last visit's pick at once; else a title from the top of the first of the tab's catalogs to come in. The next
  const sec = HOMES[tab], h = sec.querySelector(".hero"), last = heroes[tab];
  if (last && last.id && last.type) paintHero(sec, last);
  const types = tab === "series" ? ["series"] : tab === "movies" ? ["movie"] : ["movie", "series"];
  let done = false;
  types.forEach((t) => catalog(t, "top").then((list) => {
    const pool = list.slice(0, 12).filter((m) => m.background && m.description);
    if (done || !pool.length) return;
    done = true;
    const any = (l) => l[Math.floor(Math.random() * l.length)];
    if (!h.dataset.id) paintHero(sec, any(pool));
    const others = pool.filter((m) => m.id !== h.dataset.id), next = any(others.length ? others : pool);
    heroes[tab] = {id: next.id, type: next.type, name: next.name, description: next.description, releaseInfo: next.releaseInfo, year: next.year,
                   runtime: next.runtime, imdbRating: next.imdbRating, genres: genresOf(next), background: next.background, poster: next.poster};
    store.set("hero", heroes);
    whenQuiet(() => { new Image().src = pic.bgSmall(next.id); new Image().src = pic.bg(next.id); new Image().src = pic.logo(next.id); }, 6000);
  }).catch(() => {}));
}
function paintHero(sec, m) {
  const h = sec.querySelector(".hero"), href = "#/title/" + m.type + "/" + m.id;
  h.dataset.id = m.id;
  remember(m);
  backdrop(h.querySelector(".bg.sm"), h.querySelector(".bg.lg"), m.id);
  titleSlot(h.querySelector(".tslot"), m.id, m.name);
  h.querySelector(".meta").innerHTML = metaLine(m);
  h.querySelector(".desc").textContent = m.description || "";
  const w = h.querySelector(".hw"), d = h.querySelector(".hm");
  w.href = href + "?to=watch";
  w.innerHTML = ICON.play + (m.type === "series" ? "Episodes" : "Watch");
  d.href = href;
  d.textContent = "Details";
  h.classList.remove("out");
}
let recentRaw = null, recentList = [];
function recent() {  // (what's being watched, newest first: parsed again only when its stored text has changed)
  let raw = null;
  try { raw = localStorage.getItem("fb.recent"); } catch (e) {}
  if (raw !== recentRaw) { recentRaw = raw; try { recentList = raw ? JSON.parse(raw) : []; } catch (e) { recentList = []; } }
  return recentList.slice();
}
const recentSig = (list) => list.map((r) => r.key + "@" + r.at).join(" ");
function renderContinue() {  // (rebuilt only when what's being watched changed: coming back shows it as it was)
  if (!cwRow) return;
  const list = recent(), sig = recentSig(list);
  if (sig === cwSig) return;
  cwSig = sig;
  cwRow.classList.toggle("hidden", !list.length);
  cwRow.innerHTML = "<h2>Continue watching</h2>";
  const sc = strip(cwRow), f = document.createDocumentFragment();
  list.forEach((r, i) => f.appendChild(cwCard(r, i < 3)));
  sc.insertBefore(f, sc.lastElementChild);
}
function cwCard(r, eager) {
  const a = document.createElement("a");
  a.className = "cw";
  a.href = "#/title/" + r.title.type + "/" + r.title.id;
  const pct = r.d ? Math.min(100, Math.round(100 * r.t / r.d)) : 0;
  const sub = (r.ep ? "S" + r.ep.s + ":E" + r.ep.e + " · " : "") + (r.d ? leftOf(r.t, r.d) : "at " + fmtTime(r.t));
  a.innerHTML = '<div class="im"><span class="hov">' + ICON.play + '</span><button class="x" aria-label="Remove">' + ICON.close + "</button></div>" +
    '<span class="pb"><i style="width:' + pct + '%"></i></span><div class="tx"><div class="nm">' + esc(r.title.name) + '</div><div class="sub">' + esc(sub) + "</div></div>";
  const img = loadPic(new Image(), via(r.thumb) || pic.bgSmall(r.title.id), eager), alt = pic.bgSmall(r.title.id);
  img.alt = "";
  img.onerror = () => { if (img.getAttribute("src") !== alt) img.src = alt; };  // (an episode without its picture: the series')
  a.querySelector(".im").prepend(img);
  a.onclick = (e) => { e.preventDefault(); resume(r); };
  a.addEventListener("pointerdown", () => loadHls().catch(() => {}), {once: true, passive: true});  // (a resume is near)
  if (hoverable) a.addEventListener("pointerenter", () => loadHls().catch(() => {}), {once: true, passive: true});
  a.querySelector(".x").onclick = (e) => { e.preventDefault(); e.stopPropagation(); dropCw(a, r); };
  return a;
}
function dropCw(card, r) {  // (the card fades out, those after it close the gap: FLIP, transforms only)
  const list = recent().filter((x) => x.key !== r.key);
  store.set("recent", list);
  cwSig = recentSig(list);
  libSig = null;
  const sib = [];
  for (let n = card.nextElementSibling; n && n.classList.contains("cw"); n = n.nextElementSibling) sib.push(n);
  const before = sib.map((n) => n.getBoundingClientRect().left);
  card.classList.add("gone");
  setTimeout(() => {
    card.remove();
    if (!list.length) { cwRow.classList.add("hidden"); return; }
    const after = sib.map((n) => n.getBoundingClientRect().left);
    sib.forEach((n, i) => { n.style.transition = "none"; n.style.transform = "translate3d(" + (before[i] - after[i]) + "px,0,0)"; });
    nextFrame(() => sib.forEach((n) => { n.style.transition = "transform 320ms var(--sp-nav)"; n.style.transform = ""; }));
    setTimeout(() => sib.forEach((n) => { n.style.transition = ""; }), 420);
  }, 200);
}

let libSig = null;
const seenList = () => store.get("seen", []);
const libSigNow = () => recentSig(recent()) + "|" + seenList().map((x) => x.id).join(",") + "|" + store.get("done", []).map((x) => x.id).join(",");
function noteSeen(m) {  // (a title opened: first in Recently viewed)
  if (!m || !m.id || !m.type) return;
  const list = seenList(), old = list.find((x) => x.id === m.id);
  if (list[0] && list[0].id === m.id && (old.name || !m.name)) return;
  store.set("seen", [{id: m.id, type: m.type, name: m.name || (old && old.name) || "", poster: m.poster || (old && old.poster) || ""}].concat(list.filter((x) => x.id !== m.id)).slice(0, 24));
  libSig = null;
}
function markWatched(p) {  // (the player's hook: a title watched to its end, first in Watched)
  const t = p && p.title;
  if (!t || !t.id) return;
  const list = store.get("done", []).filter((x) => x.id !== t.id);
  list.unshift({id: t.id, type: t.type, name: t.name || "", poster: t.poster || ""});
  store.set("done", list.slice(0, 50));
  libSig = null;
}
function renderLibrary() {  // (made again only when any of its three lists changed: coming back shows it as it was)
  const sig = libSigNow();
  if (sig === libSig) return;
  libSig = sig;
  const box = byId("libList"), list = recent(), seen = seenList(), done = store.get("done", []);
  box.textContent = "";
  if (!list.length && !seen.length && !done.length) {
    box.innerHTML = cuv(ICON.stack, "Your next watch starts here", "Movies and series you open or watch will appear in your library.", "Explore movies");
    box.querySelector(".btn").onclick = () => { location.hash = "#/movies"; };
    return;
  }
  if (list.length) {
    box.insertAdjacentHTML("beforeend", '<h2 class="lsect">Continue watching</h2><div class="lgroup"></div>');
    const g = box.lastElementChild, f = document.createDocumentFragment();
    list.forEach((r) => f.appendChild(libRow(r)));
    g.appendChild(f);
  }
  [["Recently viewed", seen], ["Watched", done]].forEach((x) => {
    if (!x[1].length) return;
    const row = document.createElement("section");
    row.className = "row";
    row.innerHTML = "<h2>" + x[0] + "</h2>";
    box.appendChild(row);
    fillRow(strip(row), x[1], posterCard);
  });
}
function libRow(r) {
  const d = document.createElement("div"), pct = r.d ? Math.min(100, Math.round(100 * r.t / r.d)) : 0;
  const sub = (r.ep ? "S" + r.ep.s + ":E" + r.ep.e + " · " : "") + (r.d ? leftOf(r.t, r.d) : "at " + fmtTime(r.t));
  d.className = "lrow";
  d.tabIndex = 0;
  d.setAttribute("role", "button");
  d.innerHTML = '<div class="th"></div><div class="tx"><div class="nm">' + esc(r.title.name) + '</div><div class="sub">' + esc(sub) + '</div><span class="pb"><i style="width:' + pct +
    '%"></i></span></div><button class="x" aria-label="Remove ' + esc(r.title.name) + '">' + ICON.xcirc + "</button>";
  const img = loadPic(new Image(), via(r.thumb) || pic.bgSmall(r.title.id), true), alt = pic.bgSmall(r.title.id);
  img.alt = "";
  img.onerror = () => { if (img.getAttribute("src") !== alt) img.src = alt; };
  d.firstChild.appendChild(img);
  d.onclick = (e) => { if (!e.target.closest(".x")) resume(r); };
  d.onkeydown = (e) => { if (e.target === d && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); resume(r); } };
  d.addEventListener("pointerdown", () => loadHls().catch(() => {}), {once: true, passive: true});  // (a resume is near)
  d.lastChild.onclick = (e) => { e.stopPropagation(); dropRow(d, r); };
  return d;
}
function dropRow(row, r) {  // (the row fades out, those below close the gap: FLIP, transforms only)
  const list = recent().filter((x) => x.key !== r.key);
  store.set("recent", list);
  cwSig = null;
  const sib = [];
  for (let n = row.nextElementSibling; n; n = n.nextElementSibling) sib.push(n);
  const before = sib.map((n) => n.getBoundingClientRect().top);
  row.classList.add("gone");
  setTimeout(() => {
    row.remove();
    libSig = libSigNow();
    if (!list.length) { libSig = null; renderLibrary(); return; }
    const after = sib.map((n) => n.getBoundingClientRect().top);
    sib.forEach((n, i) => { n.style.transition = "none"; n.style.transform = "translate3d(0," + (before[i] - after[i]) + "px,0)"; });
    nextFrame(() => sib.forEach((n) => { n.style.transition = "transform 320ms var(--sp-nav)"; n.style.transform = ""; }));
    setTimeout(() => sib.forEach((n) => { n.style.transition = ""; }), 420);
  }, 190);
}

function feedIndex(post) {  // (in the worker post is postMessage; here, should no worker start, a callback)
  let items = null, loading = null;
  const norm = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/&/g, " and ").replace(/[^a-z0-9]+/g, " ").trim();
  function load(url) {
    if (!loading) {
      loading = fetch(url).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }).then((list) => {
        items = (Array.isArray(list) ? list : []).map((m) => ({id: m.id, type: m.type, name: m.name, releaseInfo: m.releaseInfo, poster: m.poster,
                                                               imdbRating: m.imdbRating, p: m.popularity || 0, n: " " + norm(m.name)}));
      });
      loading.catch(() => { loading = null; });
    }
    return loading;
  }
  function find(q) {
    const words = norm(q).split(" ").filter(Boolean), whole = " " + words.join(" "), out = [];
    if (!words.length) return out;
    for (const m of items) {
      if (!words.every((w) => m.n.indexOf(" " + w) >= 0)) continue;
      out.push([(m.n === whole ? 3e6 : m.n.startsWith(whole) ? 2e6 : 0) + m.p, m]);
    }
    out.sort((a, b) => b[0] - a[0]);
    return out.slice(0, 60).map((x) => ({id: x[1].id, type: x[1].type, name: x[1].name, releaseInfo: x[1].releaseInfo, poster: x[1].poster, imdbRating: x[1].imdbRating}));
  }
  return (d) => load(d.url).then(() => post({seq: d.seq, list: find(d.q || "")}), (e) => post({seq: d.seq, error: String((e && e.message) || e)}));
}
const feed = {seq: 0, wait: new Map(), send: null};
function feedGot(m) {
  const w = feed.wait.get(m.seq);
  if (!w) return;
  feed.wait.delete(m.seq);
  if (m.error) w[1](new Error(m.error)); else w[0](m.list || []);
}
function feedHere() {  // (no worker: searched on this thread, the feed parsed when the page is idle)
  const ask = feedIndex(feedGot), idle = window.requestIdleCallback ? (f) => requestIdleCallback(f, {timeout: 400}) : (f) => setTimeout(f, 1);
  feed.send = (d) => idle(() => ask(d));
  feed.wait.forEach((w, seq) => feed.send({url: FEED, q: w[2], seq: seq}));
}
function feedStart() {
  if (feed.send) return;
  try {
    const src = "const ask = (" + feedIndex.toString() + ")((m) => postMessage(m));\nonmessage = (e) => ask(e.data);\n";
    const w = new Worker(URL.createObjectURL(new Blob([src], {type: "text/javascript"})));
    w.onmessage = (e) => feedGot(e.data);
    w.onerror = (e) => { if (e.preventDefault) e.preventDefault(); w.terminate(); feedHere(); };
    feed.send = (d) => w.postMessage(d);
  } catch (e) { feedHere(); }
}
function feedFind(q) {  // (the feed's titles matching q, best first: an exact name, then one it starts, then by popularity)
  feedStart();
  return new Promise((ok, fail) => {
    const seq = ++feed.seq;
    feed.wait.set(seq, [ok, fail, q]);
    feed.send({url: FEED, q: q, seq: seq});
  });
}
function loadFeed() { if (!loadFeed.p) { loadFeed.p = feedFind(""); loadFeed.p.catch(() => { loadFeed.p = null; }); } return loadFeed.p; }  // (on the
let searchSeq = 0, searchTimer = null, urlTimer = null, shownQ = null, shownIds = "", noteWas = "";
const resCards = new Map();  // (the result cards made so far, by title: one still found as more is typed keeps its card and its picture)
const counted = (n) => n + (n === 1 ? " result" : " results");
function note(txt, busy) {  // (written only when it says something else)
  const k = (busy ? "1" : "0") + txt;
  if (k === noteWas) return;
  noteWas = k;
  byId("sNote").innerHTML = (busy ? ICON.aiv : "") + esc(txt);
}
function openSearch(q, focus) {
  const inp = byId("q");
  if (inp.value !== q) inp.value = q;
  byId("qClear").classList.toggle("hidden", !q);
  const fresh = q !== shownQ || !byId("sGrid").firstChild;  // (coming back: the results as they were, where they were; else made after
  afterFrame(() => { if (lastView === "v-search" && fresh && byId("q").value.trim() === q) runSearch(true); loadFeed().catch(() => {}); });
  if (focus && !q && document.activeElement !== inp) {  // (on a touch screen in the tap's own task, so iOS raises the keyboard; with a
    if (focus === "late") afterFrame(() => { if (lastView === "v-search" && document.activeElement !== inp) inp.focus({preventScroll: true}); });
    else inp.focus({preventScroll: true});
  }
}
function resCard(m, i) {
  let a = resCards.get(m.id);
  if (a) return a;
  a = document.createElement("a");
  a.className = "res";
  a.href = "#/title/" + m.type + "/" + m.id;
  const box = document.createElement("div");
  box.className = "card";
  posterImg(box, m, i < 12);
  a.appendChild(box);
  const yr = String(yearOf(m)).slice(0, 4);
  a.insertAdjacentHTML("beforeend", '<div class="nm2">' + esc(m.name) + '</div><div class="yr">' + esc([yr, m.type === "series" ? "Series" : "Movie"].filter(Boolean).join(" · ")) + "</div>");
  resCards.set(m.id, a);
  if (resCards.size > 400) resCards.delete(resCards.keys().next().value);
  return a;
}
let gridWant = [], gridN = 0, gridIO = null;
const GRID = () => (compactMQ.matches ? 12 : 24);  // (results drawn at a time: a phone's screenful, a desktop's)
const gridTail = document.createElement("i");
gridTail.className = "gtail";
function fillGrid(list, add, q) {  // (the grid made to show list: cards kept and moved rather than made again, nothing done at all when
  const seen = new Set(add ? gridWant.map((m) => m.id) : []);
  list = list.filter((m) => m && m.id && !seen.has(m.id) && seen.add(m.id));
  list.forEach(remember);
  const ids = (add ? shownIds + " " : "") + list.map((m) => m.id).join(" "), g = byId("sGrid");
  shownQ = q;
  if (ids === shownIds && g.firstElementChild && g.firstElementChild.classList.contains("res")) return;
  shownIds = ids;
  gridWant = (add ? gridWant : []).concat(list);
  if (!add) gridN = GRID();
  paintGrid();
}
function paintGrid() {
  const g = byId("sGrid"), want = gridWant.slice(0, gridN).map((m, i) => resCard(m, i));
  if (gridN < gridWant.length) want.push(gridTail);
  want.forEach((el, i) => { if (g.children[i] !== el) g.insertBefore(el, g.children[i] || null); });
  while (g.children.length > want.length) g.lastElementChild.remove();
  if (!window.IntersectionObserver) return;
  if (!gridIO) gridIO = new IntersectionObserver((es) => { if (es[es.length - 1].isIntersecting && gridN < gridWant.length) { gridN += GRID(); paintGrid(); } }, {rootMargin: "600px 0px"});
  gridIO.unobserve(gridTail);
  if (gridN < gridWant.length) gridIO.observe(gridTail);  // (observed anew: still near after these, asked again)
}
function runSearch(initial) {
  const q = byId("q").value.trim(), seq = ++searchSeq;
  clearTimeout(typeT);
  byId("qClear").classList.toggle("hidden", !q);
  if (initial !== true) { clearTimeout(urlTimer); urlTimer = setTimeout(searchUrl, 300); }  // (the address follows a pause, not each key)
  clearTimeout(searchTimer);
  if (q.length < 2) {
    note("Popular right now");
    if (shownQ !== null && shownQ.length < 2 && shownIds && byId("sGrid").firstElementChild) { shownQ = q; return; }  // (shown already: left as it is)
    Promise.all([catalog("movie", "top"), catalog("series", "top")]).then((l) => { if (seq === searchSeq) fillGrid(interleave(l[0], l[1]), false, q); })
      .catch((e) => { if (seq === searchSeq) searchFailed("Couldn't load", e); });
    return;
  }
  note("", true);
  feedFind(q).then((found) => {
    if (seq !== searchSeq) return;
    fillGrid(found, false, q);
    if (found.length) note(counted(found.length)); else note("Looking further…", true);
    if (found.length < 8) searchTimer = setTimeout(() => remoteSearch(q, found, seq), 450);
  }).catch(() => { if (seq === searchSeq) remoteSearch(q, [], seq); });
}
function searchFailed(title, e) {  // (nothing could be looked up: said so, with Try again)
  byId("sGrid").innerHTML = cuv(ICON.alert, title, plainErr(e), "Try again");
  byId("sGrid").querySelector(".cuv .btn").onclick = () => runSearch();
  shownIds = ""; gridWant = []; gridN = 0;
  if (gridIO) gridIO.unobserve(gridTail);
  note("");
}
function searchUrl() {  // (the address says what's searched: the search tab's own page to come back to)
  clearTimeout(urlTimer);
  if (lastView !== "v-search") return;
  const q = byId("q").value.trim(), h = "#/search" + (q ? "?q=" + encodeURIComponent(q) : "");
  if (location.hash !== h) history.replaceState(history.state, "", h);
  lastHash = location.hash;
  tabTop.search = tabTop.searchRoot = h;
}
function remoteSearch(q, have, seq) {  // (Cinemeta's own search, slow and at times down: through the fleet, so that it
  const ids = new Set(have.map((m) => m.id));
  getJSON("/api/search?q=" + encodeURIComponent(q)).catch((e) => e || new Error("")).then((list) => {
    if (seq !== searchSeq) return;
    if (list instanceof Error) {  // (the fleet's search failed: what was found stays; with nothing found, that's said)
      if (have.length) note(counted(have.length)); else { shownQ = q; searchFailed("Couldn't search", list); }
      return;
    }
    const more = (Array.isArray(list) ? list : []).filter((m) => m && m.id && !ids.has(m.id)).map((m) => ({id: m.id, type: m.type, name: m.name, releaseInfo: m.year, poster: m.poster}));
    const n = have.length + more.length;
    if (!n) {
      byId("sGrid").innerHTML = cuv(ICON.search, "No results for “" + q + "”", "Check the spelling or try a new search.");
      shownIds = ""; shownQ = q; gridWant = []; gridN = 0;
      if (gridIO) gridIO.unobserve(gridTail);
      note("");
      return;
    }
    fillGrid(more, have.length > 0, q);
    note(counted(n));
  });
}
const sbar = document.querySelector(".sbar");
let typedAt = 0, typeT = null;
byId("q").addEventListener("input", () => {  // (the first key after a pause searches at once; while typing, once the keys pause for
  byId("qClear").classList.toggle("hidden", !byId("q").value.trim());
  clearTimeout(typeT);
  const now = performance.now(), fresh = now - typedAt > 400;
  typedAt = now;
  if (fresh) runSearch(); else typeT = setTimeout(runSearch, 220);
});
byId("q").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
byId("q").addEventListener("focus", () => sbar.classList.add("act"));
byId("q").addEventListener("blur", () => {
  searchUrl();
  setTimeout(() => { if (!byId("q").value && document.activeElement !== byId("q")) sbar.classList.remove("act"); }, 200);  // (after Cancel's own click)
});
byId("qClear").innerHTML = ICON.xcirc;
byId("qClear").onclick = () => { byId("q").value = ""; runSearch(); byId("q").focus(); };
byId("qCancel").onclick = () => { const q = byId("q"); q.value = ""; runSearch(); q.blur(); sbar.classList.remove("act"); window.scrollTo(0, 0); };
document.addEventListener("focusin", (e) => { if (e.target.id === "q") document.documentElement.classList.add("kb"); });  // (the tab bar
document.addEventListener("focusout", (e) => { if (e.target.id === "q") document.documentElement.classList.remove("kb"); });

let cur = null;       // the title: {id, type, name, year, poster, runtime}
let curEp = null;     // the episode: {s, e, name, id, thumb}
let series = {id: null, videos: []};  // (the episodes of the series shown)
let shown = null, seasonShown = null, pendingTo = null, posVer = 0, posSeen = -1;
const here = {season: null, episode: null};  // (the season and episode the address asks for now)
const epsOf = (m) => (m.videos || []).filter((v) => v.season != null && v.episode != null);
function scrollToSel(el) { window.scrollTo({top: el.getBoundingClientRect().top + window.scrollY - byId("nav").offsetHeight + 4, behavior: smooth()}); }
function showTitle(type, id, season, episode, to) {
  const key = type + "/" + id;
  here.season = season;
  here.episode = episode;
  pendingTo = to === "watch" || to === "streams" || to === "versions" ? key : null;
  if (shown !== key || !cur || cur.id !== id || (type === "series" && series.id !== id)) {  // (another title; or this one, should
    shown = key;
    const m = known.get(id) || {id: id, type: type};
    cur = {id: id, type: type, name: m.name || "", year: yearOf(m), poster: pic.poster(id), runtime: m.runtime || ""};
    curEp = null;
    byId("tAbout").open = false;
    backdrop(byId("tBgS"), byId("tBg"), id);
    titleSlot(byId("tSlot"), id, m.name);
    paintTitle(m, type);
    noteSeen({id: id, type: type, name: m.name, poster: m.poster});
    byId("tSeries").classList.toggle("hidden", type !== "series");
    byId("tVersions").classList.toggle("hidden", type !== "movie");
    byId("tSeasons").innerHTML = byId("tEps").innerHTML = "";
    byId("tSeasons").dataset.id = "";
    seasonShown = null;
    listsFor = null;
    if (type === "series") {
      const tl = byId("tList");
      if (tl.spy) { tl.spy.disconnect(); tl.spy = null; }
      tl.dataset.sid = "";
      tl.innerHTML = "";
      series = {id: id, videos: epsOf(m)};
      epsLoading();
    } else byId("tList").innerHTML = "";
    // Fetch streams as soon as the title opens, independently of its metadata
    // and artwork. Opening an episode likewise starts its stream list directly.
    listsFor = key;
    if (type === "movie") loadVersions(byId("tList"), "movie", id, true, null);
    else if (series.videos.length) { paintSeasons(here.season); openEpisode(here.season, here.episode); }
    meta(type, id).then((full) => {
      if (shown !== key) return;
      cur.name = full.name || cur.name;
      cur.year = yearOf(full) || cur.year;
      cur.runtime = full.runtime || cur.runtime;
      paintTitle(full, type);
      if (full.name) noteSeen({id: id, type: type, name: full.name, poster: full.poster});
      if (type === "series") {
        const vids = epsOf(full), had = series.id === id ? series.videos.length : -1;
        if (had !== vids.length || !byId("tSeasons").firstChild) {
          series = {id: id, videos: vids};
          seasonShown = null;
          if (vids.length) { paintSeasons(here.season); openEpisode(here.season, here.episode); }
          else epsFailed("none");
        }
      }
      watchScroll(key, type);
    }).catch((e) => {
      if (shown !== key) return;
      byId("tDesc").innerHTML = "Couldn't load its details. " + esc(plainErr(e)) + ' <button class="again">Try again</button>';
      byId("tDesc").querySelector(".again").onclick = reloadTitle;
      byId("tAbout").hidden = false;
      byId("tAbout").open = true;
      if (type === "series" && !series.videos.length && listsFor === key) epsFailed(e);
      watchScroll(key, type);
    });
  } else {
    paintActs(known.get(id) || cur, type);  // (back from the player: resume where it was left)
    if (type === "movie") fitNames(byId("tList"));  // (names a list got while its page was hidden)
    if (type === "series" && series.videos.length) {
      paintSeasons(season);
      openEpisode(season, episode);
    }
    watchScroll(key, type);  // (Watch for the title opened last: its details are in already)
  }
  if (type === "movie") closeSheet(true);
}
let listsFor = null;  // (the title whose stream/episode list has been started)
function reloadTitle() { shown = null; route(false); }  // (Try again: the page made again, its details asked for anew)
function watchScroll(key, type) {  // (Watch from the home page: to the streams or episodes, once the page has its details)
  if (pendingTo !== key) return;
  pendingTo = null;
  history.replaceState(history.state, "", location.hash.split("?")[0]);
  lastHash = location.hash;
  if (window.scrollY < 40) nextFrame(() => { if (shown === key && lastView === "v-title") scrollToSel(byId(type === "series" ? "tSeries" : "tVersions")); });
}
function paintTitle(m, type) {
  if (m.name) { byId("tName").textContent = m.name; if (lastView === "v-title") byId("navTitle").textContent = m.name; }
  byId("tMeta").innerHTML = metaLine(Object.assign({type: type}, m));
  byId("tDesc").textContent = m.description || "";
  const cast = (m.cast || []).slice(0, 5), dir = (m.director || []).slice(0, 2);
  byId("tCredits").innerHTML = (cast.length ? "<div><span>Cast</span>" + esc(cast.join(", ")) + "</div>" : "") +
    (dir.length ? "<div><span>Director</span>" + esc(dir.join(", ")) + "</div>" : "");
  byId("tAbout").hidden = !m.description && !cast.length && !dir.length;
  paintActs(m, type);
}
function paintActs(m, type) {  // Resume is a direct action; movie streams load without a separate chooser button.
  const box = byId("tActs"), r = recent().find((x) => x.title.id === (m.id || cur.id));
  box.classList.toggle("hidden", !r && type !== "series");
  if (r) {
    box.innerHTML = '<button class="btn">' + ICON.play + "Resume" + (r.ep ? " S" + r.ep.s + ":E" + r.ep.e : "") + "</button>";
    box.firstChild.onclick = () => resume(r);
    box.firstChild.addEventListener("pointerdown", () => loadHls().catch(() => {}), {once: true, passive: true});  // (a resume is near)
  } else if (type === "series") {
    box.innerHTML = '<button class="btn">' + ICON.play + "Browse episodes</button>";
    box.firstChild.onclick = () => scrollToSel(byId("tSeries"));
  } else box.innerHTML = "";
}
function seasonList() {
  const ss = [...new Set(series.videos.map((v) => v.season))];
  return ss.sort((a, b) => (a === 0) - (b === 0) || a - b);
}
function epsLoading() {
  const box = byId("tEps");
  box.classList.add("vload", "breathe");
  box.innerHTML = '<div class="ep"><div class="th"></div><div class="tx"><div class="bar" style="width:60%"></div><div class="bar" style="width:35%;margin-top:8px"></div></div></div>'.repeat(3);
}
function epsFailed(why) {  // ("none": the series lists none yet; else what went wrong, with Try again)
  const box = byId("tEps");
  box.classList.remove("vload", "breathe");
  if (why === "none") { box.innerHTML = cuv(ICON.film, "No episodes yet", "None are listed for this series so far."); return; }
  box.innerHTML = cuv(ICON.alert, "Couldn't load the episodes", plainErr(why), "Try again");
  box.querySelector(".cuv .btn").onclick = reloadTitle;
}
function paintSeasons(season) {
  const ss = seasonList();
  if (!ss.length) return;
  byId("tSeries").classList.remove("hidden");
  const sid = series.id, last = recent().find((r) => r.title.id === sid && r.ep);
  const s = season != null && ss.includes(Number(season)) ? Number(season) : last && ss.includes(last.ep.s) ? last.ep.s : ss.find((x) => x > 0) || ss[0];
  const box = byId("tSeasons"), sig = sid + ":" + ss.join(",");
  if (box.dataset.id !== sig) {
    box.dataset.id = sig;
    box.innerHTML = ss.map((x) => '<button class="pill" data-s="' + x + '">' + (x === 0 ? "Specials" : "Season " + x) + "</button>").join("");
    box.querySelectorAll(".pill").forEach((b) => { b.onclick = () => replaceHash("#/title/series/" + sid + "/" + b.dataset.s); });
  }
  box.querySelectorAll(".pill").forEach((b) => b.classList.toggle("on", Number(b.dataset.s) === s));  // (its fill crossfades)
  const want = sid + ":" + s;
  if (seasonShown === want) {  // (the same season: back from the player, its progress brought up to date in place)
    if (posSeen !== posVer) { posSeen = posVer; epProgress(); }
    markEp();
    return;
  }
  const animate = seasonShown !== null && seasonShown.split(":")[0] === sid;
  seasonShown = want;
  posSeen = posVer;
  const pos = store.get("pos", {}), eps = byId("tEps"), f = document.createDocumentFragment();
  series.videos.filter((v) => v.season === s).sort((a, b) => a.episode - b.episode).forEach((v) => {
    f.appendChild(epRow(v, pos, () => go("#/title/series/" + sid + "/" + s + "/" + v.episode)));
  });
  eps.classList.remove("vload", "breathe");
  eps.replaceChildren(f);
  if (animate) anim(eps, RISE, 260);
  markEp();
}
const sidOf = (v) => String(v.id || "").split(":")[0] || series.id;  // (an episode's series, from its own id)
function epPos(v, pos) {  // (how far an episode was watched: its latest place, and its length where that's known)
  const sid = sidOf(v), pre = sid + ":" + v.season + ":" + v.episode + "|";
  let best = null;
  Object.keys(pos).forEach((k) => { if (k.startsWith(pre) && (!best || pos[k].at > best.at)) best = pos[k]; });
  const r = recent().find((x) => x.title.id === sid && x.ep && x.ep.s === v.season && x.ep.e === v.episode);
  if (r && (!best || r.at >= best.at)) best = {t: r.t, d: r.d, at: r.at};
  return best && best.t > 30 ? best : null;
}
let dateFmt = null;
function epDate(v) {  // (one formatter for every row: the first is costly)
  if (!v.released) return "";
  try {
    if (!dateFmt) dateFmt = new Intl.DateTimeFormat(undefined, {year: "numeric", month: "short", day: "numeric"});
    const d = new Date(v.released);
    return isNaN(d.getTime()) ? "" : dateFmt.format(d);
  } catch (e) { return ""; }
}
const leftText = (p) => (p ? (p.d ? leftOf(p.t, p.d) : "watched to " + fmtTime(p.t)) : "");
function epRow(v, pos, onPick) {
  const d = document.createElement("div"), sid = sidOf(v);
  d.className = "ep";
  d.tabIndex = 0;
  d.setAttribute("role", "button");
  d.dataset.s = v.season;
  d.dataset.e = v.episode;
  const p = epPos(v, pos || store.get("pos", {})), pct = p && p.d ? Math.min(100, Math.round(100 * p.t / p.d)) : 0;
  const bits = [epDate(v), cur && cur.id === sid ? cur.runtime : ""].filter(Boolean).join(" · ");
  const about = v.overview || v.description || "";
  d.innerHTML = '<div class="thw"><div class="th"><span class="hov">' + ICON.play + "</span></div>" + (pct ? '<span class="pb"><i style="width:' + pct + '%"></i></span>' : "") + "</div>" +
    '<div class="tx"><div class="tt"><span class="no">' + v.episode + "</span>" + esc(v.name || v.title || "Episode " + v.episode) + "</div>" +
    '<div class="sm"><span class="bits">' + esc(bits) + '</span><span class="left">' + esc(leftText(p)) + "</span></div>" + (about ? "<p>" + esc(about) + "</p>" : "") + "</div>";
  if (v.thumbnail) {
    const img = loadPic(new Image(), via(v.thumbnail));
    img.alt = "";
    d.querySelector(".th").prepend(img);
  }
  d.onclick = onPick;
  d.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(); } };
  return d;
}
function epProgress() {  // (the progress lines and "left" words of the episodes shown, brought up to date; the rows stay)
  const pos = store.get("pos", {});
  byId("tEps").querySelectorAll(".ep").forEach((d) => {
    const v = series.videos.find((x) => x.season === Number(d.dataset.s) && x.episode === Number(d.dataset.e));
    if (!v) return;
    const p = epPos(v, pos), pct = p && p.d ? Math.min(100, Math.round(100 * p.t / p.d)) : 0, thw = d.querySelector(".thw"), left = d.querySelector(".sm .left");
    let pb = thw.querySelector(".pb");
    if (pct && !pb) { thw.insertAdjacentHTML("beforeend", '<span class="pb"><i></i></span>'); pb = thw.lastElementChild; }
    if (pb) { if (pct) pb.firstChild.style.width = pct + "%"; else pb.remove(); }
    if (left) left.textContent = leftText(p);
  });
}
function markEp() {  // (the episode whose streams are open: outlined)
  const open = sheet.classList.contains("open") && curEp && series.id === (cur && cur.id);
  byId("tEps").querySelectorAll(".ep").forEach((d) => d.classList.toggle("on", !!open && Number(d.dataset.s) === curEp.s && Number(d.dataset.e) === curEp.e));
}


const skipLink = document.querySelector(".skip-link");
if (skipLink) skipLink.addEventListener("click", (event) => {
  event.preventDefault();
  const view = document.querySelector("main > .view:not(.hidden):not(.parked)") || byId("mainContent");
  view.setAttribute("tabindex", "-1");
  view.focus({preventScroll: true});
  window.scrollTo({top: 0, behavior: "auto"});
});
const SECT = {  // (the fleet's section names: a flat pictogram of the same thing, a word, a line on what it is)
  "🎞 Original": ["Original", "Original video without re-encoding, repackaged for the browser with lossless audio conversion.", "film", "orig"],
  "▶ As is": ["As is", "The source file, played directly without conversion.", "asis", "asis"],
  "🌐 Browser 1080p": ["Converted", "Converted to browser-friendly H.264 video and stereo audio.", "globe", "conv"],
};
const sectOf = (name) => SECT[name] || [String(name).replace(/^[^A-Za-z0-9]+/, "") || "Streams", "", /Browser|onvert/.test(name) ? "globe" : "film", "other"];
const sectLabel = (name) => ICON[sectOf(name)[2]] + esc(sectOf(name)[0]);
function kindOf(s) { const n = (s.name || "").split("\n"); return n[0] + " · " + (s.f ? s.f.h : (n[1] || "")); }  // (the file probed: its own words)
function nameCut(f) {  // (where a long name's end starts: its last 15 to 26 characters, from a separator)
  let cut = Math.max(0, f.length - 15);
  for (let i = cut; i > Math.max(0, f.length - 26); i--) if (" .-_[(".indexOf(f[i]) >= 0) { cut = " ._".indexOf(f[i]) >= 0 ? i + 1 : i; break; }
  return cut;
}
function fileName(f) {  // (long names lose their middle, not their end: the release group stays readable; fitNames cuts them to fit)
  const cut = nameCut(f);
  return '<span class="vf" title="' + esc(f) + '"><span class="h">' + esc(f.slice(0, cut)) + '</span><span class="t">' + esc(f.slice(cut)) + "</span></span>";
}
let nameCtx = null;
const nameW = {};  // (a list's file-name width, by where the list is and the window's width: measured once, then known)
function fitNames(box) {  // (the file names of a list cut to its width: the start ends on a whole word before "…", so a cut never
  const vfs = box.querySelectorAll(".vf:not(.fit)");
  if (!vfs.length) return;
  const key = (box.closest(".sscroll") ? "s" : "p") + lastW, w = nameW[key];
  if (!w) {  // (and not while the sheet slides up)
    setTimeout(() => afterFrame(() => { const v = box.querySelector(".vf:not(.fit)"), m = v ? v.clientWidth - 1 : 0; if (m > 40) { nameW[key] = m; fitNames(box); } }), key[0] === "s" ? sheetSliding() : 0);
    return;
  }
  if (!nameCtx) { nameCtx = document.createElement("canvas").getContext("2d"); nameCtx.font = "400 12px " + getComputedStyle(vfs[0]).fontFamily; }
  const len = (s) => nameCtx.measureText(s).width;
  vfs.forEach((vf) => {
    vf.classList.add("fit");
    const f = vf.title;
    if (len(f) <= w) { vf.innerHTML = '<span class="h">' + esc(f) + "</span>"; return; }
    const cut = nameCut(f), tail = f.slice(cut), room = w - len("…" + tail);
    let lo = 0, hi = cut;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (len(f.slice(0, m)) <= room) lo = m; else hi = m - 1; }
    while (lo > 0 && " ._-[(".indexOf(f[lo - 1]) >= 0) lo--;  // (no separator left hanging before the "…")
    if (lo >= 6) { vf.innerHTML = '<span class="h">' + esc(f.slice(0, lo)) + "…</span>" + '<span class="t">' + esc(tail) + "</span>"; return; }
    let k = f.length;  // (too narrow to keep its start: "…" and as much of its end as fits)
    while (k > 0 && len("…" + f.slice(k - 1)) <= w) k--;
    while (k < f.length - 1 && " ._-[(".indexOf(f[k]) >= 0) k++;
    vf.innerHTML = '<span class="t">…' + esc(f.slice(k)) + "</span>";
  });
}
function sizeLine(info) {  // ("💾 83.6 GB · as on the source…": the size in bold, the floppy dropped)
  const m = /^💾\s*([\d.]+\s*[KMGT]B)(.*)/.exec(info || "");
  return m ? "<b>" + esc(m[1]) + "</b>" + esc(m[2]) : esc(String(info || "").replace(/^💾\s*/, ""));
}
const RELEASES = [[/remux/i, "Remux"], [/blu-?ray|\bbd(rip)?\b|\bbrrip\b|\buhd\b/i, "Blu-ray encode"], [/web-?dl|\bweb\b(?!-?rip)/i, "WEB-DL"], [/web-?rip/i, "WEB rip"],
                  [/hdtv|dvd/i, "TV/DVD"]];  // (the kinds of release the fleet sorts each section by, best first; tor.py's quality())
function verHead(label, file) {
  const resolution = (text) => {
    const match = [[/2160p|\b4k\b|\buhd\b/i, "4K"], [/1080p/i, "1080p"], [/1080i/i, "1080i"], [/720p/i, "720p"], [/576[pi]|480[pi]|\bsd\b/i, "SD"]]
      .find(([pattern]) => pattern.test(text || ""));
    return match ? match[1] : "";
  };
  const release = RELEASES.find(([pattern]) => pattern.test(file || ""));
  return [resolution(label) || resolution(file), release ? release[1].replace(" encode", "") : ""].filter(Boolean).join(" · ");
}

function verSize(info) {  // ("17.6 GB" first; then what the fleet says of it, but not what its section's header says already)
  const m = /^💾\s*([\d.]+\s*[KMGT]B)(.*)/.exec(info || "");
  if (!m) return esc(String(info || "").replace(/^💾\s*/, ""));
  const bits = m[2].split(" · "), head = bits.shift(), keep = bits.filter((b) => !/^as on the source|^the original file/.test(b));
  return (parseFloat(m[1]) > 0 ? "<b>" + esc(m[1]) + "</b>" : "Size unavailable") + esc(head + (keep.length ? " · " + keep.join(" · ") : ""));
}
const unfit = (s) => !!s.f && ((/\/orig\.m3u8/.test(s.url || "") && !s.f.o) || (!!s.apple && !s.f.a));  // (its probe showed it can't play here: see tor.py's facts_of)
function sourceKind(s) { return sectOf((s.name || "").split("\n")[0]); }
const playbackVersion = (s) => Object.prototype.hasOwnProperty.call(SECT, (s.name || "").split("\n")[0]);
function versionBytes(s) {
  // The backend's size is the source file size, including for converted options.
  const hinted = (s.behaviorHints || {}).videoSize;
  if (typeof hinted === "number" && Number.isFinite(hinted) && hinted > 0) return hinted;
  const info = (s.title || "").split("\n")[1] || "";
  const m = /(?:^|[^\w.])(\d[\d,]*(?:\.\d+)?)\s*([KMGT]?i?B)\b/i.exec(info);
  if (!m) return null;
  const number = Number(m[1].replace(/,/g, "")), unit = m[2].toUpperCase();
  const power = {B: 0, KB: 1, KIB: 1, MB: 2, MIB: 2, GB: 3, GIB: 3, TB: 4, TIB: 4}[unit];
  const bytes = number * Math.pow(unit.includes("I") ? 1024 : 1000, power);
  return Number.isFinite(bytes) && bytes > 0 ? bytes : null;
}
function sortedVersions(list, direction) {
  const seen = new Set(), rank = {orig: 0, asis: 1, conv: 2};
  return list.map((source, index) => ({source, index, bytes: versionBytes(source), kind: sourceKind(source)[3]}))
    .filter((item) => {
      const identity = item.kind + "|" + (item.source.url || "");
      if (!playbackVersion(item.source) || !item.source.url || seen.has(identity)) return false;
      seen.add(identity);
      return true;
    }).sort((a, b) => {
      if (a.bytes === null || b.bytes === null) return a.bytes === b.bytes ? a.index - b.index : a.bytes === null ? 1 : -1;
      const size = direction === "smallest" ? a.bytes - b.bytes : b.bytes - a.bytes;
      return size || (rank[a.kind] ?? 4) - (rank[b.kind] ?? 4) || a.index - b.index;
    }).map((item) => item.source);
}
function verTop(s) {
  const n = (s.name || "").split("\n"), t = (s.title || "").split("\n"), kind = sourceKind(s);
  const quality = verHead(s.f ? s.f.h : (n[1] || ""), t[0]);
  return '<span class="vbadge" data-kind="' + esc(kind[3]) + '" title="' + esc(kind[1]) + '">' + esc(kind[0]) + '</span>' +
    (quality ? '<span class="vquality">' + esc(quality) + '</span>' : "");
}
function streamSize(s) {
  let bytes = versionBytes(s), unit = 0;
  if (bytes === null) return "Size unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  while (bytes >= 1000 && unit < units.length - 1) { bytes /= 1000; unit++; }
  return Number(bytes.toFixed(unit ? 1 : 0)) + " " + units[unit];
}
function verWords(s) { return '<b>' + esc(streamSize(s)) + '</b>'; }
function versionPlayLabel(s) {
  const file = (s.title || "").split("\n")[0];
  return "Play " + sourceKind(s)[0] + ": " + (file || "this stream") + ", " + streamSize(s);
}

function versionFileUrl(s) { return new URL((s.download || s.url).replace("/dl/", "/file/"), location.href).href; }
function verRow(s, onPick) {
  const t = (s.title || "").split("\n"), d = document.createElement("div");
  d.className = "ver";
  d._source = s;
  if (s.k) d.dataset.k = s.k;
  d.dataset.kind = sourceKind(s)[3];
  d.innerHTML = '<button class="vplay" type="button" aria-label="' + esc(versionPlayLabel(s)) + '" title="' + esc(t[0] || "Play stream") + '"><span class="vt"><span class="vk">' + verTop(s) +
    '</span><span class="vs">' + verWords(s) + '</span></span><span class="go" aria-hidden="true">' + ICON.play + '</span></button>' +
    (s.download ? '<button class="dl cp" type="button" title="Copy file link" aria-label="Copy file link">' + ICON.link + '</button>' +
      '<a class="dl" href="' + esc(s.download) + '" download title="Download source file" aria-label="Download source file">' + ICON.dl + '</a>' : "");
  d.querySelector(".vplay").onclick = () => onPick(s);
  const copy = d.querySelector(".cp");
  if (copy) copy.onclick = () => copyUrl(versionFileUrl(s));
  return d;
}
function copyUrl(url) {  // (a stream's link, to the clipboard; where that's refused (an old browser, no focus), the old way)
  const old = () => {
    const t = document.createElement("textarea");
    t.value = url;
    t.setAttribute("readonly", "");
    t.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(t);
    t.select();
    t.setSelectionRange(0, url.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (err) {}
    t.remove();
    toast(ok ? "Link copied" : "Couldn't copy the link", !ok);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(() => toast("Link copied"), old);
  else old();
}
function loadingVersions() {
  const row = '<div class="ver" aria-hidden="true"><div class="vt"><div class="bar" style="width:38%"></div><div class="bar" style="width:74%;margin-top:9px"></div>' +
    '<div class="bar" style="width:56%;margin-top:9px;height:10px"></div></div><span class="go"></span></div>';
  return '<div class="vload"><div class="vsec"><div class="vsec-h"><b>Finding streams…</b></div><p class="vsec-d">Streams appear here automatically, sorted by size.</p>' +
    '<div class="vgroup breathe">' + row.repeat(4) + "</div></div></div>";
}
const verId = (file, info) => file + "|" + ((String(info || "").match(/[\d.]+ ?[GM]B/) || [""])[0]);  // (a file, by its name and size: its links change)
let appleSeen;
function applePlayer() {  // (Safari's own player, on an iPhone, an iPad or a Mac: an MP4's HEVC, Dolby Vision and Dolby audio as they are)
  if (appleSeen === undefined) {
    const video = document.createElement("video");
    const webkit = typeof video.webkitEnterFullscreen === "function" || typeof video.webkitSetPresentationMode === "function";
    appleSeen = !!video.canPlayType("application/vnd.apple.mpegurl") && (/Apple/.test(navigator.vendor || "") || webkit);
  }
  return appleSeen;
}
const appleFiles = new Set();  // (the links of files listed for Apple's own player: one it refuses goes converted, see fileFailed)
const FACTS_KEEP = 30 * 864e5;
function factsIn(list) {  // (what this device learnt of the files before (see noteFacts): said at once)
  const known = store.get("facts", {});
  list.forEach((s) => {
    if (s.f || !s.k) return;
    const t = (s.title || "").split("\n"), x = known[verId(t[0] || "", t[1])];
    if (x && Date.now() - x.at < FACTS_KEEP) { s.f = x.f; delete s.soon; }
  });
}
function noteFacts(list) {  // (what the fleet found the files to be, kept 30 days on this device, 400 at most: the next list of them says it at once)
  const known = store.get("facts", {}), now = Date.now();
  let n = 0;
  list.forEach((s) => { if (s.f && s.k) { const t = (s.title || "").split("\n"); known[verId(t[0] || "", t[1])] = {f: s.f, at: now}; n++; } });
  if (!n) return;
  const kept = {};
  Object.keys(known).filter((k) => now - known[k].at < FACTS_KEEP).sort((a, b) => known[b].at - known[a].at).slice(0, 400).forEach((k) => { kept[k] = known[k]; });
  store.set("facts", kept);
}
function fitVersions(list) {  // (what this browser can play of the fleet's list: Apple's own only there; an Original, or a file for Apple's
  const bad = store.get("unfit", {});
  factsIn(list);
  return list.filter((s) => {
    if (!playbackVersion(s)) return false;
    if (s.apple && !applePlayer()) return false;
    if (s.apple) appleFiles.add(s.url);
    if (!s.apple && !/\/orig\.m3u8/.test(s.url || "")) return true;
    if (unfit(s)) return false;
    const t = (s.title || "").split("\n"), x = bad[verId(t[0] || "", t[1])];
    return !x || (x.soft && Date.now() - x.at > 6048e5);
  });
}
const versionViews = new Map(), versionFeeds = new Map();
function pauseVersionFeed(box, forget) {
  if (box.spy) { box.spy.disconnect(); box.spy = null; }
  if (forget) versionFeeds.delete(box);
}
function visibleVersionFeed(box) {
  const view = box.closest(".view");
  return box.isConnected && !document.hidden && box.getClientRects().length > 0 && !box.closest('[aria-hidden="true"]') &&
    (typeof lastKey === "undefined" || lastKey !== "play") && (!view || typeof lastView === "undefined" || view.id === lastView);
}
function syncVersionFeeds() {
  versionFeeds.forEach((feed, box) => {
    if (!box.isConnected) pauseVersionFeed(box, true);
    else feed.watch();
  });
}
function renderVersions(box, list, scroller, onPick, id) {
  pauseVersionFeed(box, true);
  box.tok = null;
  if (box.pin) { if (pinIO) pinIO.unobserve(box.pin); box.pin = null; }
  if (!list) { box.innerHTML = loadingVersions(); box.setAttribute("aria-busy", "true"); return; }
  box.removeAttribute("aria-busy");
  const first = matchMedia("(min-width: 1100px)").matches ? 12 : 8;
  const state = versionViews.get(id) || {shown: first};
  versionViews.delete(id);
  versionViews.set(id, state);
  while (versionViews.size > 80) versionViews.delete(versionViews.keys().next().value);
  const saved = store.get("source-sort", "largest");
  let order = saved === "smallest" ? "smallest" : "largest";
  let rows = sortedVersions(fitVersions(list), order);
  state.shown = Math.min(rows.length, Math.max(first, state.shown));
  box.innerHTML = '<div class="vtoolbar" title="File sizes refer to the source file, including converted playback."><div class="vsummary" role="status" aria-live="polite" aria-atomic="true"></div>' +
    '<label class="vsort"><span>Size</span><select aria-label="Sort streams by file size"><option value="largest">Largest first</option><option value="smallest">Smallest first</option></select></label></div>' +
    '<div class="vgroup" aria-label="Streams"></div>';
  const group = box.querySelector(".vgroup"), select = box.querySelector("select"), summary = box.querySelector(".vsummary");
  const automatic = typeof IntersectionObserver === "function", tail = document.createElement(automatic ? "div" : "button");
  tail.className = automatic ? "vmore-sentinel" : "vmore";
  if (automatic) { tail.setAttribute("aria-hidden", "true"); tail.style.minHeight = "1px"; }
  else { tail.type = "button"; tail.textContent = "Load more streams"; }
  select.value = order;
  const progress = () => {
    summary.innerHTML = '<strong>' + state.shown + '</strong> of <strong>' + rows.length + '</strong> stream' + (rows.length === 1 ? "" : "s");
    if (state.shown < rows.length) group.appendChild(tail);
    else tail.remove();
  };
  const append = (start, end) => {
    const frag = document.createDocumentFragment();
    rows.slice(start, end).forEach((source) => frag.appendChild(verRow(source, onPick)));
    group.appendChild(frag);
  };
  const more = (keyboard) => {
    if (versionFeeds.get(box) !== feed || state.shown >= rows.length || !visibleVersionFeed(box)) return;
    pauseVersionFeed(box);
    const start = state.shown;
    state.shown = Math.min(rows.length, state.shown + first);
    tail.remove();
    append(start, state.shown);
    progress();
    fitNames(group);
    if (keyboard) group.querySelectorAll(".vplay")[start]?.focus({preventScroll: true});
    feed.watch();
  };
  const feed = {watch() {
    if (!automatic || state.shown >= rows.length || !visibleVersionFeed(box) || !tail.isConnected) { pauseVersionFeed(box); return; }
    if (box.spy) return;
    const observer = new IntersectionObserver((entries) => {
      if (box.spy !== observer || versionFeeds.get(box) !== feed) return;
      if (entries.some((entry) => entry.target === tail && entry.isIntersecting)) more(false);
    }, {root: scroller || null, rootMargin: "0px 0px 600px 0px", threshold: 0});
    box.spy = observer;
    observer.observe(tail);
  }};
  versionFeeds.set(box, feed);
  if (!automatic) tail.onclick = () => more(document.activeElement === tail);
  group.addEventListener("focusin", (event) => {
    // Keyboard and assistive-tech navigation also prepares the next rows before
    // reaching the end, without taking focus away from the current control.
    if (!automatic || state.shown >= rows.length) return;
    const row = event.target.closest(".ver"), displayed = group.querySelectorAll(".ver");
    if (row && Array.prototype.indexOf.call(displayed, row) >= displayed.length - 2) more(false);
  });
  append(0, state.shown);
  progress();
  fitNames(box);
  feed.watch();
  select.onchange = () => {
    pauseVersionFeed(box);
    order = select.value === "smallest" ? "smallest" : "largest";
    store.set("source-sort", order);
    rows = sortedVersions(fitVersions(list), order);
    state.shown = Math.min(first, rows.length);
    group.replaceChildren();
    append(0, state.shown);
    progress();
    fitNames(box);
    feed.watch();
  };
}

function loadVersions(box, type, sid, count, scroller) {
  const id = type + "/" + sid;
  box.dataset.sid = sid;
  box.reload = () => { if (box.dataset.sid === sid) loadVersions(box, type, sid, count, scroller); };  // (drawn again: see markUnfit)
  box.at = Date.now();
  whenQuiet(() => loadHls().catch(() => {}), 0, 1500);  // (streams shown: a play is near; fetched and compiled once nobody has scrolled
  const pick = (s) => { const t = (s.title || "").split("\n"); play(s.url, kindOf(s), t[0], t[1] || ""); };
  const show = (r) => {
    if (box.dataset.sid !== sid) return;
    box.removeAttribute("aria-busy");
    const list = sortedVersions(fitVersions(r.streams || []), store.get("source-sort", "largest"));
    if (count) byId("tCount").textContent = list.length || "";
    if (!list.length) {
      pauseVersionFeed(box, true);
      box.tok = null;
      box.innerHTML = cuv(ICON.film, "No streams found", r.error ? "The fleet couldn't look just now." : "The fleet has nothing of this title yet.", r.error ? "Try again" : null);
      if (r.error) box.querySelector(".cuv .btn").onclick = () => { memo.delete(versionsUrl(type, sid)); loadVersions(box, type, sid, count, scroller); };
      return;
    }
    const wait = scroller ? sheetSliding() : 0;  // (a list fetched while the sheet slides up: drawn once it's in place)
    if (wait) { setTimeout(() => show(r), wait); return; }
    renderVersions(box, list, scroller, pick, id);
    noteFacts(r.streams || []);
    askFacts(box, r, sid);
  };
  const hit = have(versionsUrl(type, sid), VER_KEEP);
  if (hit) { show(hit); return; }
  if (count) byId("tCount").textContent = "";
  renderVersions(box, null);
  versions(type, sid).then(show).catch((e) => {
    if (box.dataset.sid !== sid) return;
    box.at = 0;
    box.removeAttribute("aria-busy");
    box.innerHTML = cuv(ICON.alert, "Couldn't load streams", plainErr(e), "Try again");
    box.querySelector(".cuv .btn").onclick = () => loadVersions(box, type, sid, count, scroller);
  });
}

function askFacts(box, r, sid) {  // (what the fleet has found of the listed files since the list: asked once soon after it, then, while the first
  const tok = box.factsTok = {}, waits = [2500, 3000, 3500, 4000, 5000, 7000, 10000, 15000];
  let i = 0;
  const ask = () => {
    const keys = [...new Set((r.streams || []).filter((s) => s.k && !s.f).map((s) => s.k))];
    if (box.factsTok !== tok || box.dataset.sid !== sid || !keys.length) return;
    fetch("/browser/facts?k=" + keys.join(",") + (r.r === undefined ? "" : "&r=" + r.r)).then((x) => x.json()).then((got) => {
      if (box.factsTok !== tok || box.dataset.sid !== sid) return;
      tellFacts(box, r, got || {});
      if (i < waits.length && (r.streams || []).some((s) => s.soon && !s.f)) setTimeout(ask, waits[i++]);
    }).catch(() => {});
  };
  setTimeout(ask, 400);
}
function tellFacts(box, r, got) {  // (the rows told what their files are: their words change in place; a list with an Original, or a file for
  let n = 0, drop = false;
  (r.streams || []).forEach((s) => {
    if (s.f || !s.k || !got[s.k]) return;
    s.f = got[s.k];
    delete s.soon;
    n++;
    if (unfit(s)) drop = true;
  });
  if (!n) return;
  noteFacts(r.streams || []);
  whenQuiet(() => {
    if (!box.isConnected) return;
    if (drop && box.reload) { box.reload(); return; }
    box.querySelectorAll(".ver[data-k]").forEach((row) => {
      const s = row._source;
      if (!s || !s.f) return;
      row.querySelector(".vplay").setAttribute("aria-label", versionPlayLabel(s));
      row.querySelector(".vk").innerHTML = verTop(s);
      row.querySelector(".vs").innerHTML = verWords(s);
    });
  }, 0, 600);
}

function trapDialogTab(event, dialog) {
  if (event.key !== "Tab") return;
  const focusable = Array.from(dialog.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
    .filter((element) => element.tabIndex >= 0 && element.getClientRects().length);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1], active = document.activeElement;
  if (!dialog.contains(active) || (event.shiftKey ? active === first : active === last)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus({preventScroll: true});
  }
}
const sheet = byId("sheet"), panel = byId("shPanel"), dim = byId("shDim"), shScroll = byId("shScroll");
let sheetFor = null, sheetBack = null, sheetUp = 0;
const sheetSliding = () => Math.max(0, sheetUp + 600 - performance.now());  // (ms left of the sheet's slide up)
function behind(on) {  // (the page behind the sheet or the player out of reach of taps, keys and readers: set after the first frame,
  clearTimeout(behind.t);
  const set = () => {
    const b = sheet.classList.contains("open") || lastKey === "play";
    document.querySelector("main").inert = b;
    byId("nav").inert = b;
    byId("tabbar").inert = b;
  };
  if (on) behind.t = setTimeout(set, 120); else set();
}
function openEpisode(season, episode) {
  if (episode == null) { closeSheet(true); return; }
  const v = series.videos.find((x) => x.season === Number(season) && x.episode === Number(episode));
  if (!v) { closeSheet(true); return; }
  curEp = {s: v.season, e: v.episode, name: v.name || v.title || "", id: v.id, thumb: v.thumbnail || ""};
  const key = series.id + ":" + v.season + ":" + v.episode, list = byId("shList");
  if (sheetFor !== key || !list.at || Date.now() - list.at > VER_KEEP) {  // (the same episode again: its list as it was left)
    sheetFor = key;
    byId("shSeries").textContent = cur && cur.id === series.id ? cur.name : (known.get(series.id) || {}).name || "";
    byId("shTitle").textContent = "S" + v.season + ":E" + v.episode + (curEp.name ? " · " + curEp.name : "");
    loadImg(byId("shThumb"), via(v.thumbnail) || pic.bgSmall(series.id), true);
    shScroll.scrollTop = 0;
    loadVersions(list, "series", v.id, false, shScroll);
  }
  if (!sheet.classList.contains("open")) {
    sheetUp = performance.now();
    sheet.classList.add("open");
    sheet.setAttribute("aria-hidden", "false");
    document.documentElement.classList.add("lock");
    shieldOn(sheet);
    sheetBack = document.activeElement;
    behind(true);
    setTimeout(() => { if (sheet.classList.contains("open") && !sheet.contains(document.activeElement)) byId("shClose").focus({preventScroll: true}); }, 130);
  }
  versionFeeds.get(list)?.watch();
  markEp();
}
function closeSheet(quiet, instant) {  // (quiet: the address already says so; else back to the season's page, as it was come from;
  const open = sheet.classList.contains("open");
  pauseVersionFeed(byId("shList"));
  if (open && instant) { sheet.classList.add("now"); nextFrame(() => sheet.classList.remove("now")); }
  sheet.classList.remove("open");
  sheet.setAttribute("aria-hidden", "true");
  if (lastKey !== "play") document.documentElement.classList.remove("lock");
  if (open) {
    behind(false);
    if (sheet.contains(document.activeElement) && sheetBack && document.contains(sheetBack)) sheetBack.focus({preventScroll: true});  // (back to the episode)
    sheetBack = null;
  }
  markEp();
  if (quiet || !open) return;
  const p = location.hash.split("?")[0].split("/");  // (#, title, series, id, season, episode)
  if (p[1] !== "title" || p[2] !== "series" || p.length < 6) return;
  const from = ((history.state && history.state.from) || "").split("?")[0].split("/");
  if (from[1] === "title" && from[2] === "series" && from[3] === p[3] && from.length <= 5) history.back();
  else replaceHash("#/title/series/" + p[3] + "/" + p[4]);
}
byId("shClose").onclick = () => closeSheet();
dim.onclick = () => closeSheet();
document.addEventListener("keydown", (event) => { if (sheet.classList.contains("open")) trapDialogTab(event, panel); });
let drag = null;
const rubber = (d, h) => (1 - 1 / (d * 0.55 / h + 1)) * h;
function dragStart(y, t) {
  drag = {y0: y, y: 0, h: panel.offsetHeight, pts: [[t, y]]};
  panel.style.transition = "none";
  dim.style.transition = "none";
}
function dragMove(y, t) {
  const dy = y - drag.y0;
  drag.y = dy < 0 ? -rubber(-dy, drag.h) : dy;
  drag.pts.push([t, y]);
  if (drag.pts.length > 5) drag.pts.shift();
  panel.style.transform = "translate3d(0," + drag.y + "px,0)";
  dim.style.opacity = String(Math.max(0, 1 - Math.max(0, drag.y) / drag.h));
}
function dragEnd() {
  const a = drag.pts[0], b = drag.pts[drag.pts.length - 1];
  const v = (b[1] - a[1]) / Math.max(1, b[0] - a[0]);  // (px a millisecond, downwards)
  const shut = v > 0.45 || (drag.y > drag.h * 0.28 && v > -0.2);
  if (shut) {
    const ms = Math.round(Math.max(160, Math.min(320, (drag.h - drag.y) / Math.max(v, 1.2))));
    panel.style.transition = "transform " + ms + "ms cubic-bezier(.2,.8,.4,1)";
    dim.style.transition = "opacity " + ms + "ms linear";
    panel.style.transform = "";
    dim.style.opacity = "";
    closeSheet();
    setTimeout(() => { panel.style.transition = ""; dim.style.transition = ""; }, ms + 40);
  } else {  // (back to open on the snappy spring)
    panel.style.transition = "transform var(--sp-snappy-ms) var(--sp-snappy)";
    dim.style.transition = "";
    panel.style.transform = "";
    dim.style.opacity = "";
    setTimeout(() => { if (!drag) panel.style.transition = ""; }, 520);
  }
  drag = null;
}
const shHead = byId("shHead");
shHead.addEventListener("pointerdown", (e) => {
  if (e.button !== 0 || e.target.closest("button") || wide()) return;
  shHead.setPointerCapture(e.pointerId);
  dragStart(e.clientY, e.timeStamp);
});
shHead.addEventListener("pointermove", (e) => { if (drag) dragMove(e.clientY, e.timeStamp); }, {passive: true});
shHead.addEventListener("pointerup", () => { if (drag) dragEnd(); });
shHead.addEventListener("pointercancel", () => { if (drag) dragEnd(); });
let pull = null;  // (pulling the list down from its top drags the sheet too, as on iOS)
shScroll.addEventListener("touchstart", (e) => { pull = !wide() && shScroll.scrollTop <= 0 ? {y0: e.touches[0].clientY, on: false} : null; }, {passive: true});
shScroll.addEventListener("touchmove", (e) => {
  if (!pull) return;
  const y = e.touches[0].clientY;
  if (!pull.on) {
    if (y - pull.y0 > 6 && shScroll.scrollTop <= 0) { pull.on = true; dragStart(y, e.timeStamp); }
    else if (y - pull.y0 < -6) { pull = null; return; } else return;
  }
  e.preventDefault();
  dragMove(y, e.timeStamp);
}, {passive: false});
shScroll.addEventListener("touchend", () => { if (pull && pull.on && drag) dragEnd(); pull = null; }, {passive: true});
shScroll.addEventListener("touchcancel", () => { if (pull && pull.on && drag) dragEnd(); pull = null; }, {passive: true});

// Hash navigation changes the visible view after its handler runs. Release old
// observers immediately, then re-arm only the connected, visible source list.
window.addEventListener("hashchange", () => {
  versionFeeds.forEach((feed, box) => pauseVersionFeed(box));
  setTimeout(syncVersionFeeds, 0);
});
document.addEventListener("visibilitychange", syncVersionFeeds);
const vid = byId("vid"), pl = byId("v-player"), pframe = byId("pFrame"), pgest = byId("pGest"), psc = byId("pScrub");
const landMQ = matchMedia("(orientation: landscape)"), mouseMQ = matchMedia("(hover: hover) and (pointer: fine)");
const canFS = !!(document.fullscreenEnabled || document.webkitFullscreenEnabled);  // (false on an iPhone: there only the native player goes full screen)
let playing = null, hls = null, lastPlayed = null, langPicked = false, lastSave = 0, nexting = false, openTs = -1e9, backAt = -1e9;  // (openTs: when the
let uiState = "idle", retrying = false, busyKind = null, busyT = 0, capT = 0, cap2T = 0, ripOn = false, hudOn = false;
let imm = false, wasWide = landMQ.matches, trkW = 0, trkX = 0, zoomed = false, bright = 1, rate = 1, layoutDirty = true;
let hideT = 0, wakeAt = 0, overCtl = false, menuOpen = null, menuBtn = null, kbd = false, swallowAt = -1e9, swallowT = -1;
let gest = null, pinch = null, lastTap = null, tapChain = null, tapT = 0, touchAt = -1e9, upnT = 0, upnOff = null, restartT = 0;
let teardownN = 0, shownSec = -1, barAnims = [], posMem = null, mediaFor = null;  // (mediaFor: the session whose stream is on the element)
let errKind = null, httpFail = null, openerEl = null;  // (httpFail: a part the fleet keeps failing, its card due when the picture runs out)
const live = () => !!playing && mediaFor === playing;
const nativePlayer = () => !!(playing && playing.native);
const scrub = {on: false, t: 0, from: 0, resume: false, pv: null, want: null, dwellT: 0, raf: 0, f: 0, r: null};
const GLYPH = (() => {  // (the player's glyphs: a 24-unit box in the text's colour, round ends, SF Symbols' shapes)
  const g = (w, d, fd, more) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + w + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    (d ? '<path d="' + d + '"/>' : "") + (more || "") + (fd ? '<path d="' + fd + '" fill="currentColor" stroke="none"/>' : "") + "</svg>";
  const ten = '<path stroke-width="1.7" d="M8.4 11.1l1.7-1.1v6.8M13.9 10c1.3 0 2.05 1.45 2.05 3.4s-.75 3.4-2.05 3.4-2.05-1.45-2.05-3.4.75-3.4 2.05-3.4z"/>';
  const spk = "M3.5 9h3.3l4.4-3.7a.6.6 0 0 1 1 .46v12.48a.6.6 0 0 1-1 .46L6.8 15H3.5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1z";
  const box = "M14 13.5h7a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-4.5a1 1 0 0 1 1-1z";
  const tri = (d) => '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="' + d + '"/></svg>';
  const spokes = [["M12 6.8L12 2.6", 1], ["M15.68 8.32L18.65 5.35", .2], ["M17.2 12L21.4 12", .26], ["M15.68 15.68L18.65 18.65", .34], ["M12 17.2L12 21.4", .44],
                  ["M8.32 15.68L5.35 18.65", .56], ["M6.8 12L2.6 12", .7], ["M8.32 8.32L5.35 5.35", .85]];
  return {
    play: g(0, "", "M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"),
    pause: g(0, "", "M6.5 4h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm8 0h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"),
    again: g(2, "M4.5 12a7.5 7.5 0 1 0 2.2-5.3M6.7 3.2v3.5h3.5"),
    back10: g(2, "M12 4.5A8.5 8.5 0 1 1 5.99 6.99M14.6 1.9L12 4.5l2.6 2.6", "", ten),
    fwd10: g(2, "M12 4.5A8.5 8.5 0 1 0 18.01 6.99M9.4 1.9L12 4.5 9.4 7.1", "", ten),
    airplay: g(2, "M7.5 17H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2.5", "M12 13.6l5.2 6.4H6.8z"),
    pip: g(2, "M21 11V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h5M7 8l4 4M7 11.5V8h3.5", box),
    pipExit: g(2, "M21 11V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h5M11 12L7 8M11 8.5V12H7.5", box),
    fsEnter: g(2, "M14 4h6v6M20 4l-6.5 6.5M10 20H4v-6M4 20l6.5-6.5"),
    fsExit: g(2, "M19.5 10h-6V4M13.5 10L20 3.5M4.5 14h6v6M10.5 14L4 20.5"),
    spk0: g(2, "M16 9.5l5 5M21 9.5l-5 5", spk),
    spk1: g(2, "M15.2 9.6a3.4 3.4 0 0 1 0 4.8", spk),
    spk2: g(2, "M15.2 9.6a3.4 3.4 0 0 1 0 4.8M17.8 7a7 7 0 0 1 0 10", spk),
    spk3: g(2, "M15.2 9.6a3.4 3.4 0 0 1 0 4.8M17.8 7a7 7 0 0 1 0 10M20.4 4.4a10.6 10.6 0 0 1 0 15.2", spk),
    audio: g(2, "M5 4.5h14a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-6.2L8 20.5V17H5a2 2 0 0 1-2-2V6.5a2 2 0 0 1 2-2zM8 9.5v3M10.7 8v6M13.3 9v4M16 10v2"),
    next: g(0, "", "M4.5 5.6v12.8a1 1 0 0 0 1.53.85l9.9-6.4a1 1 0 0 0 0-1.7l-9.9-6.4A1 1 0 0 0 4.5 5.6zM18 4.5h1.6a.9.9 0 0 1 .9.9v13.2a.9.9 0 0 1-.9.9H18a.9.9 0 0 1-.9-.9V5.4a.9.9 0 0 1 .9-.9z"),
    more: g(0, "", "M6 10.2a1.8 1.8 0 1 1 0 3.6 1.8 1.8 0 0 1 0-3.6zm6 0a1.8 1.8 0 1 1 0 3.6 1.8 1.8 0 0 1 0-3.6zm6 0a1.8 1.8 0 1 1 0 3.6 1.8 1.8 0 0 1 0-3.6z"),
    sun: g(2, "M12 8.2a3.8 3.8 0 1 1 0 7.6 3.8 3.8 0 0 1 0-7.6zM12 2.8v1.9M12 19.3v1.9M2.8 12h1.9M19.3 12h1.9M5.5 5.5l1.35 1.35M17.15 17.15l1.35 1.35M5.5 18.5l1.35-1.35M17.15 6.85l1.35-1.35"),
    sunLow: g(2, "M12 8.6a3.4 3.4 0 1 1 0 6.8 3.4 3.4 0 0 1 0-6.8zM12 4.6v.4M12 19v.4M4.6 12h.4M19 12h.4M6.8 6.8l.3.3M16.9 16.9l.3.3M6.8 17.2l.3-.3M16.9 7.1l.3-.3"),
    link: g(2.2, "M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"),
    versions: g(2, "M4 6.5h16M4 12h16M4 17.5h10"),
    down: g(2.4, "M6 9.5l6 6 6-6"),
    triL: tri("M15.5 5.2v13.6a.8.8 0 0 1-1.25.66L4.6 12.66a.8.8 0 0 1 0-1.32l9.65-6.8a.8.8 0 0 1 1.25.66z"),
    triR: tri("M8.5 5.2v13.6a.8.8 0 0 0 1.25.66l9.65-6.8a.8.8 0 0 0 0-1.32L9.75 4.54a.8.8 0 0 0-1.25.66z"),
    spin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" aria-hidden="true">' +
      spokes.map((s) => '<path d="' + s[0] + '" opacity="' + s[1] + '"/>').join("") + "</svg>",
  };
})();
[["pBack", ICON.left], ["pAir", GLYPH.airplay], ["pPip", GLYPH.pip], ["pB10", '<span class="gt">' + GLYPH.back10 + "</span>"], ["pF10", '<span class="gt">' + GLYPH.fwd10 + "</span>"], ["pMute", GLYPH.spk3], ["pAudio", GLYPH.audio],
 ["pNextBtn", GLYPH.next], ["pMore", GLYPH.more], ["pFs", GLYPH.fsEnter], ["pFs2", GLYPH.fsEnter], ["pRestart", GLYPH.again], ["pErr", ICON.alert], ["pUpnGo", GLYPH.play],
 ["pUpnX", ICON.close], ["dNext", GLYPH.next], ["dAudio", GLYPH.audio], ["dCopy", GLYPH.link]].forEach((x) => byId(x[0]).insertAdjacentHTML("afterbegin", x[1]));
byId("pPlay").innerHTML = '<span class="pg">' + [["i-play", GLYPH.play], ["i-pause", GLYPH.pause], ["i-again", GLYPH.again]].map((x) => '<span class="gi ' + x[0] + '">' + x[1] + "</span>").join("") + "</span>";
byId("dAudioName").insertAdjacentHTML("afterend", ICON.down.replace("<svg ", '<svg class="dn" '));
pl.querySelector(".aspin").innerHTML = GLYPH.spin;
pl.querySelector("#pRipL .arr").innerHTML = ("<i>" + GLYPH.triL + "</i>").repeat(3);
pl.querySelector("#pRipR .arr").innerHTML = ("<i>" + GLYPH.triR + "</i>").repeat(3);
[["pNativeBack", ICON.left], ["pNativePlay", GLYPH.play], ["pNativeFullscreen", GLYPH.fsEnter], ["pNativePip", GLYPH.pip],
 ["pNativeAirplay", GLYPH.airplay], ["pPipReturn", GLYPH.pip]].forEach(([id, icon]) => byId(id).insertAdjacentHTML("afterbegin", icon));

function configureNativePlayer() {
  const on = nativePlayer();
  pl.classList.toggle("native-player", on);
  byId("pNativeActions").classList.toggle("hidden", !on);
  byId("pNativePlay").classList.add("hidden");
  byId("pNativeStatus").textContent = "";
  vid.controls = on;
  // Let iPhone open its system player from the same gesture that starts playback.
  // Desktop Safari keeps native controls inline when full-screen entry is unavailable.
  vid.playsInline = !on;
  if (on) { vid.removeAttribute("playsinline"); vid.removeAttribute("webkit-playsinline"); }
  else vid.setAttribute("playsinline", "");
  layoutDirty = true;
  paintNativeOptions();
}
function nativeNotice(message) { byId("pNativeStatus").textContent = message || ""; }
function nativeFullscreen() { return !!vid.webkitDisplayingFullscreen || vid.webkitPresentationMode === "fullscreen" || fsEl() === vid; }
const landscape = {held: false, request: 0};
function wantsLandscape() {
  return !!playing && !hoverable && !document.hidden && !pl.classList.contains("hidden") && !inPip() && (nativeFullscreen() || fsEl() === pl);
}
function releaseLandscape() {
  landscape.request++;
  if (!landscape.held) return;
  landscape.held = false;
  try { if (typeof screen !== "undefined" && screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); } catch (_) {}
}
function syncLandscape() {
  if (!wantsLandscape()) { releaseLandscape(); return; }
  const orientation = typeof screen === "undefined" ? null : screen.orientation;
  if (landscape.held || !orientation || typeof orientation.lock !== "function") return;
  const request = ++landscape.request;
  landscape.held = true;
  try {
    Promise.resolve(orientation.lock("landscape")).then(() => {
      // Some implementations settle after unlock. Do not strand another page
      // sideways, but keep a newer valid fullscreen session's requested lock.
      if (!wantsLandscape()) {
        try { if (orientation.unlock) orientation.unlock(); } catch (_) {}
        if (request === landscape.request) landscape.held = false;
      }
    }, () => { if (request === landscape.request) landscape.held = false; });
  } catch (_) { if (request === landscape.request) landscape.held = false; }
  // Unsupported browsers keep their system player's normal rotation behavior.
}
function enterNativeFullscreen(automatic) {
  if (!playing || nativeFullscreen() || inPip()) return;
  // Do not retry from metadata/playing callbacks: those have no user activation.
  if (automatic && navigator.userActivation && !navigator.userActivation.isActive) return;
  const mine = playing;
  const rejected = () => { if (playing === mine && !nativeFullscreen()) nativeNotice("Tap Full screen to open the system player."); };
  try {
    let result;
    if (vid.webkitEnterFullscreen && vid.webkitSupportsFullscreen) result = vid.webkitEnterFullscreen();
    else if (vid.webkitSetPresentationMode && vid.webkitSupportsPresentationMode && vid.webkitSupportsPresentationMode("fullscreen")) result = vid.webkitSetPresentationMode("fullscreen");
    else if (vid.requestFullscreen || vid.webkitRequestFullscreen) result = (vid.requestFullscreen || vid.webkitRequestFullscreen).call(vid);
    else { if (!automatic) nativeNotice("Start playback, then use the video's full-screen control."); return; }
    if (result && result.then) result.then(syncLandscape, rejected);
    else syncLandscape();
  } catch (e) { rejected(); }
}

function posKey(p) { p = p || playing; return p.key + "|" + p.file; }
function posStore() { if (!posMem) posMem = store.get("pos", {}); return posMem; }  // (read once: the player alone writes it)
function loadHls() {
  if (window.Hls) return Promise.resolve();
  if (!loadHls.p) loadHls.p = new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "/gw/cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js";
    s.onload = ok;
    s.onerror = () => { loadHls.p = null; fail(new Error("hls.js didn't load")); };
    document.head.appendChild(s);
  });
  return loadHls.p;
}
const PATIENT = {maxTimeToFirstByteMs: 30000, maxLoadTimeMs: 120000, timeoutRetry: {maxNumRetry: 4, retryDelayMs: 0, maxRetryDelayMs: 0},
                 errorRetry: {maxNumRetry: 6, retryDelayMs: 2000, maxRetryDelayMs: 6000}};  // (a piece being made sends nothing for up to
function hlsConfig(from) {  // (90 s ahead, 30 s behind; the master may take a cold probe's 35 s; playlists come from the reader)
  return {startPosition: from || -1, maxBufferLength: 90, maxMaxBufferLength: 600, maxBufferSize: 200 * 1000 * 1000, backBufferLength: 30,
          fragLoadPolicy: {default: PATIENT},
          playlistLoadPolicy: {default: Object.assign({}, PATIENT, {maxLoadTimeMs: 60000, errorRetry: {maxNumRetry: 2, retryDelayMs: 1000, maxRetryDelayMs: 2000}})},  // (a
          manifestLoadPolicy: {default: {maxTimeToFirstByteMs: 60000, maxLoadTimeMs: 60000, timeoutRetry: {maxNumRetry: 2, retryDelayMs: 0, maxRetryDelayMs: 0},
                                         errorRetry: {maxNumRetry: 1, retryDelayMs: 1000, maxRetryDelayMs: 8000}}}};
}
function kindLabel(kind) {  // ("🎞 Original · 4K HDR Blu-ray": its pictogram and words)
  const i = kind.indexOf(" · "), sect = i >= 0 ? kind.slice(0, i) : kind, rest = i >= 0 ? kind.slice(i + 3) : "";
  if (SECT[sect]) return sectLabel(sect) + esc(rest ? " · " + rest : "");
  return (/^converted/i.test(kind) ? ICON.globe : ICON.film) + esc(kind.charAt(0).toUpperCase() + kind.slice(1));
}
function paintTitles(t, ep) {  // (what's playing, wherever it's named: over the picture and in the dock)
  const about = [t.year, runtime(t)].filter(Boolean).join(" · ");
  byId("pTitle").textContent = t.name || "";
  byId("pSub").textContent = ep ? "S" + ep.s + ":E" + ep.e + (ep.name ? " · " + ep.name : "") : about;
  byId("pEpName").textContent = ep ? ep.name || "Episode " + ep.e : t.name || "";
  byId("pEpSub").textContent = ep ? [t.name, "S" + ep.s + ":E" + ep.e, t.runtime].filter(Boolean).join(" · ") : about;
}
function paintCover(p) {  // (the backdrop, or the episode's still, faint behind the spinner until the first frame; never the last title's)
  const c = byId("pCover"), want = p.ep && p.ep.thumb ? via(p.ep.thumb) : pic.bg(p.title.id), alt = pic.bg(p.title.id);
  c.onload = () => c.classList.add("ok");
  c.onerror = () => { if (c.getAttribute("src") !== alt) c.src = alt; };
  if (c.getAttribute("src") === want && c.complete && c.naturalWidth) { c.classList.add("ok"); return; }
  c.classList.remove("ok");
  c.src = want;
}
function snapFrame() {  // (the frame on screen kept as a picture while the same film reloads or the next one comes: no black, no poster)
  const c = byId("pSnap");
  try {
    if (!vid.videoWidth || vid.readyState < 2) return;
    const k = Math.min(1, 1280 / vid.videoWidth);
    c.width = Math.round(vid.videoWidth * k);
    c.height = Math.round(vid.videoHeight * k);
    c.getContext("2d").drawImage(vid, 0, 0, c.width, c.height);
    c.classList.remove("hidden");
  } catch (e) { c.classList.add("hidden"); }
}
const nextWords = (ep) => "Next: S" + ep.s + ":E" + ep.e + (ep.name ? " · " + ep.name : "");
const WHY = {retry: "Reconnecting…", again: "Trying again…", "switch": "Switching to the converted stream…"};
function play(url, kind, file, info, title, ep, why) {  // (title, ep: what this is, else the title page's own (copied); why: "retry",
  const prev = playing, prevT = prev && mediaFor === prev ? vid.currentTime : 0;
  if (prev) { savePos(true); if (why) snapFrame(); }
  else if (pl.classList.contains("hidden")) {  // (focus goes back there when the player closes; the input's own time starts the shield)
    const a = document.activeElement, ev = window.event;
    openerEl = a && a !== document.body && !pl.contains(a) ? a : null;
    openTs = ev && ev.timeStamp ? ev.timeStamp : performance.now();
  }
  if (title === undefined) { title = cur; ep = curEp; }
  title = Object.assign({}, title || {});
  ep = ep ? Object.assign({}, ep) : null;
  const mine = {key: title.id + (ep ? ":" + ep.s + ":" + ep.e : ""), url: url, kind: kind || "", file: file || "", info: info || "", title: title, ep: ep,
                why: why || null, eps: prev && prev.title.id === title.id ? prev.eps : null, replays: 0, started: false, apple: appleFiles.has(url), native: applePlayer()};
  playing = mine;
  configureNativePlayer();
  lastPlayed = {id: title.id, type: title.type, ep: ep ? {s: ep.s, e: ep.e} : null};
  lastSave = Date.now();
  if (prev) vid.pause();
  else if (!mine.native && !hls && !vid.getAttribute("src")) vid.play().catch(() => {});
  if (why !== "next") { nexting = false; pl.classList.remove("nexting"); }
  clearTimeout(upnT); upnOff = null; hideUpNext();
  clearTimeout(restartT); byId("pRestart").classList.add("hidden");
  byId("pErr").classList.add("hidden");
  retrying = false; httpFail = null; errKind = null;
  closeMenu(); ripOff(); hudOff(0);
  if (!why) { pl.classList.remove("live"); byId("pSnap").classList.add("hidden"); paintCover(mine); }
  if (!why || why === "next") { stopBar(); placeBar(0); shownSec = -1; byId("pCur").textContent = "0:00"; byId("pLeft").textContent = ""; byId("pTrk").querySelectorAll(".buf").forEach((b) => b.remove()); }
  ["pAudio", "dAudio", "pNextBtn", "dNext", "pNext"].forEach((id) => byId(id).classList.add("hidden"));
  closeSheet(true, true);
  paintTitles(title, ep);
  const mode = /^converted/i.test(mine.kind) ? "converted" : mine.kind.split(" · ")[0], size = /\b\d+(?:\.\d+)?\s*[KMGT]B\b/i.exec(mine.info);
  byId("pVer").innerHTML = '<div class="vk">' + kindLabel(mode) + '</div>' + (size ? '<div class="vs">' + sizeLine(size[0]) + '</div>' : '');
  byId("pFileDetails").innerHTML = '<div class="vk">' + kindLabel(mine.kind) + '</div><div class="vs">' + sizeLine(mine.info) + '</div><div class="stream-filename">' + esc(mine.file) + "</div>";
  if (!why || why === "next") byId("pStreamDetails").open = false;
  const saved = (posStore()[posKey(mine)] || {}).t || 0;
  mine.from = why && why !== "next" && prevT > 1 ? prevT : saved > 15 ? saved - 5 : 0;  // (a reconnect goes on from the very place; a resume a
  setUi("start");
  setBusy("start", why === "next" ? nextWords(ep) : why ? WHY[why] : mine.from ? "Picking up at " + fmtTime(mine.from) : "", !!why);
  if (prev && (!why || why === "next")) anim(byId("pDock"), RISE, 260);
  showPlayer();
  if (layoutDirty) playerLayout();
  langPicked = false;
  if (mine.native) {
    attach(mine);  // Source + play stay in the source button's original user gesture.
    if (!why || why === "next") enterNativeFullscreen(true);
  } else {
    if (!hoverable && (!why || why === "next")) enterFull();
    requestAnimationFrame(() => setTimeout(() => attach(mine)));
  }
}
function attach(mine) {  // (the stream itself: the browser's own for a file or Safari's HLS (the Original: HDR, HEVC), else hls.js, which
  if (playing !== mine) return;
  teardown();
  mediaFor = mine;
  setTimeout(() => {  // (the rest a task later, the stream already asked for: no long task while the player fades in)
    if (playing !== mine) return;
    if (!mine.native && !pl.contains(document.activeElement)) pl.focus({preventScroll: true});
    go("#/play");
    setTimeout(() => { if (playing === mine) { loadEps(mine); mediaSession(); } });
  });
  const url = mine.url, from = mine.from, own = vid.canPlayType("application/vnd.apple.mpegurl"), mse = !!(window.MediaSource || window.ManagedMediaSource);
  if (!/\/hls\//.test(url) || (own && (mine.native || !mse))) {
    vid.src = url;
    if (from) vid.addEventListener("loadedmetadata", () => { if (playing === mine) vid.currentTime = from; }, {once: true});
    startPlay(mine);
    return;
  }
  if (!mse) { failPlay("This browser can't play it", "It needs HLS, which this browser doesn't have. Safari, Chrome and Edge do."); return; }
  loadHls().then(() => {
    if (playing !== mine) return;
    if (!Hls.isSupported()) { if (own) { vid.src = url; startPlay(mine); } else failPlay("This browser can't play it", "It needs HLS, which this browser doesn't have. Safari, Chrome and Edge do."); return; }
    const h = hls = new Hls(hlsConfig(from));
    wireHls(h, mine);
    h.loadSource(url + (url.indexOf("?") < 0 ? "?" : "&") + "d=1");  // (its playlist asked for now; the element joined in the next task,
    setTimeout(() => { if (playing === mine && hls === h) { h.attachMedia(vid); startPlay(mine); } });
  }).catch((e) => { if (playing === mine) failPlay("Couldn't start the player", e.message + ". Check the connection, then try again."); });
}
function startPlay(mine) {  // (play; refused (nothing has allowed sound yet): the big play button says what to do)
  vid.defaultPlaybackRate = vid.playbackRate = rate;
  vid.play().catch((e) => {
    if (playing !== mine || vid.error || !e || e.name !== "NotAllowedError") return;  // (an aborted play: the next load's, nothing to say)
    setBusy(null);
    setUi("pause");
    if (mine.native) { byId("pNativePlay").classList.remove("hidden"); nativeNotice("Tap Play to start the video."); }
  });
}
const HLSWHY = {manifestLoadError: "The service didn't send this stream's playlist.", manifestLoadTimeOut: "The service took too long to answer.",
                manifestParsingError: "This stream's playlist is broken.", levelLoadError: "This stream's playlist didn't load.",
                fragLoadError: "A piece of it didn't arrive.", bufferAppendError: "This browser couldn't decode a piece of it.",
                manifestIncompatibleCodecsError: "This browser can't decode this stream."};
function wireHls(h, mine) {
  let recovered = 0;
  h.on(Hls.Events.AUDIO_TRACKS_UPDATED, showLangs);
  h.on(Hls.Events.AUDIO_TRACK_SWITCHED, showLangs);
  h.on(Hls.Events.FRAG_BUFFERED, () => {  // (a piece through: healed)
    if (playing !== mine || hls !== h) return;
    recovered = 0;
    if (!mine.stopped) httpFail = null;  // (a piece through since loading went on again: that part came back)
    if (!retrying) return;
    retrying = false;
    if (busyKind === "retry") settleUi();
  });
  h.on(Hls.Events.ERROR, (ev, d) => {
    if (playing !== mine || hls !== h || !d.fatal) return;
    if (d.type === Hls.ErrorTypes.MEDIA_ERROR && recovered++ < 2) { h.recoverMediaError(); return; }
    const code = d.response && d.response.code;
    if (d.details === "fragLoadError" && code && !elsewhere(d)) {  // (the fleet answered that part with an error 9 times in 20 s: the card,
      let why = "";
      try { why = d.response.text ? JSON.parse(d.response.text).error || "" : ""; } catch (e) {}
      const ahead = bufferedAhead(), t = d.frag && d.frag.start;
      mine.stopped = true;
      httpFail = ["Couldn't load this part", (why ? why + ". " : "The service couldn't get this part (" + code + "). ") + "Try again, or pick another stream."];
      if (ahead > 10 && !mine.bgRound) { mine.bgRound = true; setTimeout(() => { if (playing === mine && hls === h && mine.stopped && uiState !== "error") { mine.stopped = false; h.startLoad(); } }, 4000); return; }
      if (vid.paused || ahead < 1) failPlay(httpFail[0], httpFail[1], "http");
      if (t != null) [20000, 45000].forEach((ms) => setTimeout(() => { if (playing === mine && errKind === "http" && !document.hidden) hint(t + .1); }, ms));  // (behind the
      return;
    }
    if (d.type === Hls.ErrorTypes.NETWORK_ERROR && d.details !== "manifestLoadError" && recovered++ < 4) {  // (no word while the buffer still
      retrying = true;
      if (busyKind) setBusy("retry");
      if (recovered > 2 || elsewhere(d)) setTimeout(() => replay(mine), 1000);  // (its reader may be gone: the master once more, from
      else setTimeout(() => { if (playing === mine && hls === h) h.startLoad(); }, 2000);
      return;
    }
    let why = "";
    try { why = d.response && d.response.text ? JSON.parse(d.response.text).error || "" : ""; } catch (e) {}
    if (d.details === "manifestLoadError" && /\/orig\.m3u8/.test(mine.url) && code === 404) {  // (an Original the fleet may not play as it is:
      origCheck(mine.url).then((bad) => { if (playing === mine && hls === h) twin(bad && bad.code === 404 && /as it is/.test(bad.why) ? markUnfit(mine, unfitWhy(bad.why)) : ""); });
      return;
    }
    if (d.details === "manifestLoadError" && /\/orig\.m3u8/.test(mine.url) && code !== 404) {  // (an Original not ready (a file TorBox hadn't sent
      const n = mine.origTries = (mine.origTries || 0) + 1;
      if (n <= 3) { setTimeout(() => { if (playing === mine && hls === h) h.loadSource(mine.url); }, 1500 * n); return; }
      if (twin("the Original didn't start", true)) return;
    }
    if (twin()) return;
    failPlay("Couldn't play this stream", (why ? why + ". " : HLSWHY[d.details] ? HLSWHY[d.details] + " " : "The stream stopped. ") + "Try again, or pick another stream.");
  });
}
function failPlay(title, detail, kind) {  // (an error: a card that stays until it's acted on; the film paused, its loading stopped)
  if (!playing) return;
  if (nativeFullscreen()) exitFull();  // A failed system player must reveal the retry/version actions.
  if (inPip()) togglePip();
  errKind = kind || "other"; httpFail = null;
  setBusy(null); ripOff(); hudOff(0); hideUpNext(); closeMenu();
  nexting = false; pl.classList.remove("nexting");
  vid.pause();
  if (hls) { try { hls.stopLoad(); } catch (e) {} }
  byId("pErrT").textContent = title;
  byId("pErrP").textContent = detail || "";
  byId("pErr").classList.remove("hidden");
  setUi("error");
}
function bufferedAhead() {  // (seconds of picture left to play where the playhead is)
  const t = vid.currentTime, b = vid.buffered;
  for (let i = 0; i < b.length; i++) if (b.start(i) <= t + .1 && t < b.end(i)) return b.end(i) - t;
  return 0;
}
function tryAgain() {  // (the card's Try again: a part the fleet failed is asked for again on the same stream, from where it stopped (nothing
  const p = playing;
  if (!p) return;
  if (errKind === "http" && hls && mediaFor === p) {
    byId("pErr").classList.add("hidden");
    errKind = null; httpFail = null; p.stopped = false; retrying = true;
    setUi("buffer");
    setBusy("retry", "", true);
    try { hls.startLoad(); } catch (e) {}
    vid.play().catch(() => {});
    return;
  }
  play(p.url, p.kind, p.file, p.info, p.title, p.ep, "again");
}
function elsewhere(d) {  // (a playlist or piece the master sent to a copy's own address, not this site's: that copy may be gone)
  const u = d.url || (d.frag && d.frag.url) || (d.context && d.context.url) || "";
  return /^https?:\/\//.test(u) && u.indexOf("//" + location.host + "/") < 0;
}
function replay(mine) {  // (the same version once more, from where it was, twice at most: the fleet picks its reader again)
  if (!playing || playing !== mine) return;
  const n = (mine.replays || 0) + 1;
  if (n > 2) { failPlay("Couldn't keep playing this stream", "The connection stopped twice. Try again, or pick another stream."); return; }
  play(mine.url, mine.kind, mine.file, mine.info, mine.title, mine.ep, "retry");
  playing.replays = n;
}
function twin(why, keep) {  // (an Original that can't play here: its converted twin instead, said at once with why; keep: it may next
  const p = playing;
  if (!p || !/\/orig\.m3u8/.test(p.url)) return false;
  play(p.url.replace("/orig.m3u8", "/index.m3u8"), "converted (the original can't play here)", p.file, p.info, p.title, p.ep, "switch");
  playing.twinned = why || "this browser can't play the Original";
  if (keep) playing.orig = {url: p.url, kind: p.kind};
  return true;
}
const UNFIT = [[/keyframe|Matroska/, "this Original's file has no keyframe index"], [/Dolby Vision only/, "this Original is Dolby Vision only"],
               [/video is/, "this Original's video doesn't play in browsers"]];
const unfitWhy = (said) => (UNFIT.find((u) => u[0].test(said)) || [0, "this Original can't play as it is"])[1];
function markUnfit(p, why, soft) {  // (an Original that won't play here: the version lists leave it out on this device from now on; soft:
  const all = store.get("unfit", {}), keys = Object.keys(all);
  all[verId(p.file, p.info)] = soft ? {why: why, at: Date.now(), soft: true} : {why: why, at: Date.now()};
  if (keys.length > 200) keys.sort((x, y) => all[x].at - all[y].at).slice(0, keys.length - 200).forEach((k) => delete all[k]);
  store.set("unfit", all);
  document.querySelectorAll("[data-sid]").forEach((box) => { if (box.reload) box.reload(); });  // (the lists shown under the player too)
  return why;
}
function origCheck(url) {  // (what the fleet says of an Original: null when its master, its video's playlist and that's first part (its
  const get = (u) => fetch(u, {cache: "no-store"}).then((r) => (r.ok ? r.text() : r.json().then((j) => Promise.reject({code: r.status, why: j.error || ""}),
    () => Promise.reject({code: r.status, why: ""}))), () => Promise.reject({code: 0, why: ""}));
  return get(url).then((m) => {
    const v = (m.split("\n").find((l) => l.trim() && l[0] !== "#") || "").trim();
    if (!v) return null;
    const vu = new URL(v, new URL(url, location.href)).href;
    return get(vu).then((list) => { const map = /#EXT-X-MAP:URI="([^"]+)"/.exec(list); return map ? get(new URL(map[1], vu).href).then(() => null) : null; });
  }).catch((e) => (e && e.code !== undefined ? e : {code: 0, why: ""}));
}
function origFailed(p) {  // (Safari's own player gave up on an Original: the fleet says why. One it can't play as it is goes converted at
  const t = vid.currentTime, n = p.origTries = (p.origTries || 0) + 1, code = vid.error ? vid.error.code : 0;
  retrying = true;
  if (p.started) setBusy("retry", "", true);  // (before its first frame it's still "Starting…", one wait with one word)
  origCheck(p.url).then((bad) => {
    if (playing !== p) return;
    if (bad && bad.code === 404 && /as it is/.test(bad.why)) { twin(markUnfit(p, unfitWhy(bad.why))); return; }
    if (!bad && n > 1 && code !== 2) { twin(markUnfit(p, "Safari couldn't play this Original", true)); return; }
    if (n > 3) { twin("the Original didn't start", true); return; }
    setTimeout(() => {
      if (playing !== p) return;
      vid.src = p.url;
      if (t > 1) vid.addEventListener("loadedmetadata", () => { if (playing === p) vid.currentTime = t; }, {once: true});
      startPlay(p);
    }, bad ? 1500 * n : 400);
  });
}
function teardown() {  // (the stream let go: hls.js destroyed, the element emptied)
  teardownN++;
  mediaFor = null;
  if (hls) { try { hls.destroy(); } catch (e) {} hls = null; }
  if (vid.getAttribute("src")) { vid.removeAttribute("src"); vid.load(); }
}
function stopPlayer() {  // (leaving: the place kept, the film paused at once; the stream let go once the page under has painted)
  savePos(true);
  playing = null;
  releaseLandscape();
  if (document.pictureInPictureElement === vid && document.exitPictureInPicture) document.exitPictureInPicture().catch(() => {});
  else if (vid.webkitPresentationMode === "picture-in-picture" && vid.webkitSetPresentationMode) { try { vid.webkitSetPresentationMode("inline"); } catch (e) {} }
  byId("pPipReturn").classList.add("hidden");
  nexting = false; retrying = false; errKind = null; httpFail = null;
  vid.pause();
  setBusy(null); ripOff(); hudOff(0); closeMenu(); hideUpNext(); stopBar();
  uiState = "idle";
  pl.classList.remove("paused", "err", "ended", "nexting", "gest", "gx");
  clearTimeout(restartT); byId("pRestart").classList.add("hidden");
  ["pAudio", "dAudio"].forEach((id) => byId(id).classList.add("hidden"));
  if ("mediaSession" in navigator) { try { navigator.mediaSession.metadata = null; } catch (e) {} }
  if (hls) { try { hls.stopLoad(); } catch (e) {} }  // (nothing more fetched from now)
  const n = ++teardownN;
  whenQuiet(() => { if (n === teardownN && !playing) teardown(); }, 700);  // (the rest once the page under has moved in and the finger rests)
}
function savePos(force) {  // (where it's at, every 20 s while it plays and at once on pause, on leaving, on hiding the page; Continue
  const p = playing;
  if (!p || mediaFor !== p || !vid.duration || !isFinite(vid.duration)) return;  // (the last one's stream, still on for a frame: not this one's place)
  const now = Date.now();
  if (!force && now - lastSave < 20000) return;
  lastSave = now;
  const t = vid.currentTime, d = vid.duration, pos = posStore(), done = vid.ended || t > d * 0.95;  // (the credits: it's been watched)
  if (done) delete pos[posKey(p)]; else pos[posKey(p)] = {t: t, d: d, at: now};
  const keys = Object.keys(pos);
  if (keys.length > 300) keys.sort((a, b) => pos[b].at - pos[a].at).slice(300).forEach((k) => delete pos[k]);
  store.set("pos", pos);
  let list = recent().filter((r) => r.key !== p.key);
  if (!done && t > 30) {
    if (p.ep) list = list.filter((r) => r.title.id !== p.title.id);  // (only now: an episode started and left at once loses nothing)
    const o = p.orig || p;  // (an Original that only didn't start: resumed as the Original)
    list.unshift({key: p.key, title: p.title, ep: p.ep, url: o.url, kind: o.kind, file: p.file, info: p.info, thumb: p.ep ? p.ep.thumb : "", t: t, d: d, at: now,
                  apple: o === p && p.apple ? true : undefined});
  }
  store.set("recent", list.slice(0, 16));
  posVer++;
  if (done && !p.marked && typeof markWatched === "function") { p.marked = true; markWatched(p); }
}
function resume(r) { if (r.apple) appleFiles.add(r.url); play(r.url, r.kind, r.file, r.info, r.title, r.ep || null); }
function loadEps(mine) {  // (a series' episodes, for its next one: kept on the session, never on the title page's state)
  if (!mine.ep || mine.title.type !== "series" || mine.eps) { paintNext(); return; }
  meta("series", mine.title.id).then((full) => { if (playing !== mine) return; mine.eps = epsOf(full); paintNext(); mediaSession(); }).catch(() => {});
}
function nextEpisode() {
  const p = playing;
  if (!p || !p.ep || !p.eps) return null;
  const eps = p.eps.filter((v) => (v.season > 0) === (p.ep.s > 0)).sort((a, b) => a.season - b.season || a.episode - b.episode);
  const i = eps.findIndex((v) => v.season === p.ep.s && v.episode === p.ep.e);
  return i >= 0 && i + 1 < eps.length ? eps[i + 1] : null;
}
function paintNext() {  // (the next episode: the button over the picture, the dock's capsule and its row)
  const n = nextEpisode();
  ["pNextBtn", "dNext", "pNext"].forEach((id) => byId(id).classList.toggle("hidden", !n));
  if (n) byId("pNextEp").replaceChildren(epRow(n, posStore(), () => playNext()));
}
function playNext() {  // (the next episode in the same kind of version as this one: named at once, this one paused, the button off
  const n = nextEpisode(), p = playing;
  if (!n || !p || nexting) return;
  nexting = true;
  pl.classList.add("nexting");
  const ep = {s: n.season, e: n.episode, name: n.name || n.title || "", id: n.id, thumb: n.thumbnail || ""};
  hideUpNext();
  vid.pause();
  savePos(true);
  paintTitles(p.title, ep);
  setUi("start");
  setBusy("start", nextWords(ep), true);
  versions("series", n.id).then((r) => {
    if (playing !== p) return;
    const list = r.streams || [], pick = list.find((s) => kindOf(s) === p.kind) || list[0];
    if (!pick) { nextFailed("No streams for the next episode"); return; }
    const lines = (pick.title || "").split("\n");
    play(pick.url, kindOf(pick), lines[0], lines[1] || "", p.title, ep, "next");
  }).catch((e) => { if (playing === p) nextFailed("Couldn't list the next episode's streams: " + e.message); });
}
function nextFailed(msg) {
  nexting = false;
  pl.classList.remove("nexting");
  paintTitles(playing.title, playing.ep);
  setBusy(null);
  setUi(vid.ended ? "end" : "pause");
  say(msg, true);
}
function showPlayer() {  // (the player over the page, in this frame: it fades and settles in; its keys once it has painted)
  if (!pl.classList.contains("hidden")) return;
  pl.classList.remove("hidden");
  if (performance.now() - openTs > 1000) openTs = performance.now();  // (shown without a tap: from now)
  pl.classList.add("shield");
  setTimeout(() => pl.classList.remove("shield"), 500);
  document.documentElement.classList.add("lock", "pl-on");
  if (layoutDirty) playerLayout();
  paintFull();
  showCtl();
  paintWhere();
  if (!reduced.matches && pl.animate) pl.animate([{opacity: 0, transform: "scale(.99)"}, {opacity: 1, transform: "none"}], {duration: 220, easing: "cubic-bezier(.22,1,.36,1)"});
}
function hidePlayer() {
  releaseLandscape();
  if (pl.classList.contains("hidden")) return;
  if (fsEl() === pl) { const x = document.exitFullscreen || document.webkitExitFullscreen; try { const r = x.call(document); if (r && r.catch) r.catch(() => {}); } catch (e) {} }
  if (nativeFullscreen()) exitFull();
  imm = false;
  closeMenu();
  if (gest) { gest = null; pl.classList.remove("gest", "gx"); }
  ptrs.clear(); pinch = null;
  pullTo(0, true);
  if (zoomed) setZoom(false);
  pl.classList.add("hidden");
  pl.classList.remove("idle", "hide", "fs", "shield");
  paintWhere();
  clearTimeout(hideT);
  layoutDirty = true;
  document.documentElement.classList.remove("pl-on");
  if (!sheet.classList.contains("open")) document.documentElement.classList.remove("lock");
  const o = openerEl;
  openerEl = null;
  if (o) setTimeout(() => { const a = document.activeElement; if (o.isConnected && pl.classList.contains("hidden") && !o.closest(".hidden, .parked, [inert]") && (!a || a === document.body || pl.contains(a))) o.focus({preventScroll: true}); });  // (the
}
function placeChips() {}  // (kept for the page's start-up: the controls place themselves now)
function wake() { showCtl(); }

function showCtl(ms) {  // (ms 0: until told)
  if (nativePlayer()) { clearTimeout(hideT); pl.classList.remove("hide", "idle"); return; }
  const was = pl.classList.contains("hide");
  pl.classList.remove("hide", "idle");
  clearTimeout(hideT);
  if (was) { aimBar(); paintTimes(vid.currentTime, true); paintBuf(); }
  if (ms !== 0) hideT = setTimeout(autoHide, ms || (hoverable ? 2500 : 3500));
}
function autoHide() {
  if (nativePlayer()) return;
  const keep = !playing || uiState !== "play" || scrub.on || menuOpen || overCtl || gest || (kbd && byId("pCtl").contains(document.activeElement));
  if (keep) { if (playing) showCtl(); return; }
  hideCtl(true);
}
function hideCtl(idle) { clearTimeout(hideT); if (nativePlayer()) return; pl.classList.add("hide"); if (idle && hoverable) pl.classList.add("idle"); stopBar(); }
function toggleCtl() { if (pl.classList.contains("hide")) showCtl(); else if (uiState !== "error" && !menuOpen) hideCtl(false); }

const GRACE = {start: 250, seek: 250, buffer: 400, retry: 250};
const AFTER = {start: 1500, seek: 3000, buffer: 4000, retry: 0};
const WORDS = {start: "Starting…", seek: "Getting this part ready…", buffer: "Waiting for the stream…", retry: "Reconnecting…"};
function setBusy(kind, words, now) {  // (null: none)
  if (kind && kind === busyKind && !words) return;  // (the same wait again (hls.js's start seek, a snapped seek): its clocks run on)
  clearTimeout(busyT); clearTimeout(capT); clearTimeout(cap2T);
  if (!kind) { busyKind = null; pl.classList.remove("busy", "slow"); return; }
  if (busyKind !== kind) pl.classList.remove("slow");
  busyKind = kind;
  byId("pBusyCap").textContent = words || WORDS[kind];
  const on = () => { if (ripOn || hudOn || gest) { busyT = setTimeout(on, 120); return; } pl.classList.add("busy"); };
  if (pl.classList.contains("busy")) on(); else busyT = setTimeout(on, GRACE[kind]);
  capT = setTimeout(() => pl.classList.add("slow"), now ? GRACE[kind] : Math.max(GRACE[kind], AFTER[kind]));
  if (kind === "start" || kind === "seek") cap2T = setTimeout(() => { byId("pBusyCap").textContent = "Still working on it: the service is " + (playing && /\/orig\.m3u8/.test(playing.url) ? "getting this part ready…" : "converting this part…"); }, 10000);
}
function setUi(s) {  // (idle, start, play, pause, seek, buffer, error, end)
  uiState = s;
  if (s === "play") { byId("pNativePlay").classList.add("hidden"); if (/Tap Play/.test(byId("pNativeStatus").textContent)) nativeNotice(""); }
  pl.classList.toggle("paused", s === "pause" || s === "end" || ((s === "seek" || s === "buffer") && vid.paused));
  pl.classList.toggle("err", s === "error");
  pl.classList.toggle("ended", s === "end");
  byId("pPlay").setAttribute("aria-label", s === "end" ? "Play again" : pl.classList.contains("paused") ? "Play" : "Pause");
  if (s === "pause" || s === "error" || s === "end") showCtl(0);
  else if (s === "play" && !pl.classList.contains("hide")) showCtl();  // (re-armed if they show; a stall that ends never brings them up)
}
function settleUi() {  // (enough here to play, or paused with the frame at the target shown: no indicator)
  if (!playing || uiState === "error" || vid.seeking || vid.readyState < (vid.paused ? 2 : 3)) return;
  setBusy(null);
  setUi(vid.ended ? "end" : vid.paused ? "pause" : "play");
}

function stopBar() { barAnims.forEach((a) => a.cancel()); barAnims = []; }
function placeBar(f) {
  f = Math.max(0, Math.min(1, f || 0));
  byId("pFill").style.transform = "scaleX(" + f + ")";
  byId("pKpos").style.transform = "translate3d(" + f * 100 + "%,0,0)";
}
function aimBar() {
  stopBar();
  if (nativePlayer()) return;
  if (!playing || scrub.on || (gest && gest.kind === "seek")) return;
  const d = vid.duration, t = vid.currentTime;
  if (!(d > 0) || !isFinite(d)) { placeBar(0); return; }
  const f = Math.min(1, t / d);
  placeBar(f);
  if (vid.paused || vid.seeking || vid.readyState < 3 || pl.classList.contains("hide") || pl.classList.contains("hidden") || !pl.animate) return;
  const o = {duration: Math.max(1, (d - t) * 1000 / (vid.playbackRate || 1)), easing: "linear", fill: "forwards"};
  barAnims = [byId("pFill").animate([{transform: "scaleX(" + f + ")"}, {transform: "scaleX(1)"}], o),
              byId("pKpos").animate([{transform: "translate3d(" + f * 100 + "%,0,0)"}, {transform: "translate3d(100%,0,0)"}], o)];
}
function paintTimes(t, force) {  // (elapsed and left beside the bar, and the slider's numbers: only when the second shown changes)
  const d = vid.duration > 0 && isFinite(vid.duration) ? Math.floor(vid.duration) : 0, s = Math.floor(t || 0);
  if (s === shownSec && !force) return;
  shownSec = s;
  byId("pCur").textContent = fmtTime(s);
  byId("pLeft").textContent = d ? "-" + fmtTime(Math.max(0, d - s)) : "";
  psc.setAttribute("aria-valuenow", s);
  psc.setAttribute("aria-valuetext", fmtTime(s) + " of " + fmtTime(d));
}
function paintDur() {  // (the length: the slider's top, and both times as wide as it written out (with the "-"): the bar never shifts)
  const d = vid.duration > 0 && isFinite(vid.duration) ? vid.duration : 0, w = (fmtTime(d).length + 1) + "ch";
  psc.setAttribute("aria-valuemax", Math.floor(d));
  if (psc.parentNode.style.getPropertyValue("--tw") !== w) psc.parentNode.style.setProperty("--tw", w);
  paintTimes(vid.currentTime, true);
}
function paintBuf() {  // (one light bar per buffered range, reused)
  if (!playing || pl.classList.contains("hide") || pl.classList.contains("hidden")) return;
  const trk = byId("pTrk"), d = vid.duration, b = vid.buffered;
  if (!(d > 0) || !isFinite(d)) return;
  for (let n = trk.querySelectorAll(".buf").length; n < b.length; n++) { const i = document.createElement("i"); i.className = "buf"; trk.insertBefore(i, byId("pFill")); }
  trk.querySelectorAll(".buf").forEach((el, i) => {
    el.style.transform = i < b.length ? "translateX(" + (b.start(i) / d * 100) + "%) scaleX(" + ((b.end(i) - b.start(i)) / d) + ")" : "scaleX(0)";
  });
}
function measureScrub() {  // (the scrubber's width and its left in the frame's own terms: layout numbers, which a turned frame doesn't change)
  trkW = psc.offsetWidth; trkX = 0;
  for (let el = psc; el && el !== pframe; el = el.offsetParent) trkX += el.offsetLeft;
}
if (window.ResizeObserver) new ResizeObserver(() => { if (!pl.classList.contains("hidden")) measureScrub(); }).observe(psc);

let seekMine = false;  // (the next 'seeking' is the user's, not the player's own nudge)
function seekTo(t) {
  if (!playing || !(vid.duration > 0)) return;
  t = Math.max(0, Math.min(vid.duration - .25, t));
  if (Math.abs(t - vid.currentTime) < .05 && !vid.seeking) return;
  seekMine = true;
  vid.currentTime = t;
}
function togglePlay() {
  if (!playing || uiState === "error") return;
  if (uiState === "end" || vid.ended) { seekTo(0); vid.play().catch(() => {}); return; }
  if (!vid.paused) { vid.pause(); return; }
  if (!playing.started) { setUi("start"); setBusy("start"); }  // (its first play, a tap away: the start's wait)
  vid.play().catch(() => {});
}
function pieceIndex(t) {  // (the piece at t, as hls.js lists them; -1 without a list)
  const fr = hls && hls.levels && hls.levels[0] && hls.levels[0].details ? hls.levels[0].details.fragments : null;
  if (!fr || !fr.length) return -1;
  let lo = 0, hi = fr.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (fr[m].start <= t) lo = m; else hi = m - 1; }
  return lo;
}
function isBuffered(t) { for (let i = 0; i < vid.buffered.length; i++) if (vid.buffered.start(i) <= t && t < vid.buffered.end(i)) return true; return false; }
function snap(t) {
  const i = isBuffered(t) ? -1 : pieceIndex(t);
  if (i < 0) return t;
  const f = hls.levels[0].details.fragments[i], end = f.start + f.duration;
  return end - t < 0.35 && end < vid.duration - 1 ? end + 0.02 : t;
}
let hintAt = 0;
function hint(t) {  // (the reader may ready the piece at t before the player asks: GET /hls/<token>/h<n>?r=<reader>, 3 a second at most,
  const i = !playing || isBuffered(t) ? -1 : pieceIndex(t), now = performance.now();
  if (i < 0 || now - hintAt < 334) return;
  hintAt = now;
  const m = /^(.*\/hls\/[^/]+\/)[^/?]+\.m3u8\?r=(\d+)/.exec(hls.levels[0].details.url || "");
  if (m) fetch(m[1] + "h" + i + "?r=" + m[2]).catch(() => {});
}
let jumpTo = 0, jumpT = null, jumpAt = -1e9;
function jump(d) {  // (±10 s: the first seeks at once; more within 400 ms of a seek add up, applied 150 ms after the last)
  if (!(vid.duration > 0)) return;
  jumpTo = Math.max(0, Math.min(vid.duration - .25, (jumpT ? jumpTo : vid.currentTime) + d));
  clearTimeout(jumpT);
  jumpT = null;
  if (performance.now() - jumpAt > 400) { applyJump(); return; }
  jumpT = setTimeout(applyJump, 150);
  hint(jumpTo);
}
function applyJump() { jumpT = null; jumpAt = performance.now(); seekTo(jumpTo); }
let ripT = 0, ripN = 0, ripSide = null;
function skipBy(d, p) {  // (±10 s with an arc of light on that side and the seconds added up; p: where the finger was)
  if (!playing || !(vid.duration > 0) || uiState === "error") return;
  const side = d < 0 ? "l" : "r", el = byId(side === "l" ? "pRipL" : "pRipR"), btn = byId(d < 0 ? "pB10" : "pF10");
  if (ripSide !== side) { ripN = 0; byId(side === "l" ? "pRipR" : "pRipL").classList.remove("on"); }
  ripSide = side; ripN += Math.abs(d); ripOn = true;
  el.querySelector(".n").textContent = ripN + " seconds";
  el.classList.add("on");
  if (p) {  // (a double tap: the ripple alone, as on YouTube)
    hideCtl(false);
    const c = document.createElement("i");
    c.className = "tapc"; c.style.left = p.x + "px"; c.style.top = p.y + "px";
    pframe.appendChild(c);
    c.addEventListener("animationend", () => c.remove());
    setTimeout(() => c.remove(), 700);
  }
  clearTimeout(ripT); ripT = setTimeout(ripOff, 600);
  btn.classList.add("turn"); setTimeout(() => btn.classList.remove("turn"), 110);
  jump(d);
}
function ripOff() { clearTimeout(ripT); byId("pRipL").classList.remove("on"); byId("pRipR").classList.remove("on"); ripOn = false; ripN = 0; ripSide = null; }

function fracAt(e) { const p = gLocal(e, scrub.r); return Math.max(0, Math.min(1, (p.x - trkX) / (trkW || 1))); }
psc.addEventListener("pointerdown", (e) => {
  if (!playing || !(vid.duration > 0) || e.button > 0 || uiState === "error" || e.timeStamp - openTs < 500) return;
  e.preventDefault();
  measureScrub();
  scrub.r = pframe.getBoundingClientRect();
  try { psc.setPointerCapture(e.pointerId); } catch (x) {}
  scrub.on = true; scrub.from = vid.currentTime; scrub.resume = !vid.paused; scrub.pv = scrub.want = null;
  stopBar();
  if (scrub.resume) vid.pause();
  psc.classList.add("on");
  showCtl(0);
  scrubTo(fracAt(e) * vid.duration);
});
psc.addEventListener("pointermove", (e) => {
  if (scrub.on) {
    scrub.f = fracAt(e);
    if (!scrub.raf) scrub.raf = requestAnimationFrame(() => { scrub.raf = 0; if (scrub.on) scrubTo(scrub.f * vid.duration); });
  } else if (e.pointerType === "mouse" && playing && vid.duration > 0) {
    if (!scrub.r) { measureScrub(); scrub.r = pframe.getBoundingClientRect(); }
    psc.classList.add("hov");
    bubble(fracAt(e) * vid.duration);
  }
});
psc.addEventListener("pointerup", () => { if (scrub.on) scrubEnd(true); });
psc.addEventListener("pointercancel", () => { if (scrub.on) scrubEnd(false); });
psc.addEventListener("pointerleave", () => { psc.classList.remove("hov"); if (!scrub.on) scrub.r = null; });
function scrubTo(t) {
  scrub.t = t;
  placeBar(t / vid.duration); bubble(t); paintTimes(t); preview(t);
  clearTimeout(scrub.dwellT); scrub.dwellT = setTimeout(() => hint(scrub.t), 250);  // (resting: the fleet readies that place)
}
function scrubEnd(commit) {
  clearTimeout(scrub.dwellT);
  if (scrub.raf) { cancelAnimationFrame(scrub.raf); scrub.raf = 0; if (commit) scrubTo(scrub.f * vid.duration); }
  scrub.on = false; scrub.r = null; scrub.pv = scrub.want = null;
  psc.classList.remove("on", "hov");
  if (commit) seekTo(scrub.t); else if (Math.abs(vid.currentTime - scrub.from) > .5) seekTo(scrub.from);  // (cancelled: back where it was)
  if (scrub.resume) vid.play().catch(() => {});
  aimBar(); paintTimes(vid.currentTime);
  showCtl();
}
function preview(t) {  // (one preview seek at a time, the next one queued on 'seeked')
  if (!isBuffered(t)) return;
  if (scrub.pv !== null) { scrub.want = t; return; }
  scrub.pv = t;
  if (vid.fastSeek) vid.fastSeek(t); else vid.currentTime = t;
}
function bubble(t) {
  const d = vid.duration || 1, x = Math.max(30, Math.min(trkW - 30, t / d * trkW));
  byId("pBub").textContent = fmtTime(t);
  byId("pBpos").style.transform = "translate3d(" + x + "px,0,0)";
}
const scrubbing = () => scrub.on || !!(gest && gest.kind === "seek");

const GX = {tapMs: 280, dblMs: 280, chainMs: 700, lockPx: 12, edgeL: 24, edgeR: 16, edgeT: 20, edgeB: 28, pullPx: 56, pullV: .45,
            exitPx: 80, exitV: .5, a: .25, b: .0015, span: .6, dwell: 250};
const ptrs = new Map();
let moveRaf = 0, moveArgs = null;
function gLocal(e, r) {  // (a pointer in the frame's own terms; turned a quarter, its x runs down the screen and its y in from the right edge)
  r = r || pframe.getBoundingClientRect();
  if (pl.classList.contains("m-rot")) return {x: e.clientY - r.top, y: r.right - e.clientX, w: r.height, h: r.width};
  return {x: e.clientX - r.left, y: e.clientY - r.top, w: r.width, h: r.height};
}
const zoneOf = (p) => (p.x < p.w * .35 ? "l" : p.x > p.w * .65 ? "r" : "c");
pgest.addEventListener("pointerdown", (e) => {
  if (nativePlayer() || e.pointerType === "mouse" || !playing) return;
  touchAt = performance.now();
  if (e.timeStamp - openTs < 500 || e.timeStamp === swallowT) return;  // (the second tap of the one that opened the player; a tap that closed a menu)
  if (e.clientX < GX.edgeL || e.clientX > innerWidth - GX.edgeR || e.clientY < GX.edgeT || e.clientY > innerHeight - GX.edgeB) return;  // (the system's edges)
  const r = pframe.getBoundingClientRect(), p = gLocal(e, r);
  ptrs.set(e.pointerId, p);
  try { pgest.setPointerCapture(e.pointerId); } catch (x) {}
  if (ptrs.size === 2) { if (gest && gest.kind) gEnd(gest, false); gest = null; clearTimeout(tapT); tapT = 0; pinch = {d0: pinchSpread(), z0: zoomed, r: r}; return; }
  if (ptrs.size > 2) return;
  gest = {id: e.pointerId, x0: p.x, y0: p.y, p: p, r: r, t0: e.timeStamp, kind: null, pts: [[e.timeStamp, p.x, p.y]], zone: zoneOf(p)};
});
pgest.addEventListener("pointermove", (e) => {
  if (!ptrs.has(e.pointerId)) return;
  const g = gest && gest.id === e.pointerId ? gest : null, p = gLocal(e, g ? g.r : pinch ? pinch.r : null);
  ptrs.set(e.pointerId, p);
  if (pinch) { pinchMove(); return; }
  if (!g) return;
  const dx = p.x - g.x0, dy = p.y - g.y0;
  g.p = p; g.pts.push([e.timeStamp, p.x, p.y]); if (g.pts.length > 6) g.pts.shift();
  if (!g.kind) {
    if (Math.hypot(dx, dy) < GX.lockPx) return;
    const ax = Math.abs(dx) > Math.abs(dy) * 1.2 ? "x" : Math.abs(dy) > Math.abs(dx) * 1.2 ? "y" : null;
    if (!ax) return;
    clearTimeout(tapT); tapT = 0;
    gBegin(g, ax);
  }
  moveArgs = [g, dx, dy];
  if (!moveRaf) moveRaf = requestAnimationFrame(() => { moveRaf = 0; const a = moveArgs; moveArgs = null; if (a && a[0] === gest) gMove(a[0], a[1], a[2]); });  // (one write a frame)
});
function flushMove() { if (!moveRaf) return; cancelAnimationFrame(moveRaf); moveRaf = 0; const a = moveArgs; moveArgs = null; if (a && a[0] === gest) gMove(a[0], a[1], a[2]); }
function gUp(e, ok) {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.delete(e.pointerId);
  if (pinch) { if (ptrs.size < 2) pinch = null; gest = null; return; }  // (the finger left after a pinch is no tap)
  if (!gest || gest.id !== e.pointerId) return;
  flushMove();
  const t = gest;
  gest = null;
  if (t.kind) gEnd(t, ok); else if (ok && e.timeStamp - t.t0 < GX.tapMs) gTap(t, e.timeStamp);
}
pgest.addEventListener("pointerup", (e) => gUp(e, true));
pgest.addEventListener("pointercancel", (e) => gUp(e, false));
["gesturestart", "gesturechange"].forEach((n) => pframe.addEventListener(n, (e) => { if (!nativePlayer()) e.preventDefault(); }));
pgest.addEventListener("click", () => { if (!nativePlayer() && playing && performance.now() - touchAt > 600) { togglePlay(); showCtl(); } });
pgest.addEventListener("dblclick", () => { if (!nativePlayer() && playing && performance.now() - touchAt > 600) toggleFull(); });
function gBegin(t, ax) {
  const full = pl.classList.contains("m-full");
  if (ax === "x") t.kind = "seek";
  else if (!full) t.kind = "pull";
  else t.kind = t.x0 < t.p.w * .4 ? "vol" : t.x0 > t.p.w * .6 ? "bright" : (imm || fsEl()) ? "exit" : "none";
  if (t.kind === "seek") {
    if (!(vid.duration > 0) || uiState === "error") { t.kind = "none"; return; }
    t.from = scrub.t = vid.currentTime; t.resume = !vid.paused; scrub.pv = scrub.want = null;
    stopBar();
    if (t.resume) vid.pause();
    pl.classList.add("gest", "gx");
    showCtl(0);
    hud(hudSeekHtml(0, t.from), true);
  } else if (t.kind === "vol") {
    if (canVol) { t.v0 = vid.muted ? 0 : vid.volume; pl.classList.add("gest"); hudLevel("vol"); }
    else { t.kind = "none"; hudText(GLYPH.spk2, "Use the volume buttons"); }  // (iOS: a page can't set the volume, nor tap these streams' sound)
  } else if (t.kind === "bright") { t.b0 = bright; pl.classList.add("gest"); hudLevel("bright"); }
  else if (t.kind === "pull" || t.kind === "exit") pframe.style.transition = "none";
}
function gMove(t, dx, dy) {
  if (t.kind === "seek") {
    const d = Math.sign(dx) * (GX.a * Math.abs(dx) + GX.b * dx * dx);  // (fine near the start, faster the further: 100 px ≈ 40 s, 400 px ≈ 5:40)
    scrub.t = Math.max(0, Math.min(vid.duration - 1, t.from + d));
    hudSeek(scrub.t - t.from, scrub.t); placeBar(scrub.t / vid.duration); paintTimes(scrub.t); preview(scrub.t);
    clearTimeout(scrub.dwellT); scrub.dwellT = setTimeout(() => hint(scrub.t), GX.dwell);
  } else if (t.kind === "pull") pullTo(dy * .35);
  else if (t.kind === "exit") pullTo(Math.max(0, dy) * .5);
  else if (t.kind === "vol") { setVol(t.v0 - dy / (t.p.h * GX.span)); hudFill(vid.muted ? 0 : vid.volume); }
  else if (t.kind === "bright") {
    const was = bright < .5;
    bright = Math.max(.15, Math.min(1, t.b0 - dy / (t.p.h * GX.span)));
    byId("pDim").style.opacity = String(1 - bright);
    if (was !== bright < .5) hudLevel("bright"); else hudFill((bright - .15) / .85);
  }
}
function gEnd(t, ok) {
  clearTimeout(scrub.dwellT);
  pl.classList.remove("gest", "gx");
  const a = t.pts[0], b = t.pts[t.pts.length - 1], v = (b[2] - a[2]) / Math.max(1, b[0] - a[0]), dy = t.p.y - t.y0;  // (px a ms, down)
  if (t.kind === "seek") {
    hudOff(120);
    scrub.pv = scrub.want = null;
    if (ok && Math.abs(scrub.t - t.from) >= 2 && Math.abs(t.p.x - t.x0) >= 16) seekTo(scrub.t);  // (one seek; back where it began: none)
    else if (Math.abs(vid.currentTime - t.from) > .5) seekTo(t.from);
    if (t.resume) vid.play().catch(() => {});
    aimBar(); paintTimes(vid.currentTime);
    showCtl();
  } else if (t.kind === "vol" || t.kind === "bright") {
    hudOff(600);
    if (t.kind === "vol") setVol(vid.muted ? 0 : vid.volume, true);
  } else if (t.kind === "pull") {
    if (ok && (Math.abs(dy) > GX.pullPx || Math.abs(v) > GX.pullV)) { pullTo(0, true); enterFull(); } else springBack();
  } else if (t.kind === "exit") {
    if (ok && (dy > GX.exitPx || v > GX.exitV)) { pullTo(0, true); exitFull(); } else springBack();
  }
}
function gTap(t, now) {
  if (t.t0 === swallowT) return;
  const z = t.zone, p = t.p;
  if (tapChain && now - tapChain.t < GX.chainMs && tapChain.z === z && z !== "c") { tapChain.t = now; skipBy(z === "l" ? -10 : 10, p); return; }  // (more taps after a double tap)
  if (lastTap && now - lastTap.t < GX.dblMs && lastTap.z === z && z !== "c") {
    clearTimeout(tapT); tapT = 0; lastTap = null; tapChain = {t: now, z: z};
    skipBy(z === "l" ? -10 : 10, p);
    return;
  }
  lastTap = {t: now, z: z};
  tapChain = null;
  if (z === "c") { toggleCtl(); return; }  // (the middle answers at once: no double tap there)
  clearTimeout(tapT);
  tapT = setTimeout(() => { tapT = 0; toggleCtl(); }, GX.dblMs);  // (a side waits to see if a second tap comes)
}
function pullTo(y, instant) {  // (the picture follows a pull or an exit swipe: a transform in the frame's own terms)
  if (instant) pframe.style.transition = "none";
  pframe.style.transform = y ? (pl.classList.contains("m-rot") ? "rotate(90deg) translate3d(0,-100%,0) " : "") + "translate3d(0," + y + "px,0)" : "";
}
function springBack() { pframe.style.transition = "transform 360ms var(--p-spring)"; pullTo(0); setTimeout(() => { pframe.style.transition = ""; }, 380); }
function pinchSpread() { const v = Array.from(ptrs.values()); return v.length < 2 ? 1 : Math.hypot(v[0].x - v[1].x, v[0].y - v[1].y) || 1; }
function pinchMove() {
  const s = pinchSpread() / pinch.d0;
  if (s > 1.12 && !zoomed && pl.classList.contains("m-full")) setZoom(true);
  else if (s < .88 && zoomed) setZoom(false);
  else if (s < .8 && !pinch.z0 && !zoomed && (imm || fsEl()) && !pinch.done) { pinch.done = true; exitFull(); }  // (pinched in, fitted: out of full screen)
}
function setZoom(on) {  // (iOS's zoom: the picture fills the screen and loses its edges; a transform, so it animates)
  zoomed = on;
  const w = pframe.offsetWidth, h = pframe.offsetHeight, vw = vid.videoWidth || 16, vh = vid.videoHeight || 9;
  vid.style.transform = on ? "scale(" + (Math.max(w / vw, h / vh) / Math.min(w / vw, h / vh)).toFixed(3) + ")" : "";
}

let hudT = 0;
function hud(html, big) { const h = byId("pHud"); clearTimeout(hudT); h.classList.toggle("big", !!big); h.innerHTML = html; h.classList.add("on"); hudOn = true; pl.classList.add("hudon"); }
function hudOff(ms) {
  clearTimeout(hudT);
  const off = () => { byId("pHud").classList.remove("on"); hudOn = false; pl.classList.remove("hudon"); };
  if (ms) hudT = setTimeout(off, ms); else off();
}
function hudFill(f) { const i = byId("pHud").querySelector(".hb i"); if (i) i.style.transform = "scaleX(" + Math.max(0, Math.min(1, f)).toFixed(3) + ")"; }
function spkIcon(v) { return GLYPH["spk" + (v === 0 ? 0 : v < .34 ? 1 : v < .67 ? 2 : 3)]; }
function hudLevel(k) {
  const v = k === "vol" ? (vid.muted ? 0 : vid.volume) : (bright - .15) / .85;
  hud((k === "vol" ? spkIcon(v) : GLYPH[bright < .5 ? "sunLow" : "sun"]) + '<div class="hb"><i></i></div>');
  hudFill(v);
}
function hudText(icon, words) { hud(icon + "<b>" + esc(words) + "</b>"); hudOff(1400); }
const signed = (d) => (d < 0 ? "−" : "+") + fmtTime(Math.abs(d));
function hudSeekHtml(d, t) { return "<b>" + signed(d) + "</b><small>" + fmtTime(t) + " · " + fmtTime(vid.duration) + "</small>"; }
function hudSeek(d, t) { const h = byId("pHud"), b = h.querySelector("b"), s = h.querySelector("small"); if (b) b.textContent = signed(d); if (s) s.textContent = fmtTime(t) + " · " + fmtTime(vid.duration); }

function fsEl() { return document.fullscreenElement || document.webkitFullscreenElement || null; }
let vpW = 0, vpH = 0, vpSet = "";  // (the viewport, read in a frame (at start, on a resize): never in a tap's task, where it would force a layout)
requestAnimationFrame(() => { vpW = innerWidth; vpH = innerHeight; });
function playerLayout() {
  if (pl.classList.contains("hidden")) { layoutDirty = true; return; }
  layoutDirty = false;
  const full = !nativePlayer() && (landMQ.matches || imm || !!fsEl() || (mouseMQ.matches && vpW >= 760)), vp = vpW + "x" + vpH;
  pl.classList.toggle("m-full", full);
  pl.classList.toggle("m-inline", !full);
  pl.classList.toggle("m-rot", !nativePlayer() && imm && !landMQ.matches);
  const upn = byId("pUpn"), dock = byId("pDock");  // (Up Next: inline it's the dock's card, by the version playing; else over the picture,
  if (!full) { if (upn.parentNode !== dock) dock.insertBefore(upn, byId("pVerBox")); }
  else if (upn.parentNode !== pframe) pframe.insertBefore(upn, byId("pMenu"));
  upn.classList.toggle("upline", full && (imm && !landMQ.matches ? vpW : vpH) <= 500);
  if (vp !== vpSet) { vpSet = vp; pl.style.setProperty("--vw", vpW + "px"); pl.style.setProperty("--vh", vpH + "px"); }  // (the turned frame's size:
  if (!full && zoomed) setZoom(false);
  closeMenu();
}
onMedia(landMQ, () => { if (imm && wasWide && !landMQ.matches) imm = false; wasWide = landMQ.matches; playerLayout(); paintFull(); });  // (turned upright after
let layoutRaf = 0;
window.addEventListener("resize", () => {
  if (layoutRaf) return;
  layoutRaf = requestAnimationFrame(() => { layoutRaf = 0; vpW = innerWidth; vpH = innerHeight; playerLayout(); });
}, {passive: true});
function enterFull() {  // (the player's own full screen where the browser has one (desktop, iPad, Android); an iPhone gets the immersive mode)
  if (nativePlayer()) { enterNativeFullscreen(false); return; }
  if (canFS) {
    const f = pl.requestFullscreen || pl.webkitRequestFullscreen;
    try {
      const r = f.call(pl, {navigationUI: "hide"});
      if (r && r.then) r.then(syncLandscape, () => {});
      else syncLandscape();
    } catch (e) {}
    return;
  }
  imm = true; wasWide = landMQ.matches;
  playerLayout(); paintFull(); showCtl();
  if (!reduced.matches && pframe.animate) pframe.animate([{opacity: 0}, {opacity: 1}], {duration: 220, easing: "cubic-bezier(.22,1,.36,1)"});  // (the turn itself isn't
}
function exitFull() {
  releaseLandscape();
  if (vid.webkitDisplayingFullscreen && vid.webkitExitFullscreen) { try { vid.webkitExitFullscreen(); } catch (e) {} return; }
  if (vid.webkitPresentationMode === "fullscreen" && vid.webkitSetPresentationMode) { try { vid.webkitSetPresentationMode("inline"); } catch (e) {} return; }
  if (fsEl()) { const x = document.exitFullscreen || document.webkitExitFullscreen; try { const r = x.call(document); if (r && r.catch) r.catch(() => {}); } catch (e) {} return; }
  if (imm) { imm = false; playerLayout(); paintFull(); showCtl(); }
}
function toggleFull() { if (nativeFullscreen() || fsEl() || imm) exitFull(); else enterFull(); }
["fullscreenchange", "webkitfullscreenchange"].forEach((n) => document.addEventListener(n, () => { pl.classList.toggle("fs", fsEl() === pl); syncLandscape(); playerLayout(); paintFull(); if (playing) showCtl(); }));
function paintFull() {  // (the full screen buttons and Back say what they'll do)
  const on = nativeFullscreen() || !!fsEl() || imm;
  ["pFs", "pFs2"].forEach((id) => { const b = byId(id); b.innerHTML = on ? GLYPH.fsExit : GLYPH.fsEnter; b.setAttribute("aria-label", on ? "Exit full screen" : "Full screen"); });
  byId("pBack").innerHTML = imm ? GLYPH.down : ICON.left;
  byId("pBack").setAttribute("aria-label", imm ? "Exit full screen" : "Back");
  byId("pNativeFullscreen").innerHTML = (on ? GLYPH.fsExit : GLYPH.fsEnter) + (on ? "Exit full screen" : "Full screen");
  if (on) nativeNotice("");
}
["webkitbeginfullscreen", "webkitendfullscreen"].forEach((name) => vid.addEventListener(name, () => { savePos(true); syncLandscape(); paintFull(); }));
document.addEventListener("visibilitychange", () => { if (document.hidden) releaseLandscape(); else syncLandscape(); });

function openMenu(kind, btn, keys) {
  const m = byId("pMenu");
  m.innerHTML = kind === "audio" ? audioItems() : moreItems();
  m.setAttribute("aria-label", kind === "audio" ? "Audio" : "More");
  let x = 0, y = 0;
  for (let el = btn; el && el !== pframe; el = el.offsetParent) { x += el.offsetLeft; y += el.offsetTop; }
  m.style.right = Math.max(12, pframe.offsetWidth - x - btn.offsetWidth) + "px";
  m.style.bottom = (pframe.offsetHeight - y + 8) + "px";
  m.style.maxHeight = Math.max(120, y - 20) + "px";  // (a short window: it scrolls, the last tracks too)
  menuOpen = kind; menuBtn = btn;
  btn.setAttribute("aria-expanded", "true");
  m.classList.add("open");
  showCtl(0);
  const on = m.querySelector(".mi.on");
  if (on) m.scrollTop = Math.max(0, on.offsetTop - (m.clientHeight - on.offsetHeight) / 2);
  if (keys) { const b = on || m.querySelector("button"); if (b) b.focus({preventScroll: true}); }
}
function closeMenu() {
  if (!menuOpen) return;
  const m = byId("pMenu");
  if (m.contains(document.activeElement) && menuBtn) menuBtn.focus({preventScroll: true});
  if (menuBtn) menuBtn.setAttribute("aria-expanded", "false");
  menuOpen = null; menuBtn = null;
  m.classList.remove("open");
  if (playing) showCtl();
}
function menuKey(e) {  // (↑/↓ move between the items, Home/End to the ends, Tab leaves; the film's keys wait while it's open)
  const items = Array.from(byId("pMenu").querySelectorAll("button")), i = items.indexOf(document.activeElement), k = e.key;
  if (k === "Tab") { closeMenu(); return; }
  if (k === "ArrowDown" || k === "ArrowUp" || k === "Home" || k === "End") {
    e.preventDefault();
    const n = k === "Home" ? 0 : k === "End" ? items.length - 1 : i < 0 ? (k === "ArrowDown" ? 0 : items.length - 1) : (i + (k === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    const b = items[n], m = byId("pMenu");
    if (!b) return;
    b.focus({preventScroll: true});
    if (b.offsetTop < m.scrollTop) m.scrollTop = b.offsetTop - 6; else if (b.offsetTop + b.offsetHeight > m.scrollTop + m.clientHeight) m.scrollTop = b.offsetTop + b.offsetHeight - m.clientHeight + 6;
    return;
  }
  if (k === " " || k === "Enter") return;
  if (k.length === 1 || k.indexOf("Arrow") === 0) e.preventDefault();
}
function trackNames(ts) {  // (each track as its language and the rest; the " ·" the fleet adds to tell twins apart dropped, twins numbered)
  const seen = {};
  return ts.map((t) => {
    let s = String(t.name || "").trim();
    while (s.endsWith("·")) s = s.slice(0, -1).trim();
    const k = s.indexOf(" · "), main = k > 0 ? s.slice(0, k) : s, rest = k > 0 ? s.slice(k + 3) : "", id = main + "|" + rest;
    seen[id] = (seen[id] || 0) + 1;
    return [main, rest + (seen[id] > 1 ? (rest ? " · " : "") + "track " + (t.i + 1) : "")];
  });
}
function audioItems() {  // (each sound track: its language, the rest of it small on the right, a check on the one playing)
  const ts = langTracks(), names = trackNames(ts);
  return '<div class="h">Audio</div>' + ts.map((t, n) => '<button class="mi' + (t.on ? " on" : "") + '" role="menuitemradio" aria-checked="' + t.on + '" data-i="' + t.i + '">' +
    ICON.check.replace("<svg ", '<svg class="ck" ') + '<span class="tx">' + esc(names[n][0]) + "</span>" + (names[n][1] ? "<small>" + esc(names[n][1]) + "</small>" : "") + "</button>").join("");
}
function moreItems() {
  return '<button class="mi" role="menuitem" data-act="copy">' + GLYPH.link + '<span class="tx">Copy stream link</span></button>' +
    '<button class="mi" role="menuitem" data-act="versions">' + GLYPH.versions + '<span class="tx">Streams</span></button><hr><div class="h">Speed</div><div class="speeds">' +
    [0.75, 1, 1.25, 1.5, 2].map((r) => '<button role="menuitemradio" aria-checked="' + (r === rate) + '" class="' + (r === rate ? "on" : "") + '" data-rate="' + r + '">' + r + "×</button>").join("") + "</div>";
}
byId("pMenu").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.rate) {  // (the menu stays: another speed is one tap away; for this page's visit, not kept)
    rate = Number(b.dataset.rate);
    vid.defaultPlaybackRate = vid.playbackRate = rate;
    b.parentNode.querySelectorAll("button").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", String(x === b)); });
    return;
  }
  if (b.dataset.i) setLang(Number(b.dataset.i));
  else if (b.dataset.act === "copy") copyLink();
  closeMenu();
  if (b.dataset.act === "versions") leavePlayer();
});
document.addEventListener("pointerdown", (e) => {  // (a press outside an open menu closes it and does nothing else)
  swallowAt = -1e9;
  if (menuOpen && !(e.target.closest && e.target.closest("#pMenu, #pAudio, #pMore"))) { closeMenu(); swallowAt = performance.now(); swallowT = e.timeStamp; }
}, {capture: true, passive: true});
pl.addEventListener("click", (e) => {  // (nor does the second tap of the one that opened the player)
  const now = performance.now();
  if (e.timeStamp - openTs < 500 || now - swallowAt < 500) { e.stopPropagation(); e.preventDefault(); swallowAt = -1e9; }
}, true);

function langTracks() {
  if (hls) return hls.audioTracks.map((t, i) => ({i: i, name: t.name || t.lang || "Track " + (i + 1), lang: t.lang || "", on: i === hls.audioTrack}));
  const list = vid.audioTracks, out = [];
  for (let i = 0; list && i < list.length; i++) out.push({i: i, name: list[i].label || list[i].language || "Track " + (i + 1), lang: list[i].language || "", on: list[i].enabled});
  return out;
}
function pickLang(i) {
  if (hls) hls.audioTrack = i;
  else if (vid.audioTracks) for (let k = 0; k < vid.audioTracks.length; k++) vid.audioTracks[k].enabled = k === i;
}
function showLangs() {  // (two tracks or more: the Audio button over the picture and the dock's capsule, its short name on them)
  if (!live()) return;
  const ts = langTracks(), want = store.get("lang", null), two = ts.length > 1;
  byId("pAudio").classList.toggle("hidden", !two);
  byId("dAudio").classList.toggle("hidden", !two);
  if (!two) { if (menuOpen === "audio") closeMenu(); return; }
  if (want && !langPicked) {
    langPicked = true;
    const t = ts.find((x) => x.name === want.name) || ts.find((x) => want.lang && x.lang === want.lang);
    if (t && !t.on) { pickLang(t.i); ts.forEach((x) => { x.on = x === t; }); }
  }
  const names = trackNames(ts), n = Math.max(0, ts.findIndex((t) => t.on));
  byId("pAudioName").textContent = names[n][0];
  byId("dAudioName").textContent = names[n][0];
  byId("pLang").innerHTML = ts.map((t, k) => '<option value="' + t.i + '"' + (k === n ? " selected" : "") + ">" + esc(names[k].filter(Boolean).join(" · ")) + "</option>").join("");
  if (menuOpen === "audio") {
    const m = byId("pMenu"), st = m.scrollTop, had = m.contains(document.activeElement);
    m.innerHTML = audioItems();
    m.scrollTop = st;
    if (had) { const b = m.querySelector(".mi.on"); if (b) b.focus({preventScroll: true}); }
  }
}
function setLang(i) {  // (picked by hand: remembered for the next film)
  const t = langTracks().find((x) => x.i === i);
  if (!t) return;
  pickLang(t.i);
  store.set("lang", {name: t.name, lang: t.lang});
  showLangs();
}
byId("pLang").onchange = (e) => setLang(Number(e.target.value));
function langOpen() { return !!menuOpen; }
function closeLang() { closeMenu(); }
if (vid.audioTracks) { vid.audioTracks.addEventListener("addtrack", showLangs); vid.audioTracks.addEventListener("change", showLangs); }

const canVol = (() => { try { const v0 = vid.volume; vid.volume = .5; const ok = Math.abs(vid.volume - .5) < .01; vid.volume = v0; return ok; } catch (e) { return false; } })();
const curVol = () => (vid.muted ? 0 : vid.volume);
function setVol(v, keep) {
  v = Math.max(0, Math.min(1, v));
  vid.volume = v;
  vid.muted = v === 0;
  if (keep && canVol) store.set("vol", {v: v, m: vid.muted});
}
function toggleMute(withHud) {
  vid.muted = !vid.muted;
  if (!vid.muted && vid.volume === 0) vid.volume = .5;
  if (canVol) store.set("vol", {v: vid.volume, m: vid.muted});
  if (withHud) hudText(spkIcon(curVol()), vid.muted ? "Muted" : "Sound on");
}
function paintVol() {
  const v = curVol();
  byId("pVolFill").style.transform = "scaleX(" + v + ")";
  byId("pMute").innerHTML = spkIcon(v);
  byId("pMute").setAttribute("aria-label", vid.muted ? "Unmute" : "Mute");
  byId("pVol").setAttribute("aria-valuenow", Math.round(v * 100));
}
const pvol = byId("pVol");
let volBox = null;
const volAt = (e) => Math.max(0, Math.min(1, (e.clientX - volBox.left) / (volBox.width || 1)));
pvol.addEventListener("pointerdown", (e) => { if (e.button > 0) return; e.preventDefault(); volBox = pvol.getBoundingClientRect(); try { pvol.setPointerCapture(e.pointerId); } catch (x) {} setVol(volAt(e)); showCtl(0); });
pvol.addEventListener("pointermove", (e) => { if (volBox) setVol(volAt(e)); });
["pointerup", "pointercancel"].forEach((n) => pvol.addEventListener(n, () => { if (!volBox) return; volBox = null; setVol(curVol(), true); showCtl(); }));
byId("pVolBox").addEventListener("wheel", (e) => { e.preventDefault(); setVol(curVol() - Math.sign(e.deltaY) * .05, true); showCtl(); }, {passive: false});
byId("pMute").onclick = () => toggleMute(false);
vid.addEventListener("volumechange", paintVol);
if (canVol) { const sv = store.get("vol", null); if (sv && typeof sv.v === "number") { vid.volume = Math.max(0, Math.min(1, sv.v)); vid.muted = !!sv.m; } }
byId("pVolBox").classList.toggle("hidden", !(canVol && hoverable));
paintVol();

let airplayAvailable = false, wasPip = false;
function canPictureInPicture() {
  if (document.pictureInPictureEnabled && typeof vid.requestPictureInPicture === "function") return true;
  try { return !!(vid.webkitSupportsPresentationMode && vid.webkitSupportsPresentationMode("picture-in-picture")); }
  catch (e) { return false; }
}
function paintNativeOptions() {
  const pip = canPictureInPicture();
  ["pPip", "pNativePip"].forEach((id) => byId(id).classList.toggle("hidden", !pip));
  ["pAir", "pNativeAirplay"].forEach((id) => byId(id).classList.toggle("hidden", !airplayAvailable));
}
function inPip() { return document.pictureInPictureElement === vid || vid.webkitPresentationMode === "picture-in-picture"; }
function togglePip() {
  const rejected = () => { const msg = "Start the video, then try Picture in Picture again."; if (nativePlayer()) nativeNotice(msg); else say(msg, true); };
  try {
    if (document.pictureInPictureElement) { document.exitPictureInPicture().catch(rejected); return; }
    if (vid.requestPictureInPicture && document.pictureInPictureEnabled) { vid.requestPictureInPicture().catch(rejected); return; }
    if (vid.webkitSetPresentationMode) vid.webkitSetPresentationMode(inPip() ? "inline" : "picture-in-picture");
    else rejected();
  } catch (e) { rejected(); }
}
function paintWhere() {  // (the picture is elsewhere: the frame says where)
  const pip = inPip(), air = !!vid.webkitCurrentPlaybackTargetIsWireless;
  const restore = wasPip && !pip && playing && pl.classList.contains("hidden");
  wasPip = pip;
  syncLandscape();
  pl.classList.toggle("pip", pip);
  pl.classList.toggle("air", air);
  byId("pPip").innerHTML = pip ? GLYPH.pipExit : GLYPH.pip;
  byId("pPip").setAttribute("aria-label", pip ? "Leave Picture in Picture" : "Picture in Picture");
  byId("pNativePip").innerHTML = (pip ? GLYPH.pipExit : GLYPH.pip) + (pip ? "Return to video" : "Picture in Picture");
  byId("pPipReturn").classList.toggle("hidden", !(playing && pip && pl.classList.contains("hidden")));
  byId("pWhere").innerHTML = pip ? GLYPH.pip + "Playing in Picture in Picture" : air ? GLYPH.airplay + "Playing on AirPlay" : "";
  paintNativeOptions();
  paintFull();
  if (restore) go("#/play");  // Keep the same media element and position when the system returns it.
}
["enterpictureinpicture", "leavepictureinpicture", "webkitpresentationmodechanged"].forEach((n) => vid.addEventListener(n, paintWhere));
byId("pPip").onclick = byId("pNativePip").onclick = togglePip;
byId("pPipReturn").onclick = () => { if (playing) { go("#/play"); if (inPip()) togglePip(); } };
if (typeof vid.webkitShowPlaybackTargetPicker === "function") {
  vid.addEventListener("webkitplaybacktargetavailabilitychanged", (e) => { airplayAvailable = e.availability === "available"; paintNativeOptions(); });
  vid.addEventListener("webkitcurrentplaybacktargetiswirelesschanged", paintWhere);
  byId("pAir").onclick = byId("pNativeAirplay").onclick = () => { try { vid.webkitShowPlaybackTargetPicker(); } catch (e) { if (nativePlayer()) nativeNotice("AirPlay is unavailable right now."); } };
}
paintNativeOptions();

function fillUpNext(n) {
  const c = byId("pUpn");
  if (c.dataset.id === n.id) return;
  c.dataset.id = n.id;
  loadImg(byId("pUpnImg"), via(n.thumbnail) || pic.bgSmall(playing.title.id), true);
  byId("pUpnT").textContent = "S" + n.season + ":E" + n.episode + ((n.name || n.title) ? " · " + (n.name || n.title) : "");
}
function stopCount() { clearTimeout(upnT); clearTimeout(stopCount.t); const c = byId("pUpn"); c.classList.remove("count"); c.querySelector("small").textContent = "Next episode"; }
function hideUpNext() { stopCount(); byId("pUpn").classList.add("hidden"); pl.classList.remove("upn"); }
function upnSeen() {  // (inline the card is in the dock: scrolled into view if the dock was elsewhere)
  if (!pl.classList.contains("m-inline")) return;
  const d = byId("pDock"), c = byId("pUpn").getBoundingClientRect(), r = d.getBoundingClientRect();
  if (c.top < r.top + 8 || c.bottom > r.bottom - 8) d.scrollTo({top: d.scrollTop + c.top - r.top - 16, behavior: smooth()});
}
function upNext() {  // ('timeupdate')
  const c = byId("pUpn");
  if (!playing || !playing.ep || c.classList.contains("count")) return;
  const n = nextEpisode(), d = vid.duration;
  const on = !!n && d > 300 && d - vid.currentTime <= 40 && upnOff !== playing.key && uiState !== "error" && !nexting;
  if (on) fillUpNext(n);
  if (on === !c.classList.contains("hidden")) return;
  c.classList.toggle("hidden", !on);
  pl.classList.toggle("upn", on);
  if (on) upnSeen();
}
function upNextEnd() {
  const n = nextEpisode();
  if (!n || !playing || upnOff === playing.key) return;
  fillUpNext(n);
  const c = byId("pUpn"), small = c.querySelector("small");
  c.classList.remove("hidden");
  stopCount();
  c.classList.add("count");
  pl.classList.add("upn");
  upnSeen();
  const t0 = performance.now(), tick = () => {  // ("Next episode in 8", 7…: the seconds in words, the fill in violet)
    const left = Math.ceil((8000 - (performance.now() - t0)) / 1000);
    small.textContent = "Next episode in " + Math.max(1, left);
    if (left > 1) stopCount.t = setTimeout(tick, 1000 - ((performance.now() - t0) % 1000));
  };
  tick();
  upnT = setTimeout(() => { if (playing && uiState === "end") playNext(); }, 8000);
}
byId("pUpnGo").onclick = () => { clearTimeout(upnT); playNext(); };
byId("pUpnX").onclick = () => { upnOff = playing && playing.key; hideUpNext(); };
function showRestart() {  // (a resume: "Start from the beginning" for 6 s, gone at any seek)
  const b = byId("pRestart");
  b.classList.remove("hidden");
  clearTimeout(restartT);
  restartT = setTimeout(() => b.classList.add("hidden"), 6000);
}
byId("pRestart").onclick = () => { byId("pRestart").classList.add("hidden"); seekTo(0); showCtl(); };

function mediaSession() {
  if (!("mediaSession" in navigator) || !playing || !window.MediaMetadata) return;
  const p = playing, t = p.title, ep = p.ep, ms = navigator.mediaSession, act = (n, f) => { try { ms.setActionHandler(n, f); } catch (e) {} };
  try {
    ms.metadata = new MediaMetadata({title: ep ? ep.name || "Episode " + ep.e : t.name || "", artist: ep ? t.name + " · S" + ep.s + ":E" + ep.e : String(t.year || ""), album: "Frame",
      artwork: [{src: new URL(ep && ep.thumb ? via(ep.thumb) : pic.bg(t.id), location.href).href, sizes: "1280x720", type: "image/jpeg"}]});
  } catch (e) {}
  act("play", () => { vid.play().catch(() => {}); });
  act("pause", () => vid.pause());
  act("seekbackward", () => jump(-10));
  act("seekforward", () => jump(10));
  act("seekto", (d) => { if (d && d.seekTime != null) seekTo(d.seekTime); });
  act("nexttrack", nextEpisode() ? () => playNext() : null);
}
function say(msg, bad) { if (imm || fsEl()) hudText(bad ? ICON.alert : ICON.check, msg); else toast(msg, bad); }  // (immersive: a toast would be sideways;
function copyLink() {  // (the stream's own address, to open elsewhere: Outplayer, VLC)
  if (!playing) return;
  const u = new URL(playing.url, location.href).href;
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(u).then(() => say("Stream link copied"), () => prompt("Copy the stream link:", u));
  else prompt("Copy the stream link:", u);
}
function leavePlayer() {  // (back to where it was opened: the episode's versions, the title; a second Back within 500 ms is the same one)
  const now = performance.now();
  if (now - backAt < 500) return;
  backAt = now;
  if (idx > 0) history.back(); else replaceHash("#/");
}
document.addEventListener("click", (e) => {  // (the second tap of a double tap on Back lands on the page under the player: nothing)
  if (performance.now() - backAt < 450 && pl.classList.contains("hidden")) { e.stopPropagation(); e.preventDefault(); }
}, true);

byId("pBack").onclick = () => { if (imm) exitFull(); else leavePlayer(); };  // (immersive: back to the picture at the top first)
byId("pNativeBack").onclick = leavePlayer;
byId("pNativeFullscreen").onclick = toggleFull;
byId("pNativePlay").onclick = () => { if (playing) { startPlay(playing); enterNativeFullscreen(true); } };
byId("pPlay").onclick = () => { togglePlay(); showCtl(); };
byId("pB10").onclick = () => { skipBy(-10); showCtl(); };
byId("pF10").onclick = () => { skipBy(10); showCtl(); };
byId("pFs").onclick = byId("pFs2").onclick = () => toggleFull();
byId("pAudio").onclick = (e) => { if (menuOpen === "audio") closeMenu(); else openMenu("audio", e.currentTarget, e.detail === 0); };  // (detail 0: from a key)
byId("pMore").onclick = (e) => { if (menuOpen === "more") closeMenu(); else openMenu("more", e.currentTarget, e.detail === 0); };
byId("pNextBtn").onclick = byId("dNext").onclick = () => playNext();
byId("dCopy").onclick = () => copyLink();
byId("pChange").onclick = byId("pOther").onclick = () => leavePlayer();
byId("pRetry").onclick = tryAgain;
pl.addEventListener("pointermove", (e) => {  // (a mouse that moves shows them)
  if (e.pointerType !== "mouse") return;
  const now = performance.now();
  if (!pl.classList.contains("hide") && now - wakeAt < 200) return;
  wakeAt = now;
  showCtl();
}, {passive: true});
pl.addEventListener("mouseleave", () => { clearTimeout(hideT); if (playing) hideT = setTimeout(autoHide, 600); });
byId("pCtl").addEventListener("pointerover", (e) => { overCtl = e.pointerType === "mouse" && !!(e.target.closest && e.target.closest("button, .pbot, .scrub, .vol")); });
byId("pCtl").addEventListener("pointerout", () => { overCtl = false; });
pl.addEventListener("mousedown", (e) => { if (!nativePlayer() && e.target.closest && e.target.closest("button")) e.preventDefault(); });
pl.addEventListener("focusin", () => { if (playing) showCtl(); });
pl.addEventListener("pointerdown", () => { kbd = false; }, {passive: true});  // (a pointer again: the controls may hide)

vid.addEventListener("loadedmetadata", () => { if (!live()) return; paintDur(); showLangs(); paintNativeOptions(); });
vid.addEventListener("durationchange", () => { if (live()) { paintDur(); aimBar(); } });
vid.addEventListener("seeking", () => {
  if (!live()) return;
  const t = vid.currentTime, s = snap(t);
  if (s !== t) { vid.currentTime = s; return; }  // (the nested 'seeking' does the rest)
  const mine = seekMine;
  seekMine = false;
  if (mine && playing.stopped && hls) { playing.stopped = false; httpFail = null; try { hls.startLoad(); } catch (e) {} }  // (elsewhere: load again)
  if (scrubbing() || uiState === "error") return;
  if (mine && hls && !isBuffered(t)) { try { hls.stopLoad(); hls.startLoad(t); } catch (e) {} }  // (a seek outside what's buffered: what's
  if (!mine && busyKind && busyKind !== "seek") return;  // (hls.js's own nudge inside a wait: the same wait)
  if (byId("pUpn").classList.contains("count")) stopCount();
  if (playing.started) { clearTimeout(restartT); byId("pRestart").classList.add("hidden"); }
  if (uiState !== "start") setUi("seek");
  setBusy(uiState === "start" ? "start" : "seek");
  stopBar(); placeBar(t / vid.duration); paintTimes(t);
});
vid.addEventListener("waiting", () => {
  if (live() && httpFail && uiState !== "error" && !scrubbing() && bufferedAhead() < .5) { failPlay(httpFail[0], httpFail[1], "http"); return; }  // (the
  if (!live() || scrubbing() || uiState === "error" || uiState === "start" || busyKind) return;  // (a wait already shown goes on: one wait, one text)
  setUi(vid.seeking ? "seek" : "buffer");
  setBusy(retrying ? "retry" : uiState);
  aimBar();
});
vid.addEventListener("seeked", () => {
  if (!live()) return;
  if (scrub.pv !== null) { scrub.pv = null; if (scrub.want !== null) { const w = scrub.want; scrub.want = null; preview(w); } return; }  // (a preview)
  settleUi();
  aimBar();
});
vid.addEventListener("canplay", () => { if (live()) settleUi(); });
vid.addEventListener("loadeddata", () => { if (live()) settleUi(); });
vid.addEventListener("playing", () => {
  const p = playing;
  if (!live()) return;
  pl.classList.add("live");  // (the cover fades out, once a title)
  byId("pSnap").classList.add("hidden");
  retrying = false;
  if (nexting) { nexting = false; pl.classList.remove("nexting"); }
  settleUi();
  aimBar();
  if (!p.started) {
    p.started = true;
    if (p.from > 0 && !p.why) showRestart();
    if (p.twinned) say("Playing the converted stream: " + p.twinned);
  }
});
vid.addEventListener("pause", () => {
  if (!live() || scrubbing()) return;
  savePos(true);
  if (uiState === "buffer" || uiState === "play") { setBusy(null); setUi("pause"); }
  else if (uiState === "start" || uiState === "seek") pl.classList.add("paused");
  aimBar();
});
vid.addEventListener("play", () => {
  if (!live() || scrubbing()) return;
  if (uiState === "pause" || uiState === "end") setUi("play"); else pl.classList.remove("paused");
  aimBar();
});
vid.addEventListener("ratechange", () => { if (live()) aimBar(); });
vid.addEventListener("progress", paintBuf);
vid.addEventListener("timeupdate", () => {
  if (!live()) return;
  savePos(false);
  upNext();
  if (!scrubbing() && !pl.classList.contains("hide")) paintTimes(vid.currentTime);
});
vid.addEventListener("ended", () => {
  if (!live()) return;
  savePos(true);
  setBusy(null);
  setUi("end");
  upNextEnd();
  aimBar();
});
vid.addEventListener("error", () => {  // (a file: once more from where it was, then the card; an Original in Safari's own player: see origFailed)
  const p = playing;
  if (!live() || hls || uiState === "error" || !vid.getAttribute("src")) return;
  if (/\/orig\.m3u8/.test(p.url)) { origFailed(p); return; }
  if (p.apple && /\/file\//.test(p.url) && !(vid.error && vid.error.code === 2 && !p.reloads)) {  // (a file listed for Apple's player that it
    const tok = p.url.split("/file/")[1].split("/")[0], why = markUnfit(p, "Safari couldn't play this file as it is", true);
    play(p.url.split("/file/")[0] + "/hls/" + tok + "/index.m3u8", "converted (the original can't play here)", p.file, p.info, p.title, p.ep, "switch");
    playing.twinned = why;
    return;
  }
  if (vid.error && vid.error.code === 2 && !p.reloads) {
    p.reloads = 1;
    const t = vid.currentTime;
    retrying = true;
    setBusy("retry", "", true);
    vid.src = p.url;
    if (t > 1) vid.addEventListener("loadedmetadata", () => { if (playing === p) vid.currentTime = t; }, {once: true});
    startPlay(p);
    return;
  }
  if (!twin()) failPlay("Couldn't play this stream", (vid.error && vid.error.code === 4 ? "The browser couldn't open it." : "The stream broke off.") + " Try again, or pick another stream.");
});

const scrolls = {};
let lastKey = null, lastView = null, lastHash = null, idx = 0, solidOn = null, tallOn = null, barSoft = false, navH = 0;
const VIEWS = ["v-home", "v-movies", "v-series", "v-library", "v-search", "v-title"];
function showView(el, on) {  // (a page shown, or put away: a tab's page is parked with its rendering kept, so coming back to it is a paint;
  const tab = el.id === "v-home" || el.id === "v-movies" || el.id === "v-series";
  el.classList.toggle("parked", !on && tab);
  el.classList.toggle("hidden", !on && !tab);
}
const TABS = ["home", "movies", "series", "library"];
const ROOT = {home: "#/", movies: "#/movies", series: "#/series", library: "#/library", search: "#/search"};
const ROOTKEY = {home: "home/home", movies: "home/movies", series: "home/series", library: "library", search: "search"};
const NAVTITLE = {home: "Frame", movies: "Movies", series: "Series", library: "Library", search: "Search"};
const tabTop = {};  // (each tab's page on top: where tapping the tab goes back to)
let navIntent = null, lastTab = null, playFrom = null;  // (playFrom: the page the player opened over)
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
function tabOfHash(h) {  // (the tab a root address is; null for a title or the player: theirs is where they were opened)
  const p = String(h || "").replace(/^#/, "").split("?")[0];
  if (p === "" || p === "/") return "home";
  if (p === "/movies" || p === "/series" || p === "/library" || p === "/search") return p.slice(1);
  return null;
}
function curTab() {
  const st = history.state;
  if (st && st.tab) return st.tab;
  return tabOfHash(location.hash) || (/^#\/title\/series\//.test(location.hash) ? "series" : /^#\/title\/movie\//.test(location.hash) ? "movies" : "home");
}
if (history.state && typeof history.state.i === "number") idx = history.state.i;
else history.replaceState({i: 0, tab: curTab()}, "");
function go(h) { if (location.hash !== h) location.hash = h; }
function replaceHash(h) { if (location.hash !== h) { history.replaceState(history.state, "", h); route(false); } }
function pushHash(h, tab) {  // (a new entry made here and routed in the same task: a tab switch shows in the tap's own frame, and the
  idx += 1;
  history.pushState({i: idx, from: lastHash, tab: tab}, "", h);
  route(false);
}
function rootOf(t) { return t === "search" ? (tabTop.searchRoot || ROOT.search) : ROOT[t]; }
function popTo(h, kind) {  // (back to a tab's root, as a pop: through history when the entry before is it, else in place)
  navIntent = {kind: kind || "pop", at: performance.now()};
  const from = ((history.state && history.state.from) || "").split("?")[0];
  if (idx > 0 && from === h.split("?")[0]) history.back(); else replaceHash(h);
}
function paintTabs() {  // (the tab this page belongs to, lit at once; its lozenge slides there; none on search)
  const t = lastTab, cap = document.querySelector("#tabbar .tb-cap");
  document.querySelectorAll("#tabbar [data-tab]").forEach((a) => {
    const on = a.dataset.tab === t;
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  cap.classList.toggle("none", TABS.indexOf(t) < 0);
  moveInd(false);
  if (paintTabs.lit !== t) {  // (the minimised bar's glyph: copied when the tab lit changes)
    paintTabs.lit = t;
    const lit = document.querySelector("#tabbar .tb.on");
    byId("tbMini").innerHTML = lit ? lit.innerHTML.replace("<span>", '<span class="hidden">') : "";
  }
  tabMin(false);
}
let minOn = false, mt = null;  // (the tab bar minimised; the finger's move: its last place, where its direction last changed)
function tabMin(on) {
  if (on === minOn) return;
  minOn = on;
  byId("tabbar").classList.toggle("min", on);
}
byId("tbMini").addEventListener("click", (e) => { e.stopPropagation(); tabMin(false); });
document.addEventListener("touchstart", (e) => { mt = e.touches.length === 1 ? {y: e.touches[0].clientY, a: e.touches[0].clientY, dir: 0} : null; }, {passive: true});
document.addEventListener("touchmove", (e) => {  // (from the finger alone: 48 px moving the page down past its top folds the bar, 24 px
  if (!mt || e.touches.length !== 1 || !compactMQ.matches) return;
  const y = e.touches[0].clientY, dir = y < mt.y ? 1 : y > mt.y ? -1 : 0;
  mt.y = y;
  if (!dir) return;
  if (dir !== mt.dir) { mt.dir = dir; mt.a = y; }
  if (dir > 0 && mt.a - y > 48 && solidOn && lastTab !== "search" && lastKey !== "play" && !sheet.classList.contains("open")) tabMin(true);
  else if (dir < 0 && y - mt.a > 24) tabMin(false);
}, {passive: true});
function moveInd(instant) {  // (the lozenge under the tab picked: there at once (instant), else sliding on the snappy spring)
  const cap = document.querySelector("#tabbar .tb-cap"), pill = cap.firstElementChild, i = TABS.indexOf(lastTab);
  if (i < 0) return;
  if (instant) { pill.style.transition = "none"; nextFrame(() => { pill.style.transition = ""; }); }
  cap.style.setProperty("--i", i);
}
function tabTap(t) {  // (another tab: at once, to the page left on top of it. This one: from a title back to its root; at the root,
  if (t !== curTab() || !lastView) {
    const h = tabTop[t] || rootOf(t);
    navIntent = {kind: "tab", at: performance.now()};
    if (location.hash === h || (h === "#/" && !location.hash)) { history.replaceState(Object.assign({}, history.state, {tab: t}), ""); route(false); return; }
    pushHash(h, t);
    return;
  }
  if (!tabOfHash(location.hash)) { popTo(rootOf(t)); return; }
  if (window.scrollY > 0) window.scrollTo({top: 0, behavior: smooth()});
  else if (t === "search") byId("q").focus();
}
byId("tabbar").addEventListener("click", (e) => {
  const a = e.target.closest("[data-tab]");
  if (!a) return;
  e.preventDefault();
  tabTap(a.dataset.tab);
});
document.querySelector("#nav .brand").addEventListener("click", (e) => { e.preventDefault(); tabTap("home"); });
document.addEventListener("click", (e) => {  // (a link followed in the page is a push: it slides in on a touch screen; while one
  const a = e.target.closest && e.target.closest('a[href^="#/"]');
  if (!a || a.closest("#tabbar") || a.classList.contains("brand")) return;
  if (moving && moving.how === "push") { e.preventDefault(); e.stopImmediatePropagation(); return; }
  navIntent = {kind: "push", at: performance.now()};
}, true);

let moving = null;  // (a move in flight: finished at once should another start)
function endMove() {
  if (!moving) return;
  const m = moving;
  moving = null;
  clearTimeout(m.t);
  m.out.classList.remove("mv-out-push", "mv-out-pop", "mv-out-fade", "mv-edge");
  m.out.style.cssText = "";
  if (m.out.id !== lastView) showView(m.out, false);
  m.in.classList.remove("mv-in-push", "mv-in-pop", "mv-edge", "enter");
}
function moveTo(how, outEl, y, swap) {  // (how: "push" or "pop" on a touch screen, "fade" with a mouse)
  endMove();
  outEl.style.cssText = "position:fixed;left:0;right:0;top:" + (-y) + "px;z-index:" + (how === "pop" ? 3 : 1) + ";pointer-events:none";  // (held
  swap();  // (the new page in the flow at its own scroll; the old one hidden by it, shown again below until the move ends)
  const inEl = byId(lastView);
  if (inEl === outEl) { outEl.style.cssText = ""; return; }
  showView(outEl, true);
  if (how === "fade") { inEl.classList.add("enter"); outEl.classList.add("mv-out-fade"); }
  else {
    inEl.classList.add("mv-in-" + how);
    outEl.classList.add("mv-out-" + how);
    (how === "push" ? inEl : outEl).classList.add("mv-edge");
    const nav = byId("nav");  // (the bar's buttons and title change at once and fade in, as iOS crossfades them)
    nav.classList.add("navx");
    clearTimeout(nav.x);
    nav.x = setTimeout(() => nav.classList.remove("navx"), 260);
  }
  const last = how === "pop" ? outEl : inEl;
  moving = {in: inEl, out: outEl, how: how, t: setTimeout(endMove, 900)};
  last.addEventListener("animationend", function done(e) {
    if (e.target !== last) return;
    last.removeEventListener("animationend", done);
    if (moving && moving.in === inEl) endMove();
  });
  shieldOn(inEl);
}

function sentNow() {
  const v = lastView && byId(lastView);
  return !v ? null : lastView === "v-title" && compactMQ.matches ? v.querySelector(".tl .sent") : v.firstElementChild;
}
function solid(on, now) {
  if (on === solidOn) return;
  solidOn = on;
  if (!on) tabMin(false);  // (back at the page's top: the tabs again)
  const nav = byId("nav");
  if (now) { nav.classList.add("now"); nextFrame(() => nav.classList.remove("now")); }
  nav.classList.toggle("solid", on);
}
function tall(on) { if (on !== tallOn) { tallOn = on; byId("nav").classList.toggle("tall", on); } }
const sentIO = "IntersectionObserver" in window ? new IntersectionObserver((es) => es.forEach((e) => {
  if (e.target === sentNow() && e.boundingClientRect.height) solid(!e.isIntersecting && e.boundingClientRect.top < 0);
})) : null;
if (sentIO) document.querySelectorAll(".sent").forEach((s) => sentIO.observe(s));
let pinIO = null;
let salPx = 0, lastW = -1;  // (the screen's own left edge, in from the side (a phone on its side, its notch); the width measured last)
function pins() {  // (the markers just before the accessories that pin under the bar (the search field, a title's index of versions):
  navH = byId("nav").offsetHeight;
  lastW = window.innerWidth;
  salPx = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--sal")) || 0;
  if (!("IntersectionObserver" in window)) return;
  if (pinIO) pinIO.disconnect();
  pinIO = new IntersectionObserver((es) => es.forEach((e) => {
    const acc = e.target.nextElementSibling, b = e.boundingClientRect;
    if (!acc || !b.height) return;
    const on = !e.isIntersecting && b.top < navH;
    acc.classList.toggle("stuck", on);
    if (lastView && byId(lastView).contains(e.target)) tall(on);
  }), {rootMargin: "-" + navH + "px 0px 0px 0px"});
  document.querySelectorAll(".pin").forEach((p) => pinIO.observe(p));
}
function barNow(y) {  // (the bar for the page just shown: from where it was scrolled to. A tab's page has its marker at a fixed place
  let on = false, t = false;
  const p = lastView && byId(lastView).querySelector(".pin");
  if (y > 0 && HOMES[lastView.slice(2)]) on = y > (compactMQ.matches ? 40 : 8);
  else if (y > 0) {
    const s = sentNow();
    on = !!s && s.getBoundingClientRect().top < 0;
    t = !!p && p.getBoundingClientRect().top < navH;
  }
  if (p && p.nextElementSibling) p.nextElementSibling.classList.toggle("stuck", t);
  solid(on, !barSoft);
  tall(t);
}

function route(back) {
  const how = navIntent && performance.now() - navIntent.at < 700 ? navIntent.kind : null, boot = lastView === null && lastKey === null;  // ("push",
  const leaveY = boot ? 0 : window.scrollY;  // (at boot nothing is read from the layout before the first frame)
  navIntent = null;
  const h = location.hash.slice(1) || "/", qi = h.indexOf("?");
  const path = qi >= 0 ? h.slice(0, qi) : h, query = new URLSearchParams(qi >= 0 ? h.slice(qi + 1) : "");
  let p;
  try { p = path.split("/").filter(Boolean).map(decodeURIComponent); } catch (e) { p = ["?"]; }
  if (p[0] === "play") {
    if (!playing) { if (idx > 0) history.back(); else replaceHash("#/"); return; }  // (forward into a player closed since: back where it was)
    if (lastKey && lastKey !== "play") { scrolls[lastKey] = leaveY; playFrom = lastKey; }
    lastKey = "play";
    lastHash = location.hash;
    closeSheet(true, true);
    showPlayer();
    behind(true);
    syncVersionFeeds();
    return;
  }
  const fromPlay = lastKey === "play";
  if (playing && !inPip()) stopPlayer();
  hidePlayer();
  let view, key;
  if (!p.length || p[0] === "movies" || p[0] === "series") { view = p[0] ? "v-" + p[0] : "v-home"; key = "home/" + (p[0] || "home"); }
  else if (p[0] === "library") { view = "v-library"; key = "library"; }
  else if (p[0] === "search") { view = "v-search"; key = "search"; }
  else if (p[0] === "title" && (p[1] === "movie" || p[1] === "series") && /^tt\d+/.test(p[2] || "")) { view = "v-title"; key = "title/" + p[1] + "/" + p[2]; }
  else { replaceHash("#/"); return; }
  const lp = typeof lastPlayed !== "undefined" ? lastPlayed : null;
  if (fromPlay && p[1] === "series" && p[4] != null && lp && lp.id === p[2] && lp.ep && (Number(lp.ep.s) !== Number(p[3]) || Number(lp.ep.e) !== Number(p[4]))) {
    replaceHash("#/title/series/" + p[2] + "/" + lp.ep.s + "/" + lp.ep.e);  // (back from an episode the player moved on to: its sheet, not
    return;
  }
  const changed = view !== lastView || (view === "v-title" && key !== lastKey && lastKey !== "play");  // (another view, or another title)
  const swap = () => {
    if (lastKey && lastKey !== "play") scrolls[lastKey] = leaveY;
    lastHash = location.hash;
    const under = lastKey === "play" && key === playFrom;  // (back from the player to the page under it: scrolled where it was, its bar as
    VIEWS.forEach((x) => showView(byId(x), x === view));
    lastView = view;
    lastTab = curTab();
    tabTop[lastTab] = location.hash;
    if (view === "v-search") tabTop.searchRoot = location.hash;
    if (view === "v-title") showTitle(p[1], p[2], p[3] != null ? Number(p[3]) : null, p[4] != null ? Number(p[4]) : null, query.get("to"));
    else if (view === "v-search") openSearch(query.get("q") || "", hoverable ? "late" : how === "tab");
    else if (view === "v-library") renderLibrary();
    else renderHome(p[0] || "home");
    byId("tBack").classList.toggle("hidden", view !== "v-title");
    byId("nav").classList.toggle("ontitle", view === "v-title");
    if (view !== "v-title") closeSheet(true, true);
    byId("navTitle").textContent = view === "v-title" ? (cur && cur.name) || "" : NAVTITLE[lastTab] || "";
    const y = key !== lastKey && !under ? scrolls[key] || 0 : null;
    if (y !== null && !(boot && !y)) window.scrollTo(0, y);  // (in the same task as the swap: no frame shows the old place)
    lastKey = key;
    paintTabs();
    if (y !== null) barNow(y);
  };
  const outEl = lastView && byId(lastView);
  barSoft = false;
  if (how === "gesture") edgeDone(true);  // (the swipe's layers let go first: the root back in the flow, so its scroll can be restored)
  if (!changed || !outEl || reduced.matches || document.hidden || how === "tab" || how === "gesture") { endMove(); swap(); }
  else if (slideMQ.matches) { if (how === "push" || how === "pop") { barSoft = true; moveTo(how, outEl, leaveY, swap); } else { endMove(); swap(); } }
  else { barSoft = true; moveTo("fade", outEl, leaveY, swap); }
  behind(false);
  syncVersionFeeds();
}
window.addEventListener("scroll", () => { lastInput = performance.now(); }, {passive: true});  // (a scroll only stamps the time: the
window.addEventListener("hashchange", () => {
  const st = history.state;
  let back = false;
  if (st && typeof st.i === "number") { back = st.i < idx; idx = st.i; }
  else { idx += 1; history.replaceState({i: idx, from: lastHash, tab: tabOfHash(location.hash) || lastTab || curTab()}, ""); }  // (a new
  route(back);
});
byId("tBack").onclick = (e) => { e.preventDefault(); popTo(rootOf(curTab())); };

let edge = null;  // (the swipe's layers: the root page pinned under the title page where it was left, a shade between them)
function edgeStart() {
  const t = curTab(), root = byId(t === "home" ? "v-home" : "v-" + t), title = byId("v-title"), shade = document.createElement("div");
  endMove();
  showView(root, true);
  root.style.cssText = "position:fixed;left:0;right:0;top:" + (-(scrolls[ROOTKEY[t]] || 0)) + "px;z-index:0;transform:translate3d(-30%,0,0);pointer-events:none";
  shade.className = "eshade";
  shade.style.opacity = ".25";
  title.after(shade);
  title.classList.add("mv-edge");
  edge = {root: root, shade: shade, title: title, w: window.innerWidth};
}
function edgeMove(dx) {
  const p = Math.min(1, dx / edge.w);
  edge.title.style.transform = "translate3d(" + dx + "px,0,0)";
  edge.root.style.transform = "translate3d(" + (-30 + 30 * p) + "%,0,0)";
  edge.shade.style.opacity = String(.25 * (1 - p));
}
function edgeEnd(es, cancel) {  // (let go: on to the root if pulled a third of the way or flung, else back; in a time that matches the fling)
  const a = es.pts[0], b = es.pts[es.pts.length - 1], v = (b[1] - a[1]) / Math.max(1, b[0] - a[0]);
  const dx = Math.max(0, b[1] - es.x0), done = !cancel && (dx / edge.w > .33 || v > .5) && v > -.2;
  const ms = Math.round(Math.max(180, Math.min(360, (done ? edge.w - dx : dx) / Math.max(Math.abs(v), 1.2))));
  const e = edge, tr = "transform " + ms + "ms var(--sp-nav), opacity " + ms + "ms var(--sp-nav)";
  e.title.style.transition = e.root.style.transition = e.shade.style.transition = tr;
  e.title.style.transform = done ? "translate3d(100%,0,0)" : "translate3d(0,0,0)";
  e.root.style.transform = done ? "translate3d(0,0,0)" : "translate3d(-30%,0,0)";
  e.shade.style.opacity = done ? "0" : ".25";
  setTimeout(() => {
    if (edge !== e) return;
    if (!done) { edgeDone(); return; }
    popTo(rootOf(curTab()), "gesture");
    setTimeout(() => { if (edge === e) edgeDone(); }, 1500);  // (should the move back never come: the layers let go all the same)
  }, ms + 20);
}
function edgeDone(going) {  // (the swipe's layers let go: going back, the root page takes the flow, its scroll restored by the route
  if (!edge) return;
  const e = edge;
  edge = null;
  e.shade.remove();
  e.title.classList.remove("mv-edge");
  e.title.style.cssText = "";
  e.root.style.cssText = "";
  if (!going && lastView !== e.root.id) showView(e.root, false);
}
if (navigator.standalone === true || matchMedia("(display-mode: standalone)").matches) {
  let es = null;
  const move = (e) => {
    const t = e.touches[0], dx = t.clientX - es.x0, dy = t.clientY - es.y0;
    if (!es.on) {
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { end(); return; }  // (a scroll: not ours)
      if (dx < 10 || dx < Math.abs(dy) * 1.2) return;
      es.on = true;
      edgeStart();
    }
    e.preventDefault();
    es.pts.push([e.timeStamp, t.clientX]);
    if (es.pts.length > 5) es.pts.shift();
    edgeMove(Math.max(0, dx));
  };
  const end = (cancel) => {
    document.removeEventListener("touchmove", move);
    if (es && es.on && edge) edgeEnd(es, cancel === true);
    es = null;
  };
  document.addEventListener("touchstart", (e) => {
    const t = e.touches[0], tab = curTab();
    if (e.touches.length !== 1 || lastView !== "v-title" || sheet.classList.contains("open") || playing || edge || moving) return;
    if (TABS.indexOf(tab) >= 0 && tab !== "library" && !HOMES[tab].built) return;  // (a root never shown: nothing to swipe back to)
    if (t.clientX > 24 + salPx) return;  // (24 px in from the edge, past the notch on a phone on its side)
    es = {x0: t.clientX, y0: t.clientY, on: false, pts: [[e.timeStamp, t.clientX]]};
    document.addEventListener("touchmove", move, {passive: false});  // (for this touch only: scrolling never waits on it)
  }, {passive: true});
  document.addEventListener("touchend", () => end(), {passive: true});
  document.addEventListener("touchcancel", () => end(true), {passive: true});
}
window.addEventListener("resize", () => { if (window.innerWidth !== lastW) requestAnimationFrame(() => { pins(); barNow(window.scrollY); }); }, {passive: true});
onMedia(compactMQ, () => { pins(); barNow(window.scrollY); });
afterFrame(pins);  // (measured once the first frame is out: nothing read from the layout before it)
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => moveInd(true));
document.addEventListener("keydown", (e) => {  // (the page's keys: Escape closes a player menu, then the sheet, then the player; "/" searches
  const tag = e.target.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") { if (e.key === "Escape" && tag === "INPUT") e.target.blur(); return; }
  if (e.key === "Escape") {
    if (menuOpen) closeMenu();
    else if (sheet.classList.contains("open")) closeSheet();
    else if (nativePlayer()) { if (nativeFullscreen()) exitFull(); }  // Safari may have already consumed Escape to leave its player.
    else if (nativeFullscreen() || imm || fsEl()) exitFull();
    else if (playing && !pl.classList.contains("hidden")) leavePlayer();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;  // (the browser's own)
  if (!playing || pl.classList.contains("hidden")) {
    if (e.key === "/") {  // (on the search page: its field, the query kept)
      e.preventDefault();
      const q = byId("q");
      if (location.hash.indexOf("#/search") === 0 && q) { q.focus(); q.select(); } else go("#/search");
    }
    return;
  }
  if (nativePlayer()) return;  // Native media controls own their keyboard and touch interactions.
  if (menuOpen) { menuKey(e); return; }
  const k = e.key, own = e.target !== pl && e.target.closest && e.target.closest("button");
  if (k === "Tab" || (own && (k === " " || k === "Enter"))) { kbd = true; showCtl(); return; }  // (a focused button's own key: never both)
  if (k === " " || k === "k" || k === "K") { e.preventDefault(); if (!e.repeat) togglePlay(); }
  else if (k === "ArrowRight" || k === "ArrowLeft" || k === "l" || k === "j") {
    e.preventDefault();
    const s = k === "ArrowRight" || k === "l" ? 1 : -1;
    if (e.target === pvol && k.indexOf("Arrow") === 0) setVol(curVol() + s * .05, true); else skipBy(10 * s);
  } else if (k === "ArrowUp" || k === "ArrowDown") { if (canVol) { e.preventDefault(); setVol(curVol() + (k === "ArrowUp" ? .05 : -.05), true); hudLevel("vol"); hudOff(700); } }
  else if (k === "m" || k === "M") toggleMute(true);
  else if (k === "f" || k === "F") toggleFull();
  else if (k === "N" && e.shiftKey) { if (nextEpisode()) playNext(); }
  else if (k === "Home") { e.preventDefault(); if (e.target === pvol) setVol(0, true); else seekTo(0); }  // (the volume slider: none, all)
  else if (k === "End") { e.preventDefault(); if (e.target === pvol) setVol(1, true); else seekTo(vid.duration - 3); }
  showCtl();
});
window.addEventListener("beforeunload", () => savePos(true));
document.addEventListener("visibilitychange", () => { if (document.hidden) savePos(true); });
placeChips();
route(false);
// Installation and updates are user actions. An active native or in-page player
// is never reloaded by a service-worker update.
(() => {
  const install = byId("pwaInstall");
  const standalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const safari = /Safari/.test(navigator.userAgent) && !/Chrome|Chromium|Edg|OPR/.test(navigator.userAgent) && /Apple/.test(navigator.vendor || "");
  const activePlayback = () => {
    const video = byId("vid");
    return !!(typeof playing !== "undefined" && playing) || !!(video && (document.pictureInPictureElement === video ||
      video.webkitDisplayingFullscreen || video.webkitPresentationMode === "picture-in-picture" || (!video.paused && !video.ended)));
  };
  const notice = document.createElement("aside");
  notice.className = "pwa-notice";
  notice.hidden = true;
  notice.setAttribute("role", "status");
  notice.setAttribute("aria-live", "polite");
  document.body.appendChild(notice);
  let prompt = null, registration = null, dismissedUpdate = null, dismissedOffline = false, kind = null, reloadRequested = false, reloadAvailable = false, updateTimer = 0;
  function hide() { notice.hidden = true; kind = null; }
  function message(type, title, text, label, action) {
    if (activePlayback()) return;
    kind = type;
    notice.classList.toggle("compact", type === "update" || type === "updating");
    notice.innerHTML = '<div class="pwa-copy"><strong>' + esc(title) + '</strong>' + (text ? '<p>' + esc(text) + '</p>' : '') + '</div><div class="pwa-actions">' +
      (label ? '<button class="pwa-action" type="button">' + esc(label) + '</button>' : '') +
      '<button class="pwa-close" type="button" aria-label="Dismiss notification">' + ICON.close + '</button></div>';
    notice.hidden = false;
    const button = notice.querySelector(".pwa-action");
    if (button) button.onclick = action;
    notice.querySelector(".pwa-close").onclick = () => {
      if (kind === "update") { dismissedUpdate = registration && registration.waiting; reloadAvailable = false; }
      if (kind === "offline") dismissedOffline = true;
      hide();
    };
  }
  function updateAction() {
    if (activePlayback()) { hide(); return; }
    if (!registration || !registration.waiting) { hide(); return; }
    reloadRequested = true;
    clearTimeout(updateTimer);
    updateTimer = setTimeout(() => {
      if (!reloadRequested) return;
      reloadRequested = false;
      hide();
      refresh();
    }, 15000);
    registration.waiting.postMessage({type: "FLEET_ACTIVATE_UPDATE"});
    message("updating", "Updating…", "");
  }
  function refresh() {
    if (install) install.hidden = standalone() || (!prompt && !ios && !safari);
    if (activePlayback()) { hide(); return; }
    if (!navigator.onLine) {
      if (!dismissedOffline) message("offline", "You’re offline", "Reconnect to browse and play. Your library and progress are saved on this device.", "Try again", () => {
        if (!activePlayback()) location.reload();
      });
      return;
    }
    if (kind === "offline") hide();
    if (reloadAvailable && kind !== "install") {
      message("update", "Update ready", "", "Reload", () => {
        if (!activePlayback()) location.reload();
      });
      return;
    }
    if (registration && registration.waiting && registration.waiting !== dismissedUpdate && kind !== "install" && kind !== "updating") {
      message("update", "Update available", "", "Update", updateAction);
    }
  }
  if (install) install.onclick = async () => {
    if (activePlayback()) return;
    if (prompt) {
      const pending = prompt;
      prompt = null;
      try { await pending.prompt(); await pending.userChoice; } catch (_) {
        message("install", "Install Frame", "Open your browser’s menu and choose Install app or Add to Home Screen.");
      }
      refresh();
    } else if (ios) {
      message("install", "Add Frame to your Home Screen", "Open the Share menu, choose Add to Home Screen, then tap Add.", "Got it", hide);
    } else if (safari) {
      message("install", "Keep Frame in your Dock", "In Safari, open the File menu and choose Add to Dock.", "Got it", hide);
    }
  };
  window.addEventListener("beforeinstallprompt", (event) => { event.preventDefault(); prompt = event; refresh(); });
  window.addEventListener("appinstalled", () => { prompt = null; hide(); refresh(); });
  window.addEventListener("offline", refresh);
  window.addEventListener("online", () => { dismissedOffline = false; refresh(); });
  window.addEventListener("hashchange", () => queueMicrotask(refresh));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  const video = byId("vid");
  if (video) ["play", "pause", "ended", "emptied", "leavepictureinpicture", "webkitendfullscreen", "webkitpresentationmodechanged"].forEach((event) => video.addEventListener(event, () => queueMicrotask(refresh)));
  refresh();
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!reloadRequested) return;
    reloadRequested = false;
    clearTimeout(updateTimer);
    if (activePlayback()) { reloadAvailable = true; hide(); return; }
    location.reload();
  });
  navigator.serviceWorker.register("/sw.js?release=2026-11-13-frame-7", {updateViaCache: "none"}).then((reg) => {
    registration = reg;
    refresh();
    reg.addEventListener("updatefound", () => {
      const worker = reg.installing;
      if (worker) worker.addEventListener("statechange", () => { if (worker.state === "installed") refresh(); });
    });
  }).catch(() => { /* Browsing and playback still work when installation is unavailable. */ });
})();
</script>
</body>
</html>
"""
WEB_FILES = {'apple-touch-icon.png': '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', 'icon-192.png': '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', 'icon-512-maskable.png': 'iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAAAAXNSR0IArs4c6QAAADhlWElmTU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAAqACAAQAAAABAAACAKADAAQAAAABAAACAAAAAAAoMJe/AAA3PklEQVR4Ae3dC3Ac1Zno8a9nNA9Zki3Jkm0hOVjGD2xsc8HGxo4BXx4hBDs8KnaKJSEJtSQUBlLFJrtZUgXk1k2ySe6S3ARSSVGbTVUuySY8UgQ7jw3LgmNiDE5Y8zKOHWOCH9jYsvFT1oyk22fkjSV5JM3jdM85ff5dpZI00336O7+v5+hTz5xur6auvldYEEAAAQQQQMApgZhTvaWzCCCAAAIIIJAToADgQEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKAA4BhAAAEEEEDAQQEKAAeTTpcRQAABBBCgAOAYQAABBBBAwEEBCgAHk06XEUAAAQQQoADgGEAAAQQQQMBBAQoAB5NOlxFAAAEEEKiCAIEwBGqS42RM9UQZM2qijE63SnWiUdLJBhl18nt1okGqYmmJx1L+V8L/Sua+Yl48jPDYBwKBCPT0dkt3T9fJr4z//YRkezrleOaAdHYdkGOZjtz34/73Q5075b1jb8t7x9+Wo117A4mHRhHoL0AB0F+Dn8sU8KR+1CQZVzdDmmtnSJP/val2qv8Hv02q4uky22ZzBOwTUAVsLF4tCf+rmCXb3ekXBDtk35Etsu/wJnn3yCbZ638/eGy730xvMU2xLgJDCng1dfUcTUPy8MRwAmn/v/Yz6udKa/0F0tYwX8aPnl30QDdc+zyHAAIDBTLdx2XPoVdkx4EXZOfBF2XXwT9Ip382gQWBUgQoAEpRc3QbdYr+fWMXS3vTEjmzcbE01kwRz/Mc1aDbCFReoLe3VzqObpW3OtbKm/uekb/sX5t7i6HykRGBDQIUADZkqYIx1qXPkGnjr5bJTZdKW+MC/336VAWjYdcIIDCcQNb/jMGOjvWybd/T8qc9q+Vw567hVuc5xwUoABw/APJ1PxGvkekTlso5Z3xEJjYs5L/8fEg8hoDhAurswNsH1slrux6Vze+skkz3UcMjJrywBSgAwhY3dH+exGRS08X+H/3lMmXclbyXb2ieCAuBUgTUZwe27v2NXww8Itv3rfE/RthTSjNsEzEBCoCIJbTY7jTXzcz9pz+j5TqpTY0rdnPWRwABywSOnNgrm3b/PHdm4N3Dr1sWPeHqFKAA0KlpTVueTB+/VBZMvt3/5P4sa6ImUAQQ0CugZhSs3/agbN6zym+YCWF6dc1vjQLA/Bxpi9Dz5yTPbLleLvT/8KtP8LMggAACSkDNJHh+2wPy+u7Hpde/eBGLGwIUAA7kOe4lZVbrR/3/+FfmrsbnQJfpIgIIlCCgrkKozgi8uvOn0t3bVUILbGKTAAWATdkqMlZ19bFz2z4uF0z6jNSmJxS5NasjgICrAkc635EXt39fNu74kT974LirDJHvNwVABFOs/uOfN+kW/+tWGZVsjGAP6RICCIQhcKyrQzZs/57/9RBnBMIAD3kfFAAhgwe9u0ljL5bLZ3xZGmomB70r2kcAAUcEDhzdJk9t+qJs37/GkR670U0KgIjkuTbVIpeefV/uAj4R6RLdQAABwwTUBYWefuM+OXJit2GREU4pAhQApagZtI36ZP+8M2+RRWfdJcmqGoMiIxQEEIiiQFf2qPz+z/fLhrceYsaA5QmmALA4gW0NC/zT/V+R5rqzLe4FoSOAgI0C7x5+w39b4G7/zoTrbQyfmH0BCgALD4Nq/za8S6bf60/tW25h9ISMAAJREnh15yPyzOYvyXFuS2xdWikALEtZa/18WXbud6Uu3WJZ5ISLAAJRFTjcuVue3Hib7Dz4QlS7GMl+UQBYlNYF7XfI4qmfl5j/vj8LAgggYJJAj38FwbVbviHr3/yOSWERyzACFADD4JjyVHWiUa6e84C0N11iSkjEgQACCOQVeHPfs7L65dv9twQ68j7Pg+YIUACYk4u8kbQ1XCjL5jzIlfzy6vAgAgiYKKCuJPjkyyv9Dwg+b2J4xHRSgALA2EPB82/ac6e8f8rfccrf2BwRGAIIDCWg3hJ4bus/+zcZ+ra/CncaHMqpko9TAFRSf4h9q1P+S899UNRV/VgQQAABmwXU1QNXbVzJWwIGJpECwLCkjE63yYp5P+FSvoblhXAQQKB0AXUp4Z9tuEEOde4ovRG21C4Q094iDZYs0Fw7Q2688Bf88S9ZkA0RQMBEAXVvEjW2qTGOxRwBCgBDcqGu6nfD/MelNjXekIgIAwEEENAnoMY2NcapsY7FDAEKAAPyMGXclbJ87o8llRhtQDSEgAACCAQjoMY4NdapMY+l8gIUABXOwezWG+Sa//GQVMXTFY6E3SOAAALBC6ixTo15auxjqaxAPJlK31fZENzdu5rmd9mM/yWeRx3m7lFAzxFwT0CNeVPGfUB6erPcTKiC6acAqBD+kun3yMKzPluhvbNbBBBAoPICZ45d7N/GvFa273+28sE4GAEFQAWSfuHkz/LHvwLu7BIBBMwTaK2fx5mACqWFc88hw6v3vS6a+vch75XdIYAAAuYKXDT1H/hMQAXSQwEQIrr65OsHzvlaiHtkVwgggIAdAmpsZHZAuLmiAAjJW819XTbnu1zXPyRvdoMAAnYJqNucqzGS6wSElzcKgBCs1dWvrj/vh0z1C8GaXSCAgL0CaoqgGiu5YmA4OaQACNhZXdv/I/Me5iI/ATvTPAIIRENAXSxIjZlq7GQJVoACIEBfdVc/dWMfLu8bIDJNI4BA5ATUmKnGTjWGsgQnQAEQmK2Xu6WvugkGCwIIIIBAcQJq7FS3RRfxituQtQsWoAAomKq4FRf6c/0njb24uI1YGwEEEEDgrwJqDFVjKUswAhQAAbi2NVwoi6bcFUDLNIkAAgi4JaDGUjWmsugXoADQbKres1o250Gm+2l2pTkEEHBToG964IN8HiCA9FMAaEa9es4DUpueoLlVmkMAAQTcFVBjqhpbWfQKUABo9FzQfoe0N12isUWaQgABBBBQAmpsVWMsiz4BCgBNlq3182Xx1M9rao1mEEAAAQQGC6gxVo21LHoEKAA0OFYnGmTZuVzmVwMlTSCAAAJDCuQ+D+CPtWrMZSlfgAKgfENZMv1eqUu3aGiJJhBAAAEEhhNQY60ac1nKF6AAKNNQ3bhiVuvyMlthcwQQQACBQgXUmMtNgwrVGno9CoChbUZ8xvPvXnXFzK+OuB4rIIAAAgjoFbh8xldEjcEspQtQAJRuJ/POvEWaaqeX0QKbIoAAAgiUItBcd3ZuDC5lW7bpE6AAKPFIqE21yKKzuNpfiXxshgACCJQtoMZgNRazlCZAAVCam1x69n2SrKopcWs2QwABBBAoV0CNwWosZilNgAKgBDd1g4rpE5aWsCWbIIAAAgjoFFBjMTdeK02UAqBIt7iXlMtnfLnIrVgdAQQQQCAoATUmq7GZpTgBCoDivGTepFtE3aeaBQEEEEDADAE1JquxmaU4AQqAIrwS8Wr/ILu1iC1YFQEEEEAgDAE1NqsxmqVwAQqAwq3k3LaPy6hkYxFbsCoCCCCAQBgCamxWYzRL4QIUAAVaqfeXLpj0mQLXZjUEEEAAgbAF1BjNZwEKV6cAKNBqVutHRd2TmgUBBBBAwEwBNUarsZqlMAEKgAKc1OUmF0xeWcCarIIAAgggUEkBNVZzieDCMkABUIDTzJbrZUz1xALWZBUEEEAAgUoKqLFajdksIwtQAIxo5MmFk28fcS1WQAABBBAwQ6BvzPbMCMbgKCgARkjO9PFLpbFmyghr8TQCCCCAgCkCasxWYzfL8AIUAMP7+O/989//CEQ8jQACCBgnwOe2Rk4JBcAwRs11M2X86FnDrMFTCCCAAAImCowfPVvUGM4ytAAFwNA2cs4ZHxnmWZ5CAAEEEDBZgDF8+OxQAAzh40lMZrRcN8SzPIwAAgggYLqAGsPVWM6SXwCZ/C4yqeliqU2NG+JZHkYAAQQQMF1AjeFqLGfJL0ABkN/FP/2/fIhneBgBBBBAwBYBxvKhM0UBkMcmEa+RKeOuzPMMDyGAAAII2CSgxnI1prOcLkABcLqJTJ+wlNtK5nHhIQQQQMA2AXWLYDWms5wuQAFwugmf/s9jwkMIIICArQLMBsifOQqAQS516TNkYsPCQY/yKwIIIICArQJqTFdjO8tAAQqAgR65y0d6HteQHsTCrwgggIC1AmpMnzb+amvjDypwCoBBsu1N/3PQI/yKAAIIIGC7wOSmS23vgvb4KQD6kVbF0tLWuKDfI/yIAAIIIBAFATW2qzGe5ZQABcApC3nf2MX+AZLq9wg/IoAAAghEQUCN7WqMZzklQAFwykLam5b0+40fEUAAAQSiJMAYPzCbFAD9PM5spDrsx8GPCCCAQKQEGOMHppMC4KRHOtEgjTVTBurwGwIIIIBAZATUGK/GepY+AQqAk0dCa/08YfofLwsEEEAgugJqjD+jfm50O1hkzygAToK11l9QJB2rI4AAAgjYJtBWP9+2kAOLlwLgJG1rAwVAYEcZDSOAAAKGCDDWn0oEBUDOwpPxo2efUuEnBBBAAIFICvSN9VztVSWXAsBHqB81ibv/RfKlTqcQQACBgQLq7oBqzGehAMgdA+PqZnAsIIAAAgg4IsCY35dozgD4Ds21FACOvO7pJgIIIMCYf/IYoADwIZrrZvKSQAABBBBwRKCJs765TFMA+Axja7kAkCOve7qJAAIISFPtVBR8AQoAH2F0uo2DAQEEEEDAEQHG/L5EO18A1CTHSVWcW0Q68rqnmwgggEBuzFdjv+uL8wXAmOqJrh8D9B8BBBBwToCxn7cAZMwoCgDnXvl0GAEEnBdg7KcA8N//b3X+hQAAAggg4JoAYz8FgFQnGl077ukvAggg4LwAYz8FgFQnKQCcHwkAQAAB5wQY+0WqnMv6oA5XJxoGPcKvrguMPcOTORdVScx/dezf1SOvreuWTKfrKvQfgWgJMPZTAHAGIFqv6bJ70zolJp+4LyWJ1Km7hX3wk73yh6ey8uyjGek6XvYuaAABBAwQ4AwAbwFIOlFvwKFICKYIXHt7csAffxVXapQniz6ckJXfTMvZ8+OmhEocCCBQhgBjPwUAtwEu4wUUtU0nnh2TptahL40xemxMPvr5lNzwhZQ0Tjh1hiBqDvQHARcE1G2BXV+c/wxAzEu6fgzQ/5MCZ80p7L/7aXPjctactKxblZU1j2f4fABHEAIWCjD2cwZA4rGEhYcuIQch0Dyx8P/q4wlPFl+XkDu+XS2zLyqscAgiZtpEAIHSBBj7KQD8AoAzAKW9fKK31XCn/4fqbV2DJ9ffmZKb/3dKWiYP/fbBUNvzOAIIVEaAsZ8CgAKgMq89I/daXVN6WBOnx+WWr6Zk6aeTMmp06e2wJQIIhCNAAUABIDGP07fhvNzM30v/qX+lROvFPJl7RVXubYH5V1WJxwmBUhjZBoFQBBj7KQBCOdDYiR0CiZSeONM1nlx1c1Ju/T9pmTSLKkCPKq0ggIBuAUYn3aK0Z61AvKrwDwEW0slxE/2LCt2bluV3JWVMk962C9k/6yCAAALDCTg/DXA4HJ5DQIfAzIVVMvX8uDz3RMb/ykq2S0ertIEAAgiUJ8AZgPL82BqBggTU5wuWrEjKym+lZcYCPndSEBorIYBAoAIUAIHy0jgCAwXqm2Oy4nMpuemelDS38bbAQB1+QwCBMAUoAMLUZl8InBRonx3PfUjwyk8m/HsNwIIAAgiEL0ABEL45e0QgJxCLe3Lh1X1XEzzvMv9tAU4IcGQggECIAhQAIWKzKwTyCdSM8eTDt6bk0/+UlrZpvCTzGfEYAgjoF2C00W9KiwiUJKAuJawuKaxuSVzLXapLMmQjBBAoXIACoHAr1kQgcAHP8+TcS/quJrjomiqJMVE3cHN2gICrAhQArmaefhstkKz25IqPJeW2+9My5TxepkYni+AQsFSAkcXSxBG2GwJjW2Jy491pueELKWmcwKcE3cg6vUQgHAFOMIbjzF4QKEtg2ty4nDUnLetWZ2XNYxnJdJbVHBsjgAACwhkADgIELBGIJzxZfG3ftMHZF3E1QUvSRpgIGCtAAWBsaggMgfwCdQ2eXH9nKjdjYEI7bwvkV+JRBBAYSYACYCQhnkfAUIGJ0+O5awcs/XRSRo02NEjCQgABYwUoAIxNDYEhMLKAF/Nk7hV90wbnX1UlHq/okdFYAwEEcgIMFxwICERAIF3jyVU3J3P3F5g0i5d1BFJKFxAIXICRInBidoBAeALjJsbkE/emZfldSRnTxOcDwpNnTwjYJ8A0QPtyRsQIjCgwc2GVTD0/Ls89kfG/spLtGnETVkAAAccEOAPgWMLprjsCiZQnS1YkZeW30jJjAdMG3ck8PUWgMAEKgMKcWAsBawXqm2Oy4nMpuemelDS38baAtYkkcAQ0C1AAaAalOQRMFWifHc99SPCDn0pIapSpURIXAgiEJUABEJY0+0HAAIFY3JMFH+q7muB5l/lvC3BCwICsEAIClRGgAKiMO3tFoKICNWM8+fCtqdyFhNqmMQxUNBnsHIEKCfDKrxA8u0XABIGWybHcJYWvvT0ptfUmREQMCCAQlgAFQFjS7AcBQwU8z5NzL+m7muCia6okxuRgQzNFWAjoFaAA0OtJawhYK5Cs9uSKjyXltvvTMuU8hgZrE0ngCBQowKu8QChWQ8AVgbEtMbnx7rTc8IWUNE7gU4Ku5J1+uifAyT73ck6PEShIYNrcuJw1Jy3rVmdlzWMZyXQWtBkrIYCAJQKcAbAkUYSJQCUE4glPFl/bN21w9kVcTbASOWCfCAQlQAEQlCztIhAhgboGT66/M5WbMTChnbcFIpRauuKwAAWAw8mn6wgUKzBxejx37YCln07KqNHFbs36CCBgkgAFgEnZIBYELBDwYp7MvaJv2uD8q6rEYxSxIGuEiMDpArx0TzfhEQQQKEAgXePJVTcnc/cXmDSLoaQAMlZBwCgBXrVGpYNgELBPYNzEmHzi3rQsvyspY5r4fIB9GSRiVwWYBuhq5uk3ApoFZi6skqnnx+W5JzL+V1ayXZp3QHMIIKBVgDMAWjlpDAG3BRIpT5asSMrKb6VlxgKmDbp9NNB70wUoAEzPEPEhYKFAfXNMVnwuJTfdk5LmNt4WsDCFhOyAAAWAA0mmiwhUSqB9djz3IcEPfiohqVGVioL9IoBAPgEKgHwqPIYAAtoEYnFPFnyo72qC51/mvy3ACQFttjSEQDkCFADl6LEtAggULFAzxpNlt6ZyFxJqm8bQUzAcKyIQkACvwoBgaRYBBPILtEyO5S4pfO3tSamtz78OjyKAQPACFADBG7MHBBAYJOB5npx7Sd/VBBddUyUxJiQPEuJXBIIXoAAI3pg9IIDAEALJak+u+FhSbrs/LVPOYzgagomHEQhEgFdcIKw0igACxQiMbYnJjXen5YYvpKRxAp8SLMaOdREoVYATb6XKsR0CCGgXmDY3LmfNScu61VlZ81hGMp3ad0GDCCBwUoAzABwKCCBglEA84cnia/umDc6+iKsJGpUcgomUAAVApNJJZxCIjkBdgyfX35nKzRiY0M7bAtHJLD0xRYACwJRMEAcCCOQVmDg9nrt2wNLPJGXU6Lyr8CACCJQgQAFQAhqbIIBAuAJezJO5l/dNG5x/VZV4jFzhJoC9RVKAl1Ek00qnEIimQLrGk6tuTubuLzBpFsNXNLNMr8IS4BUUljT7QQABbQLjJsbkE/emZfldSRnTxOcDtMHSkFMCTAN0Kt10FoFoCcxcWCVTz4/Lc09k/K+sZLui1T96g0CQApwBCFKXthFAIHCBRMqTJSuSsvJbaZmxgGmDgYOzg8gIUABEJpV0BAG3BeqbY7Licym56Z6UNLfxtoDbRwO9L0SAAqAQJdZBAAFrBNpnx3MfEvzgpxKSGmVN2ASKQOgCFAChk7NDBBAIWiAW92TBh/quJnj+Zf7bApwQCJqc9i0UoACwMGmEjAAChQnUjPFk2a2p3IWE2qYx3BWmxlquCPCKcCXT9BMBhwVaJsdylxS+7o6k1NY7DEHXEegnQAHQD4MfEUAgugKe58mci/uuJrjomiqJMQk6usmmZwUJUAAUxMRKCCAQFYFktSdXfCwpt92flinnMQRGJa/0o3gBjv7izdgCAQQiIDC2JSY33p2WG76QksYJfEowAimlC0UKcBKsSDBWRwCBaAlMmxuXs+akZd3qrKx5LCOZzmj1j94gMJQAZwCGkuFxBBBwRiCe8GTxtX3TBmdfxNUEnUm84x2lAHD8AKD7CCBwSqCuwZPr70zlZgxMaOdtgVMy/BRFAQqAKGaVPiGAQFkCE6fHc9cOWPqZpIwaXVZTbIyAsQIUAMamhsAQQKCSAl7Mk7mX900bnH9VlXiMlpVMB/sOQIBDOgBUmkQAgegIpGs8uermZO7+ApNmMWRGJ7P0hKOZYwABBBAoQGDcxJh84t60LP+7pIxp4vMBBZCxiuECTAM0PEGEhwACZgnMvLBKpp4Xl+eeyPhfWcl2mRUf0SBQqABnAAqVYj0EEEDgpEAi5cmSFUlZ+a20zFjAtEEODDsFKADszBtRI4CAAQL1zTFZ8bmU3HRPSprbeFvAgJQQQhECFABFYLEqAgggkE+gfXY89yHBD34qIalR+dbgMQTME6AAMC8nRIQAAhYKxOKeLPhQ39UEz7/Mf1uAEwIWZtGtkCkA3Mo3vUUAgYAFasZ4suzWVO5CQm3TGGID5qb5MgQ4OsvAY1MEEEBgKIGWybHcJYWvuyMptfVDrcXjCFROgAKgcvbsGQEEIi7geZ7MubjvaoLvv6ZKYky8jnjG7eoeBYBd+SJaBBCwUCBZ7cnlH0vKbfenZer5DLsWpjCSIXMkRjKtdAoBBEwUGNsSk7/5x7R89O+TMpqrCZqYIqdiogBwKt10FgEETBA4+4IqWfnNtCxcxk2GTMiHqzFQALiaefqNAAIVFUimPfnATcncbAH1gUEWBMIW4KgLW5z9IYAAAv0EJrTH5G+/kpLL/ibBhwT7ufBj8AIUAMEbswcEEEBgWAF1EaHF1yXklq+kpbGFKwgNi8WT2gQoALRR0hACCCBQnoA6G3DLV9Ny5kyG5vIk2boQAY6yQpRYBwEEEAhJIF3j+TMFuLlQSNxO74YCwOn003kEEDBRQH1AUL0lwIJAkAIUAEHq0jYCCCCAAAKGClAAGJoYwkIAAXcFujp7Ze3PM+4C0PNQBLgydSjM7AQBBBAoTKDzaK/829dPyLs7egvbgLUQKFGAAqBEODZDAAEEdAu882aPPPLNE9Kxmz/+um1p73QBCoDTTXgEAQQQCFWgp7tXfv+LrPznzzLSkw111+zMYQEKAIeTT9cRQKDyAuq//l98r0t2b+upfDBE4JQABYBT6aazCCBgioD6oN8z/n/8z6/OSi9/+01Ji1NxUAA4lW46iwACJghsfjErv/xBRg7t471+E/LhagwUAK5mnn4jgEDoAvt398hvftglW/7Iv/yh47PD0wQoAE4j4QEEEEBAr8CJ473yu8czsm5Vlg/56aWltTIEKADKwGNTBBBAYDiB3t5eeXlNt/z2/3XJ0YPDrclzCIQvQAEQvjl7RAABBwR2bumWX/nv8+/cyul+B9JtZRcpAKxMG0EjgICpAkcO9Mp//KRL/us/u00NkbgQyAlQAHAgIIAAAhoEurO98sKvsrmpfV2dGhqkCQQCFqAACBiY5hFAIPoCW/9Lne7v4hK+0U91pHpIARCpdNIZBBAIU4BpfWFqsy/dAhQAukVpDwEEIi/AtL7Ip9iJDlIAOJFmOokAAjoEmNanQ5E2TBGgADAlE8SBAAJGCzCtz+j0EFwJAhQAJaCxCQIIuCPAtD53cu1aTykAXMs4/UUAgYIEmNZXEBMrWSxAAWBx8ggdAQSCEdj6kj+t71+Z1heMLq2aIkABYEomiAMBBCouoKb1/dr/w7/1JS7fW/FkEEDgAhQAgROzAwQQMF0gN63vMf9ufau5W5/puSI+fQIUAPosaQkBBCwTYFqfZQkjXK0CFABaOWkMAQRsEWBany2ZIs6gBCgAgpKlXQQQMFKAaX1GpoWgKiBAAVABdHaJAALhC6hpfet/mZVnH8kId+sL3589midAAWBeTogIAQQ0CzCtTzMozUVCgAIgEmmkEwggkE+AaX35VHgMgT4BCgCOBAQQiJwA0/oil1I6FIAABUAAqDSJAAKVEWBaX2Xc2audAhQAduaNqBFAYJAA0/oGgfArAiMIUACMAMTTCCBgtgDT+szOD9GZK0ABYG5uiAwBBIYR6M740/p+xbS+YYh4CoFhBSgAhuXhSQQQMFGAaX0mZoWYbBOgALAtY8SLgMMCTOtzOPl0XbsABYB2UhpEAAHdAkzr0y1KewiIUABwFCCAgLECuWl9z3bLbx/ukqMHjQ2TwBCwUoACwMq0ETQC0RdgWl/0c0wPKytAAVBZf/aOAAKDBJjWNwiEXxEISIACICBYmkUAgeIEmNZXnBdrI1CuAAVAuYJsjwACZQswra9sQhpAoGgBCoCiydgAAQR0CTCtT5ck7SBQvAAFQPFmbIEAAmUKMK2vTEA2R0CDAAWABkSaQACBwgSY1leYE2shEIYABUAYyuwDAQSEaX0cBAiYJUABYFY+iAaByAkwrS9yKaVDERGgAIhIIukGAqYJMK3PtIwQDwIDBSgABnrwGwIIaBDY8lK3/Ppfu6Rjd6+G1mgCAQSCEKAACEKVNhFwVIBpfY4mnm5bKUABYGXaCBoBswTUtL41j2Xk+dVZ6cmaFRvRIIBAfgEKgPwuPIoAAgUIMK2vACRWQcBQAQoAQxNDWAiYLsC0PtMzRHwIDC9AATC8D88igMAgAab1DQLhVwQsFaAAsDRxhI1A2AJM6wtbnP0hEKwABUCwvrSOQCQEmNYXiTTSCQQGCFAADODgFwQQ6C/AtL7+GvyMQLQEKACilU96g4AWAab1aWGkEQSMFqAAMDo9BIdAuAJM6wvXm70hUEkBCoBK6rNvBAwSUNP6fvmDjOza2mNQVISCAAJBCVAABCVLuwhYIsC0PksSRZgIaBagANAMSnMI2CLAtD5bMkWcCAQjQAEQjCutImC0ANP6jE4PwSEQigAFQCjM7AQBMwSY1mdGHogCARMEKABMyAIxIBCwANP6AgameQQsFKAAsDBphIxAoQJqWt/GZ7vlqYe75OjBQrdiPQQQcEGAAsCFLNNHJwWY1udk2uk0AgULUAAUTMWKCNghoKb1PfXjLtn4TLcdARMlAghURIACoCLs7BQB/QJM69NvSosIRFmAAiDK2aVvzggwrc+ZVNNRBLQJUABoo6QhBMIX2L+rR379wy7Z+hKX7w1fnz0iYLcABYDd+SN6RwWY1udo4uk2AhoFKAA0YtIUAkELMK0vaGHaR8AdAQoAd3JNTy0XYFqf5QkkfAQME6AAMCwhhIPAYAGm9Q0W4XcEENAhQAGgQ5E2EAhAgGl9AaDSJAII/FWAAuCvFPyAgDkCW/7Ynft0f8fuXnOCIhIEEIiUAAVApNJJZ2wXYFqf7RkkfgTsEaAAsCdXRBphgdy0vkcz8vwvs9KTjXBH6RoCCBgjQAFgTCoIxEUBpvW5mHX6jIAZAhQAZuSBKBwUYFqfg0mnywgYJEABYFAyCMUNAab1uZFneomA6QIUAKZniPgiI6Cm9an3+Nf47/V3dUamW3QEAQQsFaAAsDRxhG2XANP67MoX0SLgggAFgAtZpo8VE2BaX8Xo2TECCIwgQAEwAhBPI1CKANP6SlFjGwQQCFOAAiBMbfYVeQGm9UU+xXQQgcgIOF8A9PR2S8yLRyahdKR0AfXH2/O8khtgWl/JdGyIQOgCaux3fXG+AOju6ZJYvNr144D++wK9PSKl1IJM6+PwQcA+ATX2u75QAPgHQYICwPXXQa7/PX4BECviZBDT+jhsELBXgAJAhAKgJ2PvEUzkWgW6/UOhKlFYk0zrK8yJtRAwVaCbsZ8CoLvnhKnHJ3GFLKA+uZ8aNfxnAA539Mgv/yUjb7zA+4chp4fdIaBVgLGfMwCS7eGSbFpfVRY3duyQyOix+TvQ29MrL/57Vv7jx/5V/I7nX4dHEUDAHgHGfgoAOZ45YM8RS6SBCuzf3SMT2mOn7WPHn7rlVz/IyK4/+x8SYEEAgUgIMPZTAEhnFwVAJF7NGjrxpz92yzmLTn0s5uC7Pbn/+F9dy+l+Dbw0gYBRAoz9FACcATDqJVnZYF75XbfUjO6SMU2ebH2pW7Zu9P/j761sTOwdAQSCETiW6QimYYtaPfXvjkVB6wz1eBcHgU5Pm9tS1wFY92TW5i4QOwIIFCjAGQB/2nOBVpFd7ThVYGRzS8cQQACBoQQY+ykA5FDnzqGODx5HAAEEEIioAGM/BYC8d+ztiB7edAsBBBBAYCgBxn4KAHnvOAXAUC8QHkcAAQSiKsDYTwEgR7v2SrabiwFF9UVOvxBAAIHBAmrMV2O/64vzHwJUB8Chzh2uHwf0HwEEEHBGgDG/L9UUAL7DviNbnDnw6SgCCCDgugBjft8RQAHgO+w7vMn11wP9RwABBJwRYMzvSzUFgO/w7hEKAGde+XQUAQScF2DM7zsEKAB8h72cAXB+QAAAAQTcEWDM78s1BYDvcPDYdsl0c49Xd17+9BQBBFwVUGO9GvNZmAZ48hjolT2HXuF4QAABBBCIuEDfWM9dvlSaOQNw8mDfeeDFiB/2dA8BBBBAYMeBF0A4KUABcBJix0EOCl4VCCCAQNQFdh7kn73/zjEFwEmJXQf/IL29nBb67wOD7wgggEDUBNQYr8Z6lj4BCoCTR0Jn5oB0HN3KcYEAAgggEFEBNcarsZ6lT4ACoN+R8FbH2n6/8SMCCCCAQJQEGOMHZpMCoJ/Hm/ue6fcbPyKAAAIIREmAMX5gNikA+nn8Zf9ayfac6PcIPyKAAAIIREFAje1qjGc5JUABcMrC/+PfKTs61vd7hB8RQAABBKIgoMZ2NcaznBKgADhlkftp276nBz3CrwgggAACtgswtp+eQQqAQSZ/2rOa6YCDTPgVAQQQsFlATf9TYzvLQAEKgIEecrhzl7x9YN2gR/kVAQQQQMBWATWmq7GdZaAABcBAj9xvr+16NM+jPIQAAgggYKMAY3r+rFEA5HHZ/M4q7g6Yx4WHEEAAAdsE1N3/1JjOcroABcDpJv4f/6Oyde9v8jzDQwgggAACNgmosVyN6SynC1AAnG6Se+S1XY8M8QwPI4AAAgjYIsBYPnSmKACGsNm+b40cObF3iGd5GAEEEEDAdAE1hquxnCW/AAVAfhfplR7ZtPvnQzzLwwgggAACpguoMVyN5Sz5BSgA8rvkHuWTo8Pg8BQCCCBguABj+PAJogAYxufdw6/LnkOvDrMGTyGAAAIImCiw59ArosZwlqEFKACGtsk9s37bAyOswdMIIIAAAqYJrN/2oGkhGRcPBcAIKdm8Z5V0HN06wlo8jQACCCBgioAas9XYzTK8AAXA8D7+s73yPGcBRlRiBQQQQMAUgb4xu9eUcIyNgwKggNS8vvtxee/42wWsySoIIIAAApUUUGO1GrNZRhagABjZyL87YLfwflIBUKyCAAIIVFhAjdVqzGYZWYACYGSj3Bqv7vypHOl8p8C1WQ0BBBBAIGwBNUarsZqlMAEKgMKcpLu3S17c/v0C12Y1BBBAAIGwBdQYrcZqlsIEKAAKc8qttXHHj+RYV0cRW7AqAggggEAYAmpsVmM0S+ECFACFW+VuEbxh+/eK2IJVEUAAAQTCEFBjs7r1L0vhAhQAhVvl1tyw/SE5cHRbkVuxOgIIIIBAUAJqTFZjM0txAhQAxXnl3l96atMXi9yK1RFAAAEEghJQYzLv/RevSwFQvJls379GNr/DVaZKoGMTBBBAQKuAGovVmMxSvAAFQPFmuS2efuM+6coeLXFrNkMAAQQQKFdAjcFqLGYpTYACoDQ3OXJit/z+z/eXuDWbIYAAAgiUK6DGYDUWs5QmQAFQmltuqw1vPeTfbvKNMlpgUwQQQACBUgTU2KvGYJbSBSgASrfLXW7yqU13l9ECmyKAAAIIlCKgxl4u+VuK3KltKABOWZT0044D6/1LTz5S0rZshAACCCBQvIAac9XYy1KeAAVAeX65rZ/Z/CU53Mn7UBooaQIBBBAYVkCNtWrMZSlfgAKgfEM5njkgT268TXq4A5UGTZpAAAEE8guoMVaNtWrMZSlfgAKgfMNcCzsPviBrt3xDU2s0gwACCCAwWECNsWqsZdEjQAGgxzHXyvo3vyNv7ntWY4s0hQACCCCgBNTYqsZYFn0CFAD6LHMtrX75dlH3pGZBAAEEENAjoMZUNbay6BWgANDr6b831SFPvrySzwNodqU5BBBwUyD3vr8/pqqxlUWvAAWAXs9cazsOPC/Pbf3nAFqmSQQQQMAtATWWqjGVRb8ABYB+01yLz2/7NjeoCMiWZhFAwA0BdZMfNZayBCNAARCMq99qr6zauFLUfapZEEAAAQSKE1Bj5yp/yp8aS1mCEaAACMY116p6z+pnG27wb1axJ8C90DQCCCAQLQE1Zqqxk/n+weaVAiBYXznUuUMe3XCjnMgcCnhPNI8AAgjYL6DGSjVmqrGTJVgBCoBgfXOtv3tkkzz+0icl290Zwt7YBQIIIGCngBoj1VipxkyW4AUoAII3zu1B3bjiyZe5XHBI3OwGAQQsE+ib7ncbN/kJMW8UACFib937G/n31/4hxD2yKwQQQMAOATU2qjGSJTyBeDKVvi+83bGnvYdf9S8SlJUzxy4GAwEEEEDAF/jdlq/JH//yL1iELEABEDK42p16OyBZVSut9fMqsHd2iQACCJgj8OL278varV83JyCHIqEAqFCyt+9/Nne54DPHvr9CEbBbBBBAoLICv9vydf74VzAFXk1dPVdZqGACZrfeIB8452sS8+IVjIJdI4AAAuEJqA/8qff8X9n5k/B2yp5OE6AAOI0k/AemjLtSls35rlTF0+HvnD0igAACIQqoqX5qRhQf+AsRfYhdUQAMARP2w20NC+T6834oqcTosHfN/hBAAIFQBNRFftQ8f/U5KJbKC1AAVD4Hf42guXaGfGTew1KbGv/Xx/gBAQQQiIKAuryvusIfF/kxJ5sUAObkIhfJ6HSbrJj3E2momWxYZISDAAIIlCagbuyjru3P5X1L8wtqKy4EFJRsie2qF8jD66/hVsIl+rEZAgiYJaBu6avGNP74m5UXFQ1nAMzLycmIPFk4+bOyaMpdzBAwNkcEhgACQwmoT/r/fuv9sm7b//VXYbLZUE6VfJwCoJL6Bey7reFCf4bAg1KbnlDA2qyCAAIIVF7gSOc7/if9V/of9nu+8sEQwZACFABD0pjzRHWiUa6e84C0N11iTlBEggACCOQReHPfs7L65dvleKYjz7M8ZJIABYBJ2RghlgXtd8jiqZ/nLYERnHgaAQTCF1Cn/Ndu+Yasf/M74e+cPZYkQAFQElvlNmqtny/Lzv2u1KVbKhcEe0YAAQT6CRzu3C1PbrxNdh58od+j/Gi6AAWA6RnKE191okGWTL9XZrUuz/MsDyGAAALhCby68xF5ZvOX/FP+B8LbKXvSIkABoIWxMo2oqwdeMfOr0lQ7vTIBsFcEEHBWYN+RzfLb1/+Rq/pZfARQAFicPBW6599EaN6Zt8iis+7ybzFcY3lvCB8BBEwX6Moeld//+X7Z8NZD0uu/789irwAFgL25GxB5bapFLj37Ppk+YemAx/kFAQQQ0CWw+Z1V8vQb98mRE7t1NUk7FRSgAKggfhC7njT2Yrl8xpe5lHAQuLSJgKMC6lK+T236IlcojVj+KQAillDVnbiXlHmTbvG/bpVRycYI9pAuIYBAGALHujpkw/bv+V8PSXdvVxi7ZB8hClAAhIgd9q4S8Wo5t+3jcsGkz3AlwbDx2R8CFguoK/m9uP37snHHjyTTfdzinhD6cAIUAMPpROQ5dUZgVutHZcHklTKmemJEekU3EEBAt8B7x9+W9dselFd3/pT/+HXjGtgeBYCBSQkqJDVjYGbL9XLh5NulsWZKULuhXQQQsEyg4+hWeX7bA/L67sf5ZL9luSsnXAqAcvSs3daT6eOX+mcEbpfxo2dZ2wsCRwCB8gT2HHol9x//5j2r/Ia4Y195mvZtTQFgX860RtxcN1POOeMjMqPlOqlNjdPaNo0hgIB5AkdO7JVNu38ur+16VN49/Lp5ARJRaAIUAKFRm70jT2IyqelivxhYLlPGXSnqA4QsCCAQDQH1Qb6te3/j/9F/RLbvW+P/r98TjY7Ri7IEKADK4ovmxol4Te6CQurMwMSGhf7VBr1odpReIRBhgd7eXnn7wLrcf/rqAj6Z7qMR7i1dK0WAAqAUNYe2qUufIdPGXy2Tmy6VtsYFUhVLOdR7uoqAXQLZnhOyo2O9bNv3tPxpz2o53LnLrg4QbagCFAChctu9s6pYWt43drG0Ny2RMxsX52YScHbA7pwSvd0C6r989Qn+tzrWypv7npG/7F8r2Z5OuztF9KEJUACERh29HaX92xK31s/zvy6Q1oYL/BkFs/nsQPTSTI8MElDv5atP7u888KLsPKi+Nkgnt+E1KEN2hUIBYFe+DI/Wk/pRk2Rc3QxprvW//BkGY2unyOh0m1TF04bHTngImCOQ7e6UQ507ZP+RrblP6r97ZJPsPbxJDh7b7gfJdD1zMmV3JBQAdufPmuhrkuNyVyEcM2qiXxC0SnWiUar9+xRU+2cR1Pd0oj539iDmX7UwHkv4X+p7UmL+xYtYELBVoMe/XW53T9fJr4z0+NfTV//Fd2YOynH/OvvH/f/e+753+H/wd8p7x94WdTW+o117be0ycVskQAFgUbIIFQEEEEAAAV0CMV0N0Q4CCCCAAAII2CNAAWBProgUAQQQQAABbQIUANooaQgBBBBAAAF7BCgA7MkVkSKAAAIIIKBNgAJAGyUNIYAAAgggYI8ABYA9uSJSBBBAAAEEtAlQAGijpCEEEEAAAQTsEaAAsCdXRIoAAggggIA2AQoAbZQ0hAACCCCAgD0CFAD25IpIEUAAAQQQ0CZAAaCNkoYQQAABBBCwR4ACwJ5cESkCCCCAAALaBCgAtFHSEAIIIIAAAvYIUADYkysiRQABBBBAQJsABYA2ShpCAAEEEEDAHgEKAHtyRaQIIIAAAghoE6AA0EZJQwgggAACCNgjQAFgT66IFAEEEEAAAW0CFADaKGkIAQQQQAABewQoAOzJFZEigAACCCCgTYACQBslDSGAAAIIIGCPAAWAPbkiUgQQQAABBLQJUABoo6QhBBBAAAEE7BGgALAnV0SKAAIIIICANgEKAG2UNIQAAggggIA9AhQA9uSKSBFAAAEEENAmQAGgjZKGEEAAAQQQsEeAAsCeXBEpAggggAAC2gQoALRR0hACCCCAAAL2CFAA2JMrIkUAAQQQQECbAAWANkoaQgABBBBAwB4BCgB7ckWkCCCAAAIIaBOgANBGSUMIIIAAAgjYI0ABYE+uiBQBBBBAAAFtAhQA2ihpCAEEEEAAAXsEKADsyRWRIoAAAgggoE2AAkAbJQ0hgAACCCBgjwAFgD25IlIEEEAAAQS0CVAAaKOkIQQQQAABBOwRoACwJ1dEigACCCCAgDYBCgBtlDSEAAIIIICAPQIUAPbkikgRQAABBBDQJkABoI2ShhBAAAEEELBHgALAnlwRKQIIIIAAAtoEKAC0UdIQAggggAAC9ghQANiTKyJFAAEEEEBAmwAFgDZKGkIAAQQQQMAeAQoAe3JFpAgggAACCGgToADQRklDCCCAAAII2CNAAWBProgUAQQQQAABbQIUANooaQgBBBBAAAF7BCgA7MkVkSKAAAIIIKBNgAJAGyUNIYAAAgggYI8ABYA9uSJSBBBAAAEEtAlQAGijpCEEEEAAAQTsEaAAsCdXRIoAAggggIA2AQoAbZQ0hAACCCCAgD0CFAD25IpIEUAAAQQQ0CZAAaCNkoYQQAABBBCwR4ACwJ5cESkCCCCAAALaBCgAtFHSEAIIIIAAAvYIUADYkysiRQABBBBAQJsABYA2ShpCAAEEEEDAHoH/D9Yack/1zP+zAAAAAElFTkSuQmCC', 'icon-512.png': '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', 'manifest.webmanifest': 'ewogICJuYW1lIjogIkZyYW1lIiwKICAic2hvcnRfbmFtZSI6ICJGcmFtZSIsCiAgImRlc2NyaXB0aW9uIjogIllvdXIgZmlsbXMgYW5kIHNlcmllcywgYmVhdXRpZnVsbHkgaW4gb25lIHBsYWNlLiIsCiAgImlkIjogIi93YXRjaCIsCiAgInN0YXJ0X3VybCI6ICIvd2F0Y2giLAogICJzY29wZSI6ICIvIiwKICAiZGlzcGxheSI6ICJzdGFuZGFsb25lIiwKICAib3JpZW50YXRpb24iOiAiYW55IiwKICAiYmFja2dyb3VuZF9jb2xvciI6ICIjMGIwZDEwIiwKICAidGhlbWVfY29sb3IiOiAiIzBiMGQxMCIsCiAgImljb25zIjogWwogICAgewogICAgICAic3JjIjogIi9wd2EvaWNvbi0xOTIucG5nP3Y9NCIsCiAgICAgICJzaXplcyI6ICIxOTJ4MTkyIiwKICAgICAgInR5cGUiOiAiaW1hZ2UvcG5nIgogICAgfSwKICAgIHsKICAgICAgInNyYyI6ICIvcHdhL2ljb24tNTEyLnBuZz92PTQiLAogICAgICAic2l6ZXMiOiAiNTEyeDUxMiIsCiAgICAgICJ0eXBlIjogImltYWdlL3BuZyIKICAgIH0sCiAgICB7CiAgICAgICJzcmMiOiAiL3B3YS9pY29uLTUxMi1tYXNrYWJsZS5wbmc/dj00IiwKICAgICAgInNpemVzIjogIjUxMng1MTIiLAogICAgICAidHlwZSI6ICJpbWFnZS9wbmciLAogICAgICAicHVycG9zZSI6ICJtYXNrYWJsZSIKICAgIH0KICBdLAogICJjYXRlZ29yaWVzIjogWwogICAgImVudGVydGFpbm1lbnQiCiAgXSwKICAibGFuZyI6ICJlbiIsCiAgInNob3J0Y3V0cyI6IFsKICAgIHsKICAgICAgIm5hbWUiOiAiU2VhcmNoIiwKICAgICAgInVybCI6ICIvd2F0Y2gjL3NlYXJjaCIKICAgIH0sCiAgICB7CiAgICAgICJuYW1lIjogIk15IExpYnJhcnkiLAogICAgICAidXJsIjogIi93YXRjaCMvbGlicmFyeSIKICAgIH0sCiAgICB7CiAgICAgICJuYW1lIjogIk1vdmllcyIsCiAgICAgICJ1cmwiOiAiL3dhdGNoIy9tb3ZpZXMiCiAgICB9CiAgXQp9Cg==', 'sw.js': 'Ly8gRnJhbWUga2VlcHMgaXRzIGludGVyZmFjZSBhdmFpbGFibGUgb2ZmbGluZS4gTWVkaWEsIHBsYXlsaXN0cywgQVBJIHJlc3BvbnNlcwovLyBhbmQgc2lnbmVkIHNvdXJjZSBVUkxzIGFsd2F5cyBnbyBkaXJlY3RseSB0byB0aGUgbmV0d29yay4KY29uc3QgVkVSU0lPTiA9ICIyMDI2LTExLTEzLWZyYW1lLTciOwovLyBLZWVwIHRoZSBvcmlnaW5hbCBjYWNoZSBsaW5lYWdlIHNvIGV4aXN0aW5nIGluc3RhbGxhdGlvbnMgbWlncmF0ZSBzYWZlbHkuCmNvbnN0IFBBR0VfQ0FDSEUgPSAiZmxlZXQtc2hlbGwtIiArIFZFUlNJT047CmNvbnN0IEFTU0VUX0NBQ0hFID0gImZsZWV0LWFzc2V0cy0iICsgVkVSU0lPTjsKY29uc3QgUEFHRSA9ICIvd2F0Y2giLCBBU1NFVF9MSU1JVCA9IDEyOwpjb25zdCBJQ09OUyA9IFsiL3B3YS9hcHBsZS10b3VjaC1pY29uLnBuZyIsICIvcHdhL2ljb24tMTkyLnBuZyIsICIvcHdhL2ljb24tNTEyLnBuZyIsICIvcHdhL2ljb24tNTEyLW1hc2thYmxlLnBuZyJdOwpjb25zdCBvd25zQ2FjaGUgPSAoa2V5KSA9PiBrZXkgPT09ICJmbGVldC0xIiB8fCBrZXkuc3RhcnRzV2l0aCgiZmxlZXQtc2hlbGwtIikgfHwga2V5LnN0YXJ0c1dpdGgoImZsZWV0LWFzc2V0cy0iKTsKY29uc3QgaXNBc3NldCA9ICh1cmwpID0+IHVybC5wYXRobmFtZSA9PT0gIi9tYW5pZmVzdC53ZWJtYW5pZmVzdCIgfHwgSUNPTlMuaW5jbHVkZXModXJsLnBhdGhuYW1lKSB8fAogIC9eXC9nd1wvY2RuXC5qc2RlbGl2clwubmV0XC9ucG1cL2hsc1wuanNAXGQrXC5cZCtcLlxkK1wvZGlzdFwvaGxzXC5taW5cLmpzJC8udGVzdCh1cmwucGF0aG5hbWUpOwoKYXN5bmMgZnVuY3Rpb24ga2VlcChjYWNoZU5hbWUsIGtleSwgcmVzcG9uc2UpIHsKICBpZiAoIXJlc3BvbnNlIHx8ICFyZXNwb25zZS5vayB8fCByZXNwb25zZS5zdGF0dXMgIT09IDIwMCB8fCByZXNwb25zZS50eXBlID09PSAib3BhcXVlIiB8fCByZXNwb25zZS5oZWFkZXJzLmhhcygiQ29udGVudC1SYW5nZSIpKSByZXR1cm47CiAgY29uc3QgY29weSA9IHJlc3BvbnNlLmNsb25lKCk7CiAgY29uc3QgY2FjaGUgPSBhd2FpdCBjYWNoZXMub3BlbihjYWNoZU5hbWUpOwogIGF3YWl0IGNhY2hlLnB1dChrZXksIGNvcHkpOwogIGlmIChjYWNoZU5hbWUgPT09IEFTU0VUX0NBQ0hFKSB7CiAgICBjb25zdCBrZXlzID0gYXdhaXQgY2FjaGUua2V5cygpOwogICAgYXdhaXQgUHJvbWlzZS5hbGwoa2V5cy5zbGljZSgwLCBNYXRoLm1heCgwLCBrZXlzLmxlbmd0aCAtIEFTU0VUX0xJTUlUKSkubWFwKChvbGQpID0+IGNhY2hlLmRlbGV0ZShvbGQpKSk7CiAgfQp9CnNlbGYuYWRkRXZlbnRMaXN0ZW5lcigiaW5zdGFsbCIsIChldmVudCkgPT4gewogIC8vIEEgbmV3IHdvcmtlciB3YWl0cyBmb3IgYW4gZXhwbGljaXQgdXBkYXRlIGFjdGlvbjsgaXQgbmV2ZXIgcmVsb2FkcyBwbGF5YmFjay4KICBldmVudC53YWl0VW50aWwoUHJvbWlzZS5hbGwoW1BBR0UsICIvbWFuaWZlc3Qud2VibWFuaWZlc3QiLCAuLi5JQ09OUy5tYXAoKHBhdGgpID0+IHBhdGggKyAiP3Y9NCIpXS5tYXAoYXN5bmMgKHBhdGgpID0+IHsKICAgIHRyeSB7CiAgICAgIGNvbnN0IHJlc3BvbnNlID0gYXdhaXQgZmV0Y2gocGF0aCwge2NhY2hlOiAicmVsb2FkIn0pOwogICAgICBhd2FpdCBrZWVwKHBhdGggPT09IFBBR0UgPyBQQUdFX0NBQ0hFIDogQVNTRVRfQ0FDSEUsIHBhdGgsIHJlc3BvbnNlKTsKICAgIH0gY2F0Y2ggKF8pIHsgLyogQW4gZXhpc3RpbmcgaW5zdGFsbGF0aW9uIHJlbWFpbnMgdXNhYmxlIGlmIG9uZSBmZXRjaCBmYWlscy4gKi8gfQogIH0pKSk7Cn0pOwpzZWxmLmFkZEV2ZW50TGlzdGVuZXIoImFjdGl2YXRlIiwgKGV2ZW50KSA9PiB7CiAgZXZlbnQud2FpdFVudGlsKChhc3luYyAoKSA9PiB7CiAgICBjb25zdCBrZXlzID0gYXdhaXQgY2FjaGVzLmtleXMoKSwgY3VycmVudCA9IGF3YWl0IGNhY2hlcy5vcGVuKFBBR0VfQ0FDSEUpOwogICAgLy8gQSBmYWlsZWQgaW5zdGFsbC10aW1lIGZldGNoIG11c3Qgbm90IHRocm93IGF3YXkgdGhlIGxhc3QgdXNhYmxlIG9mZmxpbmUgc2hlbGwuCiAgICBpZiAoIWF3YWl0IGN1cnJlbnQubWF0Y2goUEFHRSkpIHsKICAgICAgZm9yIChjb25zdCBrZXkgb2Yga2V5cy5zbGljZSgpLnJldmVyc2UoKS5maWx0ZXIoKGtleSkgPT4ga2V5ICE9PSBQQUdFX0NBQ0hFICYmIChrZXkgPT09ICJmbGVldC0xIiB8fCBrZXkuc3RhcnRzV2l0aCgiZmxlZXQtc2hlbGwtIikpKSkgewogICAgICAgIGNvbnN0IHByZXZpb3VzID0gYXdhaXQgKGF3YWl0IGNhY2hlcy5vcGVuKGtleSkpLm1hdGNoKFBBR0UpOwogICAgICAgIGlmIChwcmV2aW91cykgeyBhd2FpdCBrZWVwKFBBR0VfQ0FDSEUsIFBBR0UsIHByZXZpb3VzKTsgYnJlYWs7IH0KICAgICAgfQogICAgfQogICAgYXdhaXQgUHJvbWlzZS5hbGwoa2V5cy5maWx0ZXIoKGtleSkgPT4gb3duc0NhY2hlKGtleSkgJiYga2V5ICE9PSBQQUdFX0NBQ0hFICYmIGtleSAhPT0gQVNTRVRfQ0FDSEUpLm1hcCgoa2V5KSA9PiBjYWNoZXMuZGVsZXRlKGtleSkpKTsKICAgIGF3YWl0IHNlbGYuY2xpZW50cy5jbGFpbSgpOwogIH0pKCkpOwp9KTsKc2VsZi5hZGRFdmVudExpc3RlbmVyKCJtZXNzYWdlIiwgKGV2ZW50KSA9PiB7CiAgaWYgKGV2ZW50LmRhdGEgJiYgZXZlbnQuZGF0YS50eXBlID09PSAiRkxFRVRfQUNUSVZBVEVfVVBEQVRFIikgZXZlbnQud2FpdFVudGlsKHNlbGYuc2tpcFdhaXRpbmcoKSk7Cn0pOwpjb25zdCBvZmZsaW5lUGFnZSA9ICgpID0+IG5ldyBSZXNwb25zZSgnPCFkb2N0eXBlIGh0bWw+PGh0bWwgbGFuZz0iZW4iPjxtZXRhIGNoYXJzZXQ9InV0Zi04Ij48bWV0YSBuYW1lPSJ2aWV3cG9ydCIgY29udGVudD0id2lkdGg9ZGV2aWNlLXdpZHRoLGluaXRpYWwtc2NhbGU9MSI+PG1ldGEgbmFtZT0idGhlbWUtY29sb3IiIGNvbnRlbnQ9IiMwYjBkMTAiPjx0aXRsZT5GcmFtZSDCtyBPZmZsaW5lPC90aXRsZT48c3R5bGU+Ym9keXttYXJnaW46MDttaW4taGVpZ2h0OjEwMHZoO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7YmFja2dyb3VuZDojMGIwZDEwO2NvbG9yOiNmM2Y1Zjc7Zm9udDoxNnB4LzEuNiBzeXN0ZW0tdWksc2Fucy1zZXJpZn1tYWlue21heC13aWR0aDoyOHJlbTtwYWRkaW5nOjMycHh9cHtjb2xvcjojYjNiYmM1fWF7ZGlzcGxheTppbmxpbmUtYmxvY2s7bWFyZ2luLXRvcDoxNnB4O3BhZGRpbmc6MTJweCAyMHB4O2JvcmRlci1yYWRpdXM6MTJweDtiYWNrZ3JvdW5kOiNhNzliY2M7Y29sb3I6IzBiMGQxMDt0ZXh0LWRlY29yYXRpb246bm9uZTtmb250LXdlaWdodDo2NTB9YTpmb2N1cy12aXNpYmxle291dGxpbmU6MnB4IHNvbGlkICNiOGFjZDk7b3V0bGluZS1vZmZzZXQ6NHB4fTwvc3R5bGU+PG1haW4+PGgxPllvdeKAmXJlIG9mZmxpbmU8L2gxPjxwPlJlY29ubmVjdCB0byBicm93c2UgYW5kIHBsYXkuIFlvdXIgc2F2ZWQgbGlicmFyeSBhbmQgd2F0Y2ggcHJvZ3Jlc3Mgc3RheSBvbiB0aGlzIGRldmljZS48L3A+PGEgaHJlZj0iL3dhdGNoIj5UcnkgYWdhaW48L2E+PC9tYWluPjwvaHRtbD4nLCB7c3RhdHVzOiAyMDAsIGhlYWRlcnM6IHsiQ29udGVudC1UeXBlIjogInRleHQvaHRtbDsgY2hhcnNldD11dGYtOCIsICJDYWNoZS1Db250cm9sIjogIm5vLXN0b3JlIn19KTsKYXN5bmMgZnVuY3Rpb24gcGFnZVJlc3BvbnNlKGV2ZW50KSB7CiAgY29uc3QgY29udHJvbGxlciA9IG5ldyBBYm9ydENvbnRyb2xsZXIoKTsKICBjb25zdCB0aW1lciA9IHNldFRpbWVvdXQoKCkgPT4gY29udHJvbGxlci5hYm9ydCgpLCA1MDAwKTsKICB0cnkgewogICAgY29uc3QgcmVzcG9uc2UgPSBhd2FpdCBmZXRjaChldmVudC5yZXF1ZXN0LCB7Y2FjaGU6ICJuby1zdG9yZSIsIHNpZ25hbDogY29udHJvbGxlci5zaWduYWx9KTsKICAgIGlmIChyZXNwb25zZS5vaykgewogICAgICBldmVudC53YWl0VW50aWwoa2VlcChQQUdFX0NBQ0hFLCBQQUdFLCByZXNwb25zZSkuY2F0Y2goKCkgPT4ge30pKTsKICAgICAgcmV0dXJuIHJlc3BvbnNlOwogICAgfQogICAgY29uc3QgY2FjaGVkID0gYXdhaXQgKGF3YWl0IGNhY2hlcy5vcGVuKFBBR0VfQ0FDSEUpKS5tYXRjaChQQUdFKTsKICAgIHJldHVybiBjYWNoZWQgfHwgcmVzcG9uc2U7CiAgfSBjYXRjaCAoXykgewogICAgcmV0dXJuIGF3YWl0IChhd2FpdCBjYWNoZXMub3BlbihQQUdFX0NBQ0hFKSkubWF0Y2goUEFHRSkgfHwgb2ZmbGluZVBhZ2UoKTsKICB9IGZpbmFsbHkgeyBjbGVhclRpbWVvdXQodGltZXIpOyB9Cn0Kc2VsZi5hZGRFdmVudExpc3RlbmVyKCJmZXRjaCIsIChldmVudCkgPT4gewogIGNvbnN0IHVybCA9IG5ldyBVUkwoZXZlbnQucmVxdWVzdC51cmwpOwogIGlmICh1cmwub3JpZ2luICE9PSBzZWxmLmxvY2F0aW9uLm9yaWdpbiB8fCBldmVudC5yZXF1ZXN0Lm1ldGhvZCAhPT0gIkdFVCIgfHwgZXZlbnQucmVxdWVzdC5oZWFkZXJzLmhhcygiUmFuZ2UiKSkgcmV0dXJuOwogIGlmICh1cmwucGF0aG5hbWUgPT09IFBBR0UgfHwgdXJsLnBhdGhuYW1lID09PSBQQUdFICsgIi8iKSBldmVudC5yZXNwb25kV2l0aChwYWdlUmVzcG9uc2UoZXZlbnQpKTsKICBlbHNlIGlmIChpc0Fzc2V0KHVybCkpIGV2ZW50LnJlc3BvbmRXaXRoKChhc3luYyAoKSA9PiB7CiAgICBjb25zdCBjYWNoZWQgPSBhd2FpdCAoYXdhaXQgY2FjaGVzLm9wZW4oQVNTRVRfQ0FDSEUpKS5tYXRjaChldmVudC5yZXF1ZXN0KTsKICAgIGlmIChjYWNoZWQpIHJldHVybiBjYWNoZWQ7CiAgICBjb25zdCByZXNwb25zZSA9IGF3YWl0IGZldGNoKGV2ZW50LnJlcXVlc3QpOwogICAgZXZlbnQud2FpdFVudGlsKGtlZXAoQVNTRVRfQ0FDSEUsIGV2ZW50LnJlcXVlc3QsIHJlc3BvbnNlKS5jYXRjaCgoKSA9PiB7fSkpOwogICAgcmV0dXJuIHJlc3BvbnNlOwogIH0pKCkpOwp9KTsK'}
FILE_TYPES = {"mkv": "video/x-matroska", "mp4": "video/mp4", "m4v": "video/x-m4v", "webm": "video/webm", "avi": "video/x-msvideo", "ts": "video/mp2t", "m2ts": "video/mp2t"}


def web_file(name):

    if WEB_FILES is not None:
        return base64.b64decode(WEB_FILES[name])
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "pwa", name), "rb") as fh:
        return fh.read()


def watch_page():

    if WATCH_PAGE is not None:
        return WATCH_PAGE
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def part(name):
        with open(os.path.join(here, name), encoding="utf-8") as fh:
            return fh.read()
    body = part("body.html").replace("<!-- TOR_PLAYER -->", part("player.html"))
    scripts = "".join(part(name) for name in ("core.js", "versions.js", "player.js", "pwa/client.js"))
    return part("head.html") + body + "<script>" + scripts + "</script>\n</body>\n</html>\n"


def main():
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(SEG_DIR, exist_ok=True)
    piece_load()
    for name in os.listdir(CACHE):
        if not os.path.exists(f"{CACHE}/{name}/{HAVE}"):
            shutil.rmtree(f"{CACHE}/{name}", ignore_errors=True)
    log(f"{APP} copy {REPLICA}: tor.py {CODE}, page on :{PAGE_PORT}, helper on :{HELPER_PORT}, {CACHE_BYTES / 2**30:.0f} GB of RAM for chunks")
    for target in (scan_copies, keep_loop, heartbeat_loop, helper_loop, front_loop, torrserver_loop, trackers_loop) + (facts_worker,) * FACTS_WORKERS:
        threading.Thread(target=target, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PAGE_PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
