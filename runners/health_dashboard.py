"""Embedded health dashboard: bounded, shared reads; no monitoring on the media path."""

HEALTH_RUNNERS = os.environ.get("RUNNERS_URL", "https://runners.billybishop4-workers.xyz").rstrip("/")
HEALTH_INTERVAL = 15
HEALTH_MAC_HISTORY = ("jobs", "latency_ms", "conversion_s", "cpu_percent", "memory_percent", "network_rx", "network_tx",
                      "gpu_percent", "memory_used", "memory_total", "disk_percent", "disk_free", "disk_read", "disk_write",
                      "gpu_memory_used", "gpu_memory_allocated", "gpu_renderer_percent", "gpu_tiler_percent", "cpu_per_core")
health_lock = threading.Lock()
health_state = {"sources": {}, "history": {}, "refreshing": False, "attempted": 0}


def health_number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def health_percent(used, total):
    used, total = health_number(used), health_number(total)
    return round(used / total * 100, 1) if used is not None and total and total > 0 else None


def health_age(now, stamp, milliseconds=False):
    stamp = health_number(stamp)
    return max(0, now - stamp / (1000 if milliseconds else 1)) if stamp else None


def health_read(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Frame-health/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=4) as response:
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("Metrics response too large")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Invalid metrics response")
        return value


def health_error(error):
    if isinstance(error, urllib.error.HTTPError):
        return "HTTP " + str(error.code)
    return "Connection timed out" if isinstance(error, (TimeoutError, socket.timeout)) else "Service unreachable"


def health_refresh():
    targets = {"fleet": HEALTH_RUNNERS + "/api/status", "metrics": HEALTH_RUNNERS + "/api/metrics?range=1h"}
    targets.update({"mac:" + mac_name(url): url + "/healthz" for url in MAC_URLS})
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5, thread_name_prefix="health") as pool:
            tasks = {pool.submit(health_fetch_timed, url): name for name, url in targets.items()}
            for task in concurrent.futures.as_completed(tasks):
                name, now = tasks[task], time.time()
                try:
                    value, latency = task.result()
                    if name.startswith("mac:") and value.get("ok") is not True:
                        raise ValueError("Worker not ready")
                    if name == "fleet" and not isinstance(value.get("runs"), list):
                        raise ValueError("Invalid fleet state")
                    if name == "metrics" and not isinstance(value.get("live"), dict):
                        raise ValueError("Invalid machine metrics")
                    with health_lock:
                        health_state["sources"][name] = {"data": value, "at": now, "checked": now, "error": None, "latency_ms": latency}
                        if name.startswith("mac:"):
                            row = health_mac(name[4:], health_state["sources"][name], now)
                            history = health_state["history"].setdefault(name, collections.deque(maxlen=240))
                            history.append([now] + [None if (i >= 3 and row["resource_stale"]) or (field.startswith("gpu_") and row["gpu_stale"])
                                                    else row[field] for i, field in enumerate(HEALTH_MAC_HISTORY)])
                except Exception as error:
                    with health_lock:
                        previous = health_state["sources"].get(name, {})
                        health_state["sources"][name] = {**previous, "checked": now, "error": health_error(error)}
                        if name.startswith("mac:"):
                            health_state["history"].setdefault(name, collections.deque(maxlen=240)).append([now] + [None] * len(HEALTH_MAC_HISTORY))
        with health_lock:
            now = time.time()
            nodes = health_fleet(health_state["sources"].get("fleet", {}), health_state["sources"].get("metrics", {}), now)
            active = {"host:" + row["id"] for row in nodes}
            for key in list(health_state["history"]):
                if key.startswith("host:") and key not in active:
                    del health_state["history"][key]
            for row in nodes:
                fields = ("cpu_percent", "memory_percent", "network_rx", "network_tx", "memory_used", "memory_total",
                          "disk_read", "disk_write", "disk_percent", "disk_free")
                health_state["history"].setdefault("host:" + row["id"], collections.deque(maxlen=240)).append(
                    [now] + [None if row["stale"] else row[field] for field in fields])
    finally:
        with health_lock:
            health_state["refreshing"] = False


def health_fetch_timed(url):
    start = time.monotonic()
    value = health_read(url)
    return value, round((time.monotonic() - start) * 1000)


def health_mac(name, source, now):
    data = source.get("data") or {}
    age = health_age(now, source.get("at"))
    known = bool(data)
    stale = age is None or age > 45 or bool(source.get("error"))
    state = "offline" if source.get("error") else "loading" if not known else "stale" if stale else "attention" if data.get("stuck", 0) else "draining" if data.get("draining") else "busy" if data.get("jobs", 0) else "healthy"
    recent_jobs = [row for row in data.get("recent", []) if isinstance(row, dict)][-8:]
    times = sorted(x for row in recent_jobs if (x := health_number(row.get("convert_s"))) is not None)
    median = (times[(len(times)-1)//2] + times[len(times)//2]) / 2 if times else None
    resources = data.get("resources") if isinstance(data.get("resources"), dict) else {}
    resource_age = health_age(now, resources.get("sample_at"))
    resource_stale = stale or resource_age is None or resource_age > 30 or bool(resources.get("error"))
    fields = ("cpu_percent", "cpus", "memory_used", "memory_total", "memory_percent", "disk_free", "disk_total", "disk_percent",
              "network_rx", "network_tx", "disk_read", "disk_write", "network_errors_rx", "network_errors_tx",
              "network_drops_rx", "network_drops_tx", "uptime_s", "sample_cost_ms", "gpu_percent", "gpu_renderer_percent",
              "gpu_tiler_percent", "gpu_memory_used", "gpu_memory_allocated", "gpu_devices")
    gpu_state = resources.get("gpu_state") if resources.get("gpu_state") in ("available", "unavailable", "error") else "pending"
    gpu_age = health_age(now, resources.get("gpu_sample_at"))
    gpu_stale = resource_stale or gpu_state == "error" or (gpu_state == "available" and (gpu_age is None or gpu_age > 30))
    return {"id": name, "name": "Mac mini " + name.rsplit("-", 1)[-1], "state": state, "online": known and not stale,
            "checked_at": source.get("checked"), "last_seen": source.get("at"), "stale": stale,
            "error": source.get("error"), "jobs": health_number(data.get("jobs")), "capacity": health_number(data.get("max")),
            "done": health_number(data.get("done")), "failed": health_number(data.get("failed")),
            "canceled": health_number(data.get("ended")), "stuck": health_number(data.get("stuck")),
            "engine": data.get("engine"), "revision": str(data.get("revision") or "")[:12],
            "latency_ms": source.get("latency_ms"), "conversion_s": round(median, 3) if median is not None else None,
            "last_conversion_at": max((row.get("at", 0) for row in recent_jobs), default=0) or None,
            "history": [], "resource_metrics": bool(resources.get("sample_at")), "resource_stale": resource_stale,
            "resource_sample_at": resources.get("sample_at"), "resource_error": resources.get("error"),
            "gpu_state": gpu_state, "gpu_stale": gpu_stale, "gpu_sample_at": resources.get("gpu_sample_at"),
            "cpu_per_core": [health_number(n) for n in resources.get("cpu_per_core", [])[:256]] if isinstance(resources.get("cpu_per_core"), list) else [],
            **{key: str(resources[key])[:160] if resources.get(key) is not None else None
               for key in ("gpu_name", "gpu_reason", "gpu_error")},
            **{field: health_number(resources.get(field)) for field in fields}}


def health_replica_number(name):
    if name == APP:
        return 1
    match = re.fullmatch(re.escape(APP) + r"-r([1-9][0-9]*)", name)
    return int(match.group(1)) if match else None


def health_replica_hosts(state, latest):
    """Current application placement owns scope, including unavailable hosts."""
    hosts = {}
    if isinstance(state.get("projects"), list):
        project = next((p for p in state["projects"] if p.get("name") == APP), None)
        if project is None:
            return hosts
        if isinstance(project.get("placed"), list):
            for placement in project["placed"]:
                number, replica = str(placement.get("machine", "")), placement.get("replica")
                if number.isdigit():
                    hosts.setdefault(number, set())
                    if isinstance(replica, int) and replica > 0:
                        hosts[number].add(replica)
            return hosts
    # Compatibility for a status feed without the application-placement list.
    for number, run in latest.items():
        replicas = {n for name in (run.get("projects") or {}) if (n := health_replica_number(name)) is not None}
        if replicas:
            hosts[number] = replicas
    return hosts


def health_fleet(status_source, metrics_source, now):
    state, metrics = status_source.get("data") or {}, metrics_source.get("data") or {}
    latest = {}
    for run in state.get("runs", []):
        number = str(run.get("machine", ""))
        if not number.isdigit():
            continue
        if number not in latest or run.get("started", 0) > latest[number].get("started", 0):
            latest[number] = run
    hosts = health_replica_hosts(state, latest)
    numbers = sorted(hosts, key=int)
    nodes = []
    for number in numbers:
        run = latest.get(number, {})
        sample = metrics.get("live", {}).get(number, {})
        same_run = bool(run.get("id") and sample.get("run") == run["id"])
        h = sample.get("h", {}) if same_run else {}
        age = health_age(now, sample.get("t"), True) if same_run else None
        seen_age = health_age(now, run.get("seen"), True)
        stale = (age is None or age > 180 or bool(metrics_source.get("error")) or bool(status_source.get("error"))
                 or (health_age(now, status_source.get("at")) or 0) > 45)
        disk = run.get("disk") or {}
        disk_total, disk_free = health_number(disk.get("total")), health_number(disk.get("free"))
        disk_used = disk_total - disk_free if disk_total is not None and disk_free is not None else None
        cpu, ram = health_number(h.get("cpu")), health_percent(h.get("memUsed"), h.get("memTotal"))
        disk_pct = health_percent(disk_used, disk_total)
        reasons = []
        if cpu is not None and cpu >= 90: reasons.append("High CPU use")
        if ram is not None and ram >= 90: reasons.append("High memory use")
        if disk_pct is not None and disk_pct >= 90: reasons.append("Low free disk space")
        projects = {name: p for name, p in (run.get("projects") or {}).items() if health_replica_number(name) is not None}
        bad_apps = sum(p.get("s") in ("unhealthy", "failed", "exited", "dead") for p in projects.values() if isinstance(p, dict))
        if bad_apps: reasons.append(str(bad_apps) + " unhealthy Frame replicas")
        offline = not run.get("live") or seen_age is None or seen_age > 180
        status = ("offline" if offline else "draining" if run.get("draining") or run.get("retiring") or run.get("departing")
                  else "starting" if not run.get("ready") else "stale" if stale else "attention" if reasons else "healthy")
        history = metrics.get("m", {}).get(number, {}).get("h", {})
        history_rows = []
        for i, stamp in enumerate(metrics.get("t", [])[-61:]):
            offset = max(0, len(metrics.get("t", [])) - 61) + i
            def at(key):
                values = history.get(key, [])
                return health_number(values[offset]) if offset < len(values) else None
            history_rows.append([stamp / 1000, at("cpu"), health_percent(at("memUsed"), at("memTotal")), at("netRx"), at("netTx"), at("memUsed"), at("memTotal"), at("diskRead"), at("diskWrite")])
        workloads = []
        for replica in sorted(hosts[number]):
            values = next((row.get("values", {}) for row in sample.get("replicas", {}).values()
                           if row.get("app") == APP and row.get("replica") == replica), {}) if same_run else {}
            if not values and same_run and len(hosts[number]) == 1:
                values = sample.get("a", {}).get(APP, {})
            workload = {"replica": replica, "cpu_cores": health_number(values.get("cpu")), "memory_bytes": health_number(values.get("mem")),
                        "network_rx": health_number(values.get("netRx")), "network_tx": health_number(values.get("netTx")),
                        "disk_read": health_number(values.get("diskRead")), "disk_write": health_number(values.get("diskWrite"))}
            if workload["cpu_cores"] is not None:
                workload["cpu_cores"] /= 100
            runtime = projects.get(APP if replica == 1 else APP + "-r" + str(replica), {})
            runtime_state = runtime.get("s") if isinstance(runtime, dict) else None
            workload["state"] = ("offline" if offline else "stale" if stale else
                                 "healthy" if runtime_state in ("healthy", "running") else
                                 "attention" if runtime_state in ("unhealthy", "failed", "exited", "dead") else "starting")
            workloads.append(workload)
        nodes.append({"id": number, "name": run.get("label") or "Machine " + number, "state": status,
                      "online": not offline and not status_source.get("error") and (health_age(now, status_source.get("at")) or 0) <= 45,
                      "region": run.get("region"), "pool": run.get("pool"), "location": run.get("location"),
                      "cpu_percent": cpu, "cpus": health_number(run.get("cpus")), "memory_percent": ram,
                      "memory_used": health_number(h.get("memUsed")), "memory_total": health_number(h.get("memTotal")),
                      "disk_percent": disk_pct, "disk_free": disk_free, "disk_total": disk_total,
                      "network_rx": health_number(h.get("netRx")), "network_tx": health_number(h.get("netTx")),
                      "disk_read": health_number(h.get("diskRead")), "disk_write": health_number(h.get("diskWrite")),
                      "latency_ms": health_number(run.get("rtt")), "started_at": (run.get("started") or 0) / 1000 or None,
                      "last_seen": (run.get("seen") or 0) / 1000 or None,
                      "sample_at": (sample.get("t") or 0) / 1000 if same_run else None,
                      "stale": stale or offline, "reasons": reasons, "apps": len(projects),
                      "runs_frame": True, "replicas": sorted(hosts[number]), "workloads": workloads, "history": history_rows})
    return nodes


def health_service():
    now = time.monotonic()
    active = [s for s in list(playback_sessions.values()) if not s["closed"] and now - s["seen"] < PLAYBACK_TTL]
    cutoff = time.time() - 300
    delivered = sorted(row[3] for row in list(bstats["served"]) if row[0] >= cutoff)
    fetches = [row for row in list(bstats["vps"]) if row[0] >= cutoff]
    fetched_seconds = sum(row[1] for row in fetches)
    modes = collections.Counter(s.get("mode", "starting") for s in active)
    return {"replica": REPLICA, "code": CODE,
            "sessions": len(active), "modes": dict(modes),
            "media_processes": len(media_processes), "cpu_jobs": len(cpu_jobs), "mac_jobs": len(mac_jobs),
            "reads": sum(up_now.values()), "queued": len(up_wait) + len(send_wait),
            "cache_bytes": mem_size["n"], "lookahead_pieces": HLS_AHEAD,
            "segment_samples": len(delivered),
            "segment_p50_ms": round((delivered[(len(delivered)-1)//2]+delivered[len(delivered)//2])*500) if delivered else None,
            "segment_p95_ms": round(delivered[min(len(delivered)-1, math.ceil(len(delivered)*.95)-1)]*1000) if delivered else None,
            "source_fetch_mbps": round(sum(row[2] for row in fetches)*8/fetched_seconds/1e6, 1) if fetched_seconds else None,
            "paused_sources": sum(v.get("until", 0) > time.time() for v in list(src_trouble.values()))}


def health_dashboard():
    now = time.time()
    launch = False
    with health_lock:
        if not health_state["refreshing"] and now - health_state["attempted"] >= HEALTH_INTERVAL:
            health_state.update(refreshing=True, attempted=now)
            launch = True
        sources = dict(health_state["sources"])
        mac_nodes = []
        for url in MAC_URLS:
            name = mac_name(url)
            row = health_mac(name, sources.get("mac:" + name, {}), now)
            row["history"] = list(health_state["history"].get("mac:" + name, []))
            mac_nodes.append(row)
        machine_nodes = health_fleet(sources.get("fleet", {}), sources.get("metrics", {}), now)
        for row in machine_nodes:
            row["observed_history"] = list(health_state["history"].get("host:" + row["id"], []))
        result = {"now": now, "refreshing": health_state["refreshing"], "poll_seconds": HEALTH_INTERVAL,
                  "macs": mac_nodes, "machines": machine_nodes,
                  "sources": {name: {k: value.get(k) for k in ("at", "checked", "error")} for name, value in sources.items()},
                  "service": health_service(), "scope": {"app": APP, "machines": "replica_hosts"}, "runners_url": HEALTH_RUNNERS}
    if launch:
        threading.Thread(target=health_refresh, daemon=True, name="health-refresh").start()
    return result
