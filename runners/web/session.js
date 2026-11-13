// Keep only playback corrections that actually reached a playing frame on this device.
function playbackChoiceKey(url, file, info) {
  const u = new URL(url, location.href), match = /\/(hls|file)\/([^/]+)\//.exec(u.pathname);
  if (!match) return null;
  const mode = match[1] === "file" ? "file" : /\/orig\.m3u8$/.test(u.pathname) ? "original" : "auto";
  return JSON.stringify([file ? verId(file, info) : match[2], mode]);
}
function compatiblePlaybackURL(url, force) {
  const u = new URL(url, location.href), match = /\/(?:hls|file)\/([^/]+)\//.exec(u.pathname);
  if (!match || u.searchParams.get("force") === "1") return null;
  u.pathname = "/hls/" + match[1] + "/index.m3u8";
  u.search = force ? "?force=1" : "";
  return u.href;
}
function correctedPlaybackURL(url, file, info) {
  const key = playbackChoiceKey(url, file, info), choice = key && store.get("playback-corrections-v1", {})[key];
  if (!choice || Date.now() - choice.at > 7 * 864e5) return url;
  return compatiblePlaybackURL(url, choice.force) || url;
}
function rememberPlayback(p) {
  if (!p.started || !p.correctionKeys || !p.correctionKeys.length) return;
  const known = store.get("playback-corrections-v1", {}), now = Date.now();
  const force = new URL(p.url, location.href).searchParams.get("force") === "1";
  p.correctionKeys.forEach(key => { if (key) known[key] = {force, at: now}; });
  const kept = {};
  Object.keys(known).filter(key => now - known[key].at < 7 * 864e5)
    .sort((a, b) => known[b].at - known[a].at).slice(0, 200).forEach(key => { kept[key] = known[key]; });
  store.set("playback-corrections-v1", kept);
}

// One lease per playback attempt. A stopped attempt can never revive itself.
function beginPlayback(p) {
  const u = new URL(p.url, location.href), match = /\/(?:hls|file)\/([^/]+)\//.exec(u.pathname);
  if (!match) { p.mediaUrl = p.url; return; }
  const sid = crypto.randomUUID().replace(/-/g, "");
  const s = p.session = {sid, endpoint: u.origin + "/hls/" + match[1] + "/session", requests: new Set(), seq: 0, ended: false};
  u.searchParams.set("sid", sid);
  u.searchParams.set("managed", "1");
  u.searchParams.set("start", String(p.from || 0));
  if (p.native) u.searchParams.set("cap", "hevc");
  p.mediaUrl = u.href;
  p.lastMovement = Date.now();
  p.lastTime = p.from || 0;
  s.timer = setTimeout(() => pollPlayback(p), 1000);
}

function releasePlayback(p) {
  const s = p && p.session;
  if (!s || s.ended) return;
  s.ended = true;
  clearTimeout(s.timer);
  clearTimeout(s.seekTimer);
  s.requests.forEach(c => c.abort());
  s.requests.clear();
  const url = s.endpoint + "?sid=" + s.sid + "&action=stop";
  // Beacons may wait behind native media traffic. A foreground stop has higher
  // priority and keepalive survives page dismissal; beacon is the fallback.
  const beacon = () => { if (navigator.sendBeacon) navigator.sendBeacon(url, ""); };
  try {
    fetch(url, {method: "POST", keepalive: true, priority: "high", cache: "no-store"})
      .then(r => { if (!r.ok) beacon(); }, beacon);
  } catch (_) { beacon(); }
}

async function playbackControl(p, action, extra) {
  const s = p.session;
  if (!s || s.ended) return null;
  const controller = new AbortController();
  s.requests.add(controller);
  const deadline = setTimeout(() => controller.abort(), 5000);
  try {
    const query = new URLSearchParams({sid: s.sid, managed: "1", action, ...extra});
    const r = await fetch(s.endpoint + "?" + query, {method: "POST", priority: "high", cache: "no-store", signal: controller.signal});
    if (!r.ok || playing !== p || s.ended) return null;
    return await r.json();
  } catch (_) { return null; }
  finally { clearTimeout(deadline); s.requests.delete(controller); }
}

async function pollPlayback(p) {
  const s = p.session;
  if (!s || s.ended || playing !== p) return;
  const file = /\/file\//.test(p.url);
  const progress = p.started && !vid.seeking && Number.isFinite(vid.currentTime) ? {at: String(vid.currentTime), seq: String(s.seq)} : {};
  const state = await playbackControl(p, "keep", {mode: file ? "as-is" : "hls", cap: p.native ? "hevc" : "h264", ...progress});
  if (playing !== p || s.ended) return;
  if (state && state.error) { failPlay("Source unavailable", state.error); return; }
  if (state && state.mode && state.mode !== p.effectiveMode) {
    p.effectiveMode = state.mode;
    const label = state.mode === "converted" ? "Converted" : state.mode === "remux" ? "Original video · compatible audio" : "Original";
    byId("pVer").querySelector(".vk").textContent = label;
    byId("pFileDetails").querySelector(".vk").textContent = label;
  }
  if (state && state.fallback && file) { recoverPlayback(p, "using compatible playback", false); return; }
  // Progress, not repeated waiting events, owns this clock. Paused playback and
  // a rejected autoplay gesture must not be mistaken for a stalled decoder.
  const stalled = (!p.started && !p.needsGesture) || (p.started && !vid.paused && bufferedAhead() < .5);
  if (stalled && Date.now() - p.lastMovement > 25000) {
    if (!recoverPlayback(p, "the stream stopped responding", true)) failPlay("Stream did not respond", "Choose another stream or try again.");
    return;
  }
  s.timer = setTimeout(() => pollPlayback(p), p.started ? (vid.paused ? 12000 : 4000) : 2000);
}

function recoverPlayback(p, reason, force) {
  if (playing !== p || !p.session || p.session.ended) return false;
  if ((p.fallbacks || 0) >= 2) return false;
  const url = compatiblePlaybackURL(p.url, force);
  if (!url) return false;
  const keys = (p.correctionKeys || []).concat(playbackChoiceKey(p.url, p.file, p.info));
  play(url, "converted", p.file, p.info, p.title, p.ep, "switch");
  playing.correctionKeys = keys;
  playing.twinned = reason;
  return true;
}

function playbackSeek(p, seconds) {
  if (!p || !p.session || p.session.ended || !Number.isFinite(seconds)) return;
  const s = p.session;
  clearTimeout(s.seekTimer);
  const seq = ++s.seq;
  p.lastMovement = Date.now();
  s.seekTimer = setTimeout(() => {
    if (playing === p && !s.ended) playbackControl(p, "seek", {at: String(seconds), seq: String(seq)});
  }, 80);
}

let pageResume = null;
window.addEventListener("pagehide", () => {
  if (!playing) return;
  savePos(true);
  pageResume = {...playing, resumeAt: vid.currentTime};
  releasePlayback(playing);
  vid.pause();
  teardown();
});
window.addEventListener("pageshow", e => {
  if (!e.persisted || !pageResume) return;
  const p = pageResume;
  pageResume = null;
  playing = p;
  play(p.url, p.kind, p.file, p.info, p.title, p.ep, "again");
});
