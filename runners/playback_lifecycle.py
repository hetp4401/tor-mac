"""Embedded into tor.py by web/build.py; no extra runtime deployment file."""

PLAYBACK_TTL = 45
PLAYBACK_TOMBSTONE = 600
playback_lock = threading.RLock()
playback_sessions = {}
playback_epochs = collections.defaultdict(int)
playback_stopped = set()
playback_local = threading.local()
media_process_lock = threading.Lock()
media_processes = {}
playback_demands = collections.Counter()
playback_demand_lock = threading.Lock()


def work_check(key):
    key = key.split(":", 1)[0]
    epoch = getattr(playback_local, "epochs", {}).get(key, playback_epochs[key])
    if key in playback_stopped or epoch != playback_epochs[key]:
        raise Dropped("playback was stopped or replaced")
    request = getattr(playback_local, "request", None)
    if request and request["key"] == key and request["n"] >= 0:
        s = playback_sessions.get(request["sid"], {})
        if s.get("closed"):
            raise Dropped("media request belongs to a stopped session")


def playback_requested(key, n):
    # Native HLS may finish its current fragment before issuing the latest
    # seek. Truncating that response can strand the native request queue.
    with playback_demand_lock:
        return playback_demands.get((key, n), 0) > 0


@contextlib.contextmanager
def playback_request(key, n, query):
    sid = (query.get("sid") or [""])[0]
    s = playback_sessions.get(sid, {})
    previous = getattr(playback_local, "request", None)
    tracked = bool(s.get("managed") and n >= 0)
    if tracked:
        playback_local.request = {"key": key, "n": n, "sid": sid}
        with playback_demand_lock:
            playback_demands[(key, n)] += 1
    try:
        work_check(key)
        yield
    finally:
        if tracked:
            with playback_demand_lock:
                playback_demands[(key, n)] -= 1
                if not playback_demands[(key, n)]:
                    del playback_demands[(key, n)]
        playback_local.request = previous


def playback_position_report(key, token, s, seconds, seq, seek=False):
    """Player progress owns the playhead; downloads and stale controls do not."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("invalid position")
    with playback_lock:
        old_seq = s.get("seek_seq", 0)
        if (seek and seq <= old_seq) or (not seek and (not s.get("managed") or seq < old_seq)):
            return
        # A progress report also repairs a lost seek control once playback
        # resumes. Reports from earlier seeks can never supersede it.
        seek = seek or seq > old_seq
        if not seek and seconds + .5 < s.get("position", 0):
            return
        mode = s.get("mode", "converted")
        if mode == "as-is":
            return
        orig = mode in ("original", "remux")
        n = playback_position(probed[key], seconds, orig)
        s.update(position=seconds, position_seen=time.monotonic())
        if seek:
            s.update(seek_seq=seq, seek_head=n)
        film_heard(key, token, n, "remux" if mode == "remux" else "orig" if orig else "conv")


def media_task(fn):
    def run(key, *args, **kwargs):
        base = key.split(":", 1)[0]
        previous = getattr(playback_local, "epochs", {})
        playback_local.epochs = {**previous, base: previous.get(base, playback_epochs[base])}
        try:
            work_check(base)
            return fn(key, *args, **kwargs)
        finally:
            playback_local.epochs = previous
    run.__name__ = fn.__name__
    return run


def playback_users(key):
    now = time.monotonic()
    return sum(s["key"] == key and not s["closed"] and now - s["seen"] < PLAYBACK_TTL
               for s in list(playback_sessions.values()))


def playback_touch(key, sid, mode=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", sid):
        raise ValueError("invalid playback session")
    with playback_lock:
        s = playback_sessions.get(sid)
        if s and (s["closed"] or s["key"] != key):
            raise Dropped("this playback session has ended")
        if s is None:
            if len(playback_sessions) >= 4096:
                raise ValueError("too many playback sessions")
            s = playback_sessions[sid] = {"key": key, "closed": False, "seen": time.monotonic()}
            s["expired"] = True
        if s.pop("expired", False):
            playback_stopped.discard(key)
            if films.get(key, {}).get("stopped"):
                films.pop(key, None)
        s["seen"] = time.monotonic()
        if mode:
            s["mode"] = mode
        return s


def stop_film(key):
    """Called with playback_lock held; finished cache entries remain reusable."""
    playback_stopped.add(key)
    playback_epochs[key] += 1
    f = films.get(key)
    if f:
        f.update(stopped=True, seen=0)
        f["wake"].set()
    with cpu_cond:
        for (k, _), job in list(cpu_jobs.items()):
            if k.split(":", 1)[0] == key:
                job["dropped"] = True
                if job["p"]:
                    try:
                        job["p"].kill()
                    except OSError:
                        pass
        cpu_cond.notify_all()
    with media_process_lock:
        for p, k in list(media_processes.items()):
            if k == key:
                try:
                    p.kill()
                except OSError:
                    pass
    calls = []
    with mac_lock:
        for (k, _), job in list(mac_jobs.items()):
            if k == key:
                job["dropped"] = True
                job["news"].put(None)
                calls.extend(job["calls"])
    with hls_lock:
        calls.extend(r for r in list(runs.values()) if r["key"] == key)
        calls.extend(src_meta.get(key, {}).get("lookup_calls", []))
    for call in calls:
        end_call(call)
    with turns:
        turns.notify_all()


def playback_release(key, sid):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", sid):
        raise ValueError("invalid playback session")
    with playback_lock:
        s = playback_sessions.get(sid)
        if s and s["key"] != key:
            raise ValueError("session does not belong to this stream")
        # Closing can beat the first playlist: its late arrival must stay closed.
        playback_sessions[sid] = {"key": key, "closed": True, "seen": time.monotonic()}
        for call in list(runs.values()):
            if call.get("sid") == sid:
                end_call(call)
        legacy = time.time() - src_meta.get(key, {}).get("legacy_seen", 0) < PLAYBACK_TTL
        if not playback_users(key) and not legacy:
            stop_film(key)


def playback_sweep():
    with playback_lock:
        now = time.monotonic()
        for sid, s in list(playback_sessions.items()):
            if s["closed"] or s.get("expired"):
                if now - s["seen"] > PLAYBACK_TOMBSTONE:
                    playback_sessions.pop(sid, None)
            elif now - s["seen"] >= PLAYBACK_TTL:
                # Safari may suspend JS while buffered video keeps playing in
                # PiP. Idle work is cancelled, but later media demand may renew
                # this lease. Explicitly stopped leases never renew.
                s["expired"] = True
                key = s["key"]
                legacy = time.time() - src_meta.get(key, {}).get("legacy_seen", 0) < PLAYBACK_TTL
                if not playback_users(key) and not legacy:
                    stop_film(key)


def playback_loop():
    while True:
        playback_sweep()
        time.sleep(2)


def media_run(key, cmd, body=None, timeout=60):
    """Track probe/remux/audio processes so switching stops those jobs too."""
    with media_process_lock:
        work_check(key)
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE if body is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        media_processes[p] = key
    try:
        out, err = p.communicate(body, timeout=timeout)
        work_check(key)
        return subprocess.CompletedProcess(cmd, p.returncode, out, err)
    except BaseException:
        p.kill()
        p.communicate()
        raise
    finally:
        with media_process_lock:
            media_processes.pop(p, None)


def playback_query(query):
    return "".join("&" + urllib.parse.urlencode({k: query[k][0]})
                   for k in ("sid", "start", "force", "cap", "managed") if query.get(k))


def playback_position(info, seconds, orig=False):
    cuts = info["ocuts" if orig else "cuts"]
    return max(0, min(len(cuts) - 2, bisect.bisect_right(cuts, max(0, seconds)) - 1))


def playback_mode(info, requested, force=False, hevc=False):
    """Actual metadata wins over filenames. Reuse compatible video."""
    if force:
        return "converted"
    if requested == "original" and not orig_unfit(info):
        return "original"
    if (info.get("opos") and info.get("vcodec") == "h264"
            and info.get("vprofile") in ("Baseline", "Constrained Baseline", "Main", "High")
            and not info.get("hdr") and info.get("w", 0) <= 1920):
        return "remux"
    if requested == "as-is":
        f = facts_of(info)
        if f.get("a") and (info.get("vcodec") == "h264" or hevc and info.get("vcodec") == "hevc"):
            return "as-is"
        if not orig_unfit(info) and (info.get("vcodec") == "h264" or hevc):
            return "remux"
    return "converted"
