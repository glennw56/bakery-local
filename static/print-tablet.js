/* Barista tablet: Star PassPRNT on the paired TSP100IV (58mm, Bluetooth).
   Test print and drink Print links are <a href="starpassprnt://...">
   rendered by the server. Android Chrome opens that scheme from the
   anchor tap and drops it when script assigns the address.
   This file only shows the oldest waiting link. It does not write href,
   replace a link already on the page, or listen for the tap.
   The link's ping attribute claims the drink as the browser follows it. */
(function () {
  var KEY = "sunshine-print-tablet";
  var SINCE = "sunshine-print-since";
  var box = document.getElementById("print-tablet");
  var banner = document.getElementById("print-banner");
  var statusEl = document.getElementById("print-status");
  var list = document.getElementById("print-links");
  var testLink = document.getElementById("test-print");
  var statusTimer = 0;

  function enabled() {
    try {
      return localStorage.getItem(KEY) === "1";
    } catch (err) {
      return false;
    }
  }

  function setEnabled(on) {
    try {
      localStorage.setItem(KEY, on ? "1" : "0");
      if (on && !localStorage.getItem(SINCE)) {
        localStorage.setItem(SINCE, String(Date.now()));
      }
    } catch (err) {
      /* private mode: the toggle still works until reload */
    }
  }

  function sinceMs() {
    try {
      return Number(localStorage.getItem(SINCE) || "0");
    } catch (err) {
      return 0;
    }
  }

  function debugOn() {
    return document.body.getAttribute("data-print-debug") === "1";
  }

  function linksUrl() {
    return debugOn() ? "/test/drinks/print-links?debug=1" : "/test/drinks/print-links";
  }

  function visibleSlot() {
    if (!list) return null;
    var slots = list.querySelectorAll(".kds-print-slot");
    for (var i = 0; i < slots.length; i++) {
      if (!slots[i].hidden) return slots[i];
    }
    return null;
  }

  function showBanner() {
    if (!banner) return;
    var busy = (statusEl && statusEl.textContent) || visibleSlot();
    banner.hidden = !busy;
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || "";
    showBanner();
  }

  function noteSending() {
    setStatus("Sending to PassPRNT…");
    window.clearTimeout(statusTimer);
    statusTimer = window.setTimeout(function () {
      if (document.visibilityState !== "visible") return;
      setStatus("PassPRNT did not open. Tap Print again.");
    }, 2000);
  }

  function reveal() {
    if (!list) return false;
    var slots = list.querySelectorAll(".kds-print-slot");
    var cutoff = sinceMs();
    var shown = false;
    var ready = false;
    for (var i = 0; i < slots.length; i++) {
      var created = Number(slots[i].getAttribute("data-created") || "0");
      var want = enabled() && !shown && created > cutoff;
      if (want) {
        shown = true;
        ready = true;
      }
      if (slots[i].hidden !== !want) slots[i].hidden = !want;
    }
    showBanner();
    return ready;
  }

  function absorb(html) {
    if (!list || !html) {
      reveal();
      return;
    }
    var wrap = document.createElement("div");
    wrap.innerHTML = html;
    var slots = wrap.querySelectorAll(".kds-print-slot");
    for (var i = 0; i < slots.length; i++) {
      var id = slots[i].getAttribute("data-slot");
      if (!id || list.querySelector('[data-slot="' + id + '"]')) continue;
      list.appendChild(slots[i]);
    }
    reveal();
  }

  function refreshLinks() {
    return fetch(linksUrl())
      .then(function (response) { return response.text(); })
      .then(absorb)
      .catch(function () { reveal(); });
  }

  function dropSlot(lineId) {
    if (!list || !lineId) return;
    var slot = list.querySelector('[data-slot="' + lineId + '"]');
    if (slot) slot.remove();
  }

  if (box) {
    box.checked = enabled();
    box.addEventListener("change", function () {
      if (box.checked) {
        try {
          localStorage.setItem(SINCE, String(Date.now()));
        } catch (err) {}
      }
      setEnabled(box.checked);
      refreshLinks().then(function () {
        if (reveal()) setStatus("Tap Print.");
      });
    });
  }

  if (testLink) {
    testLink.addEventListener("click", function () {
      noteSending();
    });
  }

  document.body.addEventListener("htmx:afterRequest", function (event) {
    var xhr = event.detail && event.detail.xhr;
    if (!xhr || !event.detail.successful) return;
    if ((xhr.getResponseHeader("HX-Trigger") || "") !== "orderPlaced") return;
    if (!enabled()) return;
    refreshLinks().then(function () {
      if (reveal()) setStatus("Tap Print.");
    });
  });

  function settleReturn() {
    var params = new URLSearchParams(window.location.search);
    if (params.get("print_test") && params.get("passprnt_code") !== null) {
      setStatus(params.get("passprnt_code") === "0" ? "Test print sent." : "Test print did not finish. Tap Test print to try again.");
      window.history.replaceState(null, "", "/test/drinks" + (debugOn() ? "?debug=1" : ""));
      refreshLinks();
      return;
    }
    var lineId = params.get("print_line");
    var code = params.get("passprnt_code");
    if (!lineId || code === null) {
      refreshLinks().then(function () {
        if (reveal()) setStatus("Tap Print.");
      });
      return;
    }
    var path = code === "0" ? "print-done" : "print-release";
    fetch("/test/drinks/lines/" + lineId + "/" + path, {method: "POST"})
      .finally(function () {
        if (code === "0") dropSlot(lineId);
        window.history.replaceState(null, "", "/test/drinks" + (debugOn() ? "?debug=1" : ""));
        refreshLinks().then(function () {
          if (reveal()) {
            setStatus(code === "0" ? "Tap Print." : "That drink did not print. Tap Print again.");
          } else {
            setStatus(code === "0" ? "Printed." : "That drink did not print.");
          }
        });
      });
  }

  settleReturn();
  window.setInterval(refreshLinks, 4000);
})();
