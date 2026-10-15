/* Barista tablet: Star PassPRNT on the paired TSP100IV (58mm, Bluetooth).
   Android Chrome only opens starpassprnt:// from a real link tap.
   Print links are <a href="starpassprnt://..."> set before the tap.
   The claim is a beacon and does not block that navigation. */
(function () {
  var KEY = "sunshine-print-tablet";
  var SINCE = "sunshine-print-since";
  var box = document.getElementById("print-tablet");
  var banner = document.getElementById("print-banner");
  var statusEl = document.getElementById("print-status");
  var printLink = document.getElementById("print-next");
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

  function showBanner() {
    if (!banner) return;
    var busy = (statusEl && statusEl.textContent) || (printLink && !printLink.hidden);
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

  function claim(lineId) {
    if (!lineId) return;
    var url = "/test/drinks/lines/" + lineId + "/print-claim";
    var sent = false;
    try {
      if (navigator.sendBeacon) sent = navigator.sendBeacon(url);
    } catch (err) {
      sent = false;
    }
    if (!sent) {
      fetch(url, {method: "POST", keepalive: true}).catch(function () {});
    }
  }

  function refreshNext() {
    if (!printLink) return Promise.resolve(false);
    if (!enabled()) {
      printLink.hidden = true;
      printLink.removeAttribute("href");
      showBanner();
      return Promise.resolve(false);
    }
    return fetch("/test/drinks/print-feed")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var drinks = (data && data.drinks) || [];
        var cutoff = sinceMs();
        for (var i = 0; i < drinks.length; i++) {
          if (Number(drinks[i].created_ms) > cutoff && drinks[i].passprnt) {
            printLink.hidden = false;
            printLink.href = drinks[i].passprnt;
            printLink.setAttribute("data-line", String(drinks[i].id));
            var name = drinks[i].drink_name || drinks[i].ticket_name || "";
            printLink.textContent = name ? "Print · " + name : "Print";
            showBanner();
            return true;
          }
        }
        printLink.hidden = true;
        printLink.removeAttribute("href");
        printLink.removeAttribute("data-line");
        showBanner();
        return false;
      })
      .catch(function () { return false; });
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
      refreshNext().then(function (ready) {
        if (ready) setStatus("Tap Print.");
      });
    });
  }

  if (testLink) {
    testLink.addEventListener("click", function () {
      noteSending();
    });
  }

  if (printLink) {
    printLink.addEventListener("click", function (event) {
      if (!printLink.getAttribute("href")) {
        event.preventDefault();
        return;
      }
      claim(printLink.getAttribute("data-line"));
      noteSending();
    });
  }

  document.body.addEventListener("htmx:afterRequest", function (event) {
    var xhr = event.detail && event.detail.xhr;
    if (!xhr || !event.detail.successful) return;
    if ((xhr.getResponseHeader("HX-Trigger") || "") !== "orderPlaced") return;
    if (!enabled()) return;
    refreshNext().then(function (ready) {
      if (ready) setStatus("Tap Print.");
    });
  });

  function settleReturn() {
    var params = new URLSearchParams(window.location.search);
    if (params.get("print_test") && params.get("passprnt_code") !== null) {
      setStatus(params.get("passprnt_code") === "0" ? "Test print sent." : "Test print did not finish. Tap Test print to try again.");
      window.history.replaceState(null, "", "/test/drinks");
      refreshNext();
      return;
    }
    var lineId = params.get("print_line");
    var code = params.get("passprnt_code");
    if (!lineId || code === null) {
      refreshNext().then(function (ready) {
        if (ready) setStatus("Tap Print.");
      });
      return;
    }
    var path = code === "0" ? "print-done" : "print-release";
    fetch("/test/drinks/lines/" + lineId + "/" + path, {method: "POST"})
      .finally(function () {
        window.history.replaceState(null, "", "/test/drinks");
        refreshNext().then(function (ready) {
          if (ready) {
            setStatus(code === "0" ? "Tap Print." : "That drink did not print. Tap Print again.");
          } else {
            setStatus(code === "0" ? "Printed." : "That drink did not print.");
          }
        });
      });
  }

  settleReturn();
  window.setInterval(refreshNext, 4000);
})();
