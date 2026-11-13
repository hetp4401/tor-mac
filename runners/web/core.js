
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
    esc([year, m.type === "series" ? "Series" : "Movie"].filter(Boolean).join(" · ")) + '</span></div>');
  if (rating > 0) frame.insertAdjacentHTML("beforeend", '<span class="poster-rating" aria-label="Rating ' + esc(rating) + ' out of 10">★ ' + esc(rating.toFixed(1)) + '</span>');
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
  h.querySelector(".hero-eyebrow").textContent = m.type === "series" ? "Featured series" : "Featured film";
  const w = h.querySelector(".hw"), d = h.querySelector(".hm");
  w.href = href + "?to=watch";
  w.innerHTML = ICON.play + (m.type === "series" ? "Browse episodes" : "Watch options");
  d.href = href;
  d.innerHTML = ICON.info + "Details";
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
  pendingTo = to === "watch" || to === "versions" ? key : null;
  if (shown !== key || !cur || cur.id !== id || (type === "series" && series.id !== id)) {  // (another title; or this one, should
    shown = key;
    const m = known.get(id) || {id: id, type: type};
    cur = {id: id, type: type, name: m.name || "", year: yearOf(m), poster: pic.poster(id), runtime: m.runtime || ""};
    curEp = null;
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
    let metaGone = false;
    if (type === "series") {
      const tl = byId("tList");
      if (tl.spy) { tl.spy.disconnect(); tl.spy = null; }
      tl.dataset.sid = "";
      tl.innerHTML = "";
      series = {id: id, videos: epsOf(m)};
      epsLoading();
    } else byId("tList").innerHTML = "";
    afterFrame(() => {  // (its versions or episodes, after the frame its page first shows in: they're below the fold, and a push
      if (shown !== key) return;
      listsFor = key;
      if (type === "movie") loadVersions(byId("tList"), "movie", id, true, null);
      else if (series.videos.length) { paintSeasons(here.season); openEpisode(here.season, here.episode); }  // (a catalog's episodes, or the details')
      else if (metaGone) epsFailed(metaGone);
    });
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
          if (!vids.length) metaGone = "none";
          if (listsFor !== key) {}  // (drawn after the first frame, from these)
          else if (vids.length) { paintSeasons(here.season); openEpisode(here.season, here.episode); }
          else epsFailed("none");
        }
      }
      watchScroll(key, type);
    }).catch((e) => {
      if (shown !== key) return;
      byId("tDesc").innerHTML = "Couldn't load its details. " + esc(plainErr(e)) + ' <button class="again">Try again</button>';
      byId("tDesc").querySelector(".again").onclick = reloadTitle;
      if (type === "series" && !series.videos.length) { metaGone = e; if (listsFor === key) epsFailed(e); }
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
let listsFor = null;  // (the title whose lists are drawn: after the frame its page first shows in)
function reloadTitle() { shown = null; route(false); }  // (Try again: the page made again, its details asked for anew)
function watchScroll(key, type) {  // (Watch from the home page: to the versions or episodes, once the page has its details)
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
  paintActs(m, type);
}
function paintActs(m, type) {  // (Resume where it's being watched; else Watch, which goes down to its versions or episodes)
  const box = byId("tActs"), r = recent().find((x) => x.title.id === (m.id || cur.id));
  if (r) {
    box.innerHTML = '<button class="btn">' + ICON.play + "Resume" + (r.ep ? " S" + r.ep.s + ":E" + r.ep.e : "") + "</button>";
    box.firstChild.onclick = () => resume(r);
    box.firstChild.addEventListener("pointerdown", () => loadHls().catch(() => {}), {once: true, passive: true});  // (a resume is near)
  } else {
    box.innerHTML = '<button class="btn">' + ICON.play + (type === "series" ? "Browse episodes" : "Choose a version") + "</button>";
    box.firstChild.onclick = () => scrollToSel(byId(type === "series" ? "tSeries" : "tVersions"));
  }
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
function markEp() {  // (the episode whose versions are open: outlined)
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
