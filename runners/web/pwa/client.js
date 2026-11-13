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
