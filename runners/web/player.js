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
const WHY = {retry: "Reconnecting…", again: "Trying again…", "switch": "Switching to the converted version…"};
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
  byId("pVer").innerHTML = '<div class="vk">' + kindLabel(mine.kind) + '</div><div class="vs">' + sizeLine(mine.info) + "</div>" + fileName(mine.file);
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
const HLSWHY = {manifestLoadError: "The service didn't send this version's playlist.", manifestLoadTimeOut: "The service took too long to answer.",
                manifestParsingError: "This version's playlist is broken.", levelLoadError: "A playlist of this version didn't load.",
                fragLoadError: "A piece of it didn't arrive.", bufferAppendError: "This browser couldn't decode a piece of it.",
                manifestIncompatibleCodecsError: "This browser can't decode this version."};
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
      httpFail = ["Couldn't load this part", (why ? why + ". " : "The service couldn't get this part (" + code + "). ") + "Try again, or pick another version."];
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
    failPlay("Couldn't play this version", (why ? why + ". " : HLSWHY[d.details] ? HLSWHY[d.details] + " " : "The stream stopped. ") + "Try again, or pick another version.");
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
  if (n > 2) { failPlay("Couldn't keep playing this version", "The connection stopped twice. Try again, or pick another version."); return; }
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
  cancelNativeSeekHint();
  teardownN++;
  mediaFor = null;
  if (hls) { try { hls.destroy(); } catch (e) {} hls = null; }
  if (vid.getAttribute("src")) { vid.removeAttribute("src"); vid.load(); }
}
function stopPlayer() {  // (leaving: the place kept, the film paused at once; the stream let go once the page under has painted)
  savePos(true);
  playing = null;
  cancelNativeSeekHint();
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
    if (!pick) { nextFailed("No versions of the next episode"); return; }
    const lines = (pick.title || "").split("\n");
    play(pick.url, kindOf(pick), lines[0], lines[1] || "", p.title, ep, "next");
  }).catch((e) => { if (playing === p) nextFailed("Couldn't list the next episode's versions: " + e.message); });
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
// Native HLS hides its playlist index from JavaScript. A time-based hint lets
// the existing reader prioritize a cold seek without fetching a second playlist.
const nativeSeekHint = {timer: 0, timeout: 0, controller: null, at: -1e9, session: null, sent: ""};
function cancelNativeSeekHint() {
  clearTimeout(nativeSeekHint.timer);
  clearTimeout(nativeSeekHint.timeout);
  if (nativeSeekHint.controller) nativeSeekHint.controller.abort();
  Object.assign(nativeSeekHint, {timer: 0, timeout: 0, controller: null, at: -1e9, session: null, sent: ""});
}
function hintNativeSeek(t) {
  if (!playing || !playing.native || !live() || !Number.isFinite(t) || t < 0 || isBuffered(t)) return;
  let url;
  try { url = new URL(playing.url, location.href); } catch (_) { return; }
  const match = /^(.*\/hls\/[^/]+\/)(index|orig)\.m3u8$/.exec(url.pathname);
  if (!match) return;  // Direct browser-ready files do not use the HLS reader.
  const mine = playing, reader = url.searchParams.get("r");
  url.pathname = match[1] + "seek";
  url.search = "";
  url.searchParams.set("t", t.toFixed(3));
  if (match[2] === "orig") url.searchParams.set("v", "orig");
  if (reader && /^[1-9]\d*$/.test(reader)) url.searchParams.set("r", reader);
  if (nativeSeekHint.session !== mine) { cancelNativeSeekHint(); nativeSeekHint.session = mine; }
  const target = url.href, now = performance.now();
  if (target === nativeSeekHint.sent && now - nativeSeekHint.at < 1000) return;
  clearTimeout(nativeSeekHint.timer);
  const send = () => {
    nativeSeekHint.timer = 0;
    if (playing !== mine || !live() || isBuffered(t)) return;
    clearTimeout(nativeSeekHint.timeout);
    if (nativeSeekHint.controller) nativeSeekHint.controller.abort();
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    nativeSeekHint.controller = controller;
    nativeSeekHint.at = performance.now();
    nativeSeekHint.sent = target;
    nativeSeekHint.timeout = controller ? setTimeout(() => controller.abort(), 5000) : 0;
    fetch(target, {cache: "no-store", ...(controller ? {signal: controller.signal} : {})}).catch(() => {}).then(() => {
      if (nativeSeekHint.controller === controller) { clearTimeout(nativeSeekHint.timeout); nativeSeekHint.timeout = 0; nativeSeekHint.controller = null; }
    });
  };
  const wait = Math.max(0, 334 - (now - nativeSeekHint.at));
  if (wait) nativeSeekHint.timer = setTimeout(send, wait); else send();
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
    '<button class="mi" role="menuitem" data-act="versions">' + GLYPH.versions + '<span class="tx">Other versions</span></button><hr><div class="h">Speed</div><div class="speeds">' +
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
  if (nativePlayer()) hintNativeSeek(t);
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
    if (p.twinned) say("Playing the converted version: " + p.twinned);
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
  if (!twin()) failPlay("Couldn't play this version", (vid.error && vid.error.code === 4 ? "The browser couldn't open it." : "The stream broke off.") + " Try again, or pick another version.");
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
