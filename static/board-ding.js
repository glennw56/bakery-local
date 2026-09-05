/* iPad KDS: overlay unlocks one reused Audio element; later 10s polls ding that same element. */
(function () {
  const STORE = "kds_cleared";
  const MAX = 80;
  const seen = new Set();
  let primed = false;
  let unlocked = false;
  let dingAudio = null;
  let audioCtx = null;

  function lineKeys() {
    return Array.from(document.querySelectorAll("#ticket-list [data-kds-line]"))
      .map((el) => el.getAttribute("data-kds-line") || "")
      .filter(Boolean);
  }

  function loadCleared() {
    try {
      const raw = localStorage.getItem(STORE) || sessionStorage.getItem(STORE) || "";
      return raw.split(",").map((s) => s.trim()).filter(Boolean).slice(0, MAX);
    } catch (e) {
      return [];
    }
  }

  function saveCleared(ids) {
    const value = ids.filter(Boolean).slice(0, MAX).join(",");
    try {
      localStorage.setItem(STORE, value);
      sessionStorage.setItem(STORE, value);
    } catch (e) {}
  }

  function addCleared(oid) {
    if (window.kdsRememberCleared) {
      window.kdsRememberCleared(oid);
      return;
    }
    if (!oid) return;
    const ids = loadCleared();
    if (ids.indexOf(oid) === -1) ids.push(oid);
    saveCleared(ids);
  }

  function hideCleared() {
    const ids = new Set(loadCleared());
    document.querySelectorAll("#ticket-list [data-order-id]").forEach((el) => {
      const oid = el.getAttribute("data-order-id") || "";
      if (!ids.has(oid)) return;
      el.querySelectorAll("[data-kds-line]").forEach((line) => {
        const key = line.getAttribute("data-kds-line");
        if (key) seen.add(key);
      });
      el.remove();
    });
  }

  function unlockAudio() {
    if (!dingAudio) {
      dingAudio = new Audio("/static/ding.wav");
      dingAudio.preload = "auto";
      dingAudio.setAttribute("playsinline", "true");
    }
    dingAudio.currentTime = 0;
    const play = dingAudio.play();
    if (play && play.catch) play.catch(function () {});
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) {
      if (!audioCtx) audioCtx = new AC();
      if (audioCtx.state === "suspended") audioCtx.resume();
    }
  }

  function ding() {
    if (!dingAudio) return;
    try {
      dingAudio.pause();
      dingAudio.currentTime = 0;
    } catch (e) {}
    const play = dingAudio.play();
    if (play && play.catch) play.catch(function () {});
  }

  function scan(allowDing) {
    hideCleared();
    const keys = lineKeys();
    if (!primed) {
      keys.forEach((k) => seen.add(k));
      primed = true;
      return;
    }
    let fresh = false;
    keys.forEach((k) => {
      if (!seen.has(k)) {
        seen.add(k);
        fresh = true;
      }
    });
    if (allowDing && unlocked && fresh) ding();
  }

  function enable(ev) {
    if (ev) ev.preventDefault();
    if (unlocked) return;
    unlocked = true;
    unlockAudio();
    const gate = document.getElementById("kds-ding-gate");
    if (gate) gate.hidden = true;
  }

  document.addEventListener("DOMContentLoaded", function () {
    scan(false);
    const gate = document.getElementById("kds-ding-gate");
    if (gate) {
      gate.addEventListener("pointerup", enable);
      gate.addEventListener("click", enable);
    }
    const list = document.getElementById("ticket-list");
    if (list) {
      list.addEventListener("htmx:configRequest", function (ev) {
        const params = ev.detail && ev.detail.parameters;
        if (params) params.cleared = loadCleared().join(",");
      });
    }
  });

  document.body.addEventListener("htmx:configRequest", function (ev) {
    const path = (ev.detail && ev.detail.path) || "";
    const match = path.match(/\/board\/orders\/([^?]+)/);
    if (match) addCleared(decodeURIComponent(match[1]));
    if (ev.detail && ev.detail.parameters) {
      ev.detail.parameters.cleared = (window.kdsLoadCleared ? window.kdsLoadCleared() : loadCleared()).join(",");
    }
  });

  document.body.addEventListener("htmx:afterRequest", function (ev) {
    if (!ev.detail || !ev.detail.successful) return;
    const elt = ev.detail.elt;
    const oid = elt && elt.getAttribute && elt.getAttribute("data-order-id");
    if (oid) addCleared(oid);
  });

  document.body.addEventListener("htmx:afterSwap", function (ev) {
    const target = ev.detail && ev.detail.target;
    if (!target || target.id !== "ticket-list") return;
    scan(true);
  });
})();
