const SECT = {  // (the fleet's section names: a flat pictogram of the same thing, a word, a line on what it is)
  "🎞 Original": ["Original", "Original video quality with a browser-ready container. Audio depends on the selected track.", "film", "orig"],
  "▶ As is": ["As is", "The source file, played directly without conversion.", "asis", "asis"],
  "🌐 Browser 1080p": ["Converted", "Converted to browser-friendly H.264 video and stereo audio.", "globe", "conv"],
  "📄 Direct": ["External player", "Copy the untouched file link for a compatible external player, such as VLC or Infuse.", "raw", "direct"],
};
const sectOf = (name) => SECT[name] || [String(name).replace(/^[^A-Za-z0-9]+/, "") || "Versions", "", /Browser|onvert/.test(name) ? "globe" : "film", "other"];
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
function verHead(label, file) {  // ("Remux · 4K HDR": the kind of release from the file's name, then the picture from the fleet's label)
  const r = RELEASES.find((x) => x[0].test(file || "")), pict = String(label || "").replace(/^from /, "").replace(/ ?(Blu-ray|plays in a browser)/g, "").trim();
  return [r ? r[1] : "", pict === "other" ? "" : pict].filter(Boolean).join(" · ") || label || "";
}
function verSize(info) {  // ("17.6 GB" first; then what the fleet says of it, but not what its section's header says already)
  const m = /^💾\s*([\d.]+\s*[KMGT]B)(.*)/.exec(info || "");
  if (!m) return esc(String(info || "").replace(/^💾\s*/, ""));
  const bits = m[2].split(" · "), head = bits.shift(), keep = bits.filter((b) => !/^as on the source|^the original file/.test(b));
  return (parseFloat(m[1]) > 0 ? "<b>" + esc(m[1]) + "</b>" : "Size unavailable") + esc(head + (keep.length ? " · " + keep.join(" · ") : ""));
}
const unfit = (s) => !!s.f && ((/\/orig\.m3u8/.test(s.url || "") && !s.f.o) || (!!s.apple && !s.f.a));  // (its probe showed it can't play here: see tor.py's facts_of)
function sourceKind(s) { return sectOf((s.name || "").split("\n")[0]); }
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
  const seen = new Set(), rank = {orig: 0, asis: 1, conv: 2, direct: 3, other: 4};
  return list.map((source, index) => ({source, index, bytes: versionBytes(source), kind: sourceKind(source)[3]}))
    .filter((item) => {
      const identity = item.kind + "|" + (item.source.url || "");
      if (!item.source.url || seen.has(identity)) return false;
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
  const quality = verHead(s.f ? s.f.h : (n[1] || n[0]), t[0]);
  return '<span class="vbadge" data-kind="' + esc(kind[3]) + '" title="' + esc(kind[1]) + '">' + esc(kind[0]) + '</span>' +
    '<span class="vquality">' + esc(quality) + '</span>' +
    (s.f && kind[3] !== "conv" ? '<span class="vok" title="Compatibility checked">' + ICON.check + "</span>" : "");
}
function verWords(s) {
  const t = (s.title || "").split("\n"), f = s.f;
  if (!f) return verSize(t[1]) || "Size not available";
  const conv = sourceKind(s)[3] === "conv", more = (conv ? [f.l] : [f.v, f.s, f.l]).filter(Boolean).join(" · ");
  return (verSize(conv ? t[1] : (t[1] || "").split(" · ")[0]) || "Size not available") +
    (more ? esc(" · " + more) : "") + (conv && f.c ? ' · <span class="vc">' + esc(f.c) + "</span>" : "");
}
function versionPlayLabel(s) {
  const t = (s.title || "").split("\n");
  const action = sourceKind(s)[3] === "direct" ? "Copy link for an external player" : "Play " + sourceKind(s)[0];
  return action + ": " + (t[0] || "this version") + (t[1] ? ", " + t[1].replace(/^💾\s*/, "") : "");
}
function versionFileUrl(s) { return new URL((s.download || s.url).replace("/dl/", "/file/"), location.href).href; }
function verRow(s, onPick) {
  const t = (s.title || "").split("\n"), d = document.createElement("div"), external = sourceKind(s)[3] === "direct";
  d.className = "ver";
  d._source = s;
  if (s.k) d.dataset.k = s.k;
  d.dataset.kind = sourceKind(s)[3];
  d.innerHTML = '<button class="vplay" type="button" aria-label="' + esc(versionPlayLabel(s)) + '"' + (external ? ' title="Copy link for an external player"' : '') + '><span class="vt"><span class="vk">' + verTop(s) +
    '</span><span class="vs">' + verWords(s) + "</span>" + fileName(t[0] || "") + '</span><span class="go" aria-hidden="true">' + (external ? ICON.link : ICON.play) + '</span></button>' +
    (s.download ? '<button class="dl cp" type="button" title="Copy file link" aria-label="Copy file link">' + ICON.link + '</button>' +
      '<a class="dl" href="' + esc(s.download) + '" download title="Download source file" aria-label="Download source file">' + ICON.dl + '</a>' : "");
  d.querySelector(".vplay").onclick = () => external ? copyUrl(versionFileUrl(s)) : onPick(s);
  const copy = d.querySelector(".cp");
  if (copy) copy.onclick = () => copyUrl(versionFileUrl(s));
  return d;
}
function copyUrl(url) {  // (a version's link, to the clipboard; where that's refused (an old browser, no focus), the old way)
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
  return '<div class="vload"><div class="vsec"><div class="vsec-h"><b>Finding playback options…</b></div><p class="vsec-d">Available files will appear here, sorted by size.</p>' +
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
    if (s.apple && !applePlayer()) return false;
    if (s.apple) appleFiles.add(s.url);
    if (!s.apple && !/\/orig\.m3u8/.test(s.url || "")) return true;
    if (unfit(s)) return false;
    const t = (s.title || "").split("\n"), x = bad[verId(t[0] || "", t[1])];
    return !x || (x.soft && Date.now() - x.at > 6048e5);
  });
}
const versionViews = new Map();
function renderVersions(box, list, scroller, onPick, id) {
  if (box.spy) { box.spy.disconnect(); box.spy = null; }
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
  box.innerHTML = '<div class="vtoolbar"><div class="vsummary"><strong>' + rows.length +
    '</strong> playback option' + (rows.length === 1 ? "" : "s") + '</div><label class="vsort"><span>File size</span>' +
    '<select aria-label="Sort playback options by file size"><option value="largest">Largest first</option><option value="smallest">Smallest first</option></select></label></div>' +
    '<p class="vhint">Sizes refer to the source file. Playback type is shown on each option.</p><div class="vgroup" aria-label="Playback options"></div>';
  const group = box.querySelector(".vgroup"), select = box.querySelector("select");
  select.value = order;
  const append = (start, end) => {
    const frag = document.createDocumentFragment();
    rows.slice(start, end).forEach((source) => frag.appendChild(verRow(source, onPick)));
    group.appendChild(frag);
  };
  const moreButton = () => {
    if (state.shown >= rows.length) return;
    const more = document.createElement("button");
    more.type = "button";
    more.className = "vmore";
    const count = Math.min(first, rows.length - state.shown);
    more.innerHTML = "Show " + count + " more" + ICON.down;
    more.onclick = () => {
      const start = state.shown, keyboard = document.activeElement === more;
      state.shown += count;
      shieldOn(group);
      more.remove();
      append(start, state.shown);
      moreButton();
      fitNames(group);
      if (keyboard) group.querySelectorAll(".vplay")[start]?.focus({preventScroll: true});
    };
    group.appendChild(more);
  };
  append(0, state.shown);
  moreButton();
  fitNames(box);
  select.onchange = () => {
    order = select.value === "smallest" ? "smallest" : "largest";
    store.set("source-sort", order);
    rows = sortedVersions(fitVersions(list), order);
    state.shown = first;
    group.replaceChildren();
    append(0, state.shown);
    moreButton();
    fitNames(box);
  };
}
function loadVersions(box, type, sid, count, scroller) {
  const id = type + "/" + sid;
  box.dataset.sid = sid;
  box.reload = () => { if (box.dataset.sid === sid) loadVersions(box, type, sid, count, scroller); };  // (drawn again: see markUnfit)
  box.at = Date.now();
  whenQuiet(() => loadHls().catch(() => {}), 0, 1500);  // (versions shown: a play is near; fetched and compiled once nobody has scrolled
  const pick = (s) => { const t = (s.title || "").split("\n"); play(s.url, kindOf(s), t[0], t[1] || ""); };
  const show = (r) => {
    if (box.dataset.sid !== sid) return;
    box.removeAttribute("aria-busy");
    const list = sortedVersions(fitVersions(r.streams || []), store.get("source-sort", "largest"));
    if (count) byId("tCount").textContent = list.length || "";
    if (!list.length) {
      if (box.spy) { box.spy.disconnect(); box.spy = null; }
      box.tok = null;
      box.innerHTML = cuv(ICON.film, "No versions found", r.error ? "The fleet couldn't look just now." : "The fleet has nothing of this title yet.", r.error ? "Try again" : null);
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
    box.innerHTML = cuv(ICON.alert, "Couldn't list the versions", plainErr(e), "Try again");
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
  markEp();
}
function closeSheet(quiet, instant) {  // (quiet: the address already says so; else back to the season's page, as it was come from;
  const open = sheet.classList.contains("open");
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
