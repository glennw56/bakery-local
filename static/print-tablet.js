/* Barista tablet: Star PassPRNT on the paired TSP100IV (58mm, Bluetooth).
   Android Chrome drops starpassprnt:// navigations that are not a user tap
   (fetch, poll, and timers included). Place order opens the first drink
   inside that tap. Later drinks wait on Print next drink. */
(function () {
  var KEY = "sunshine-print-tablet";
  var SINCE = "sunshine-print-since";
  var box = document.getElementById("print-tablet");
  var banner = document.getElementById("print-banner");
  var statusEl = document.getElementById("print-status");
  var nextBtn = document.getElementById("print-next");
  var fallback = document.getElementById("print-fallback");
  var testLink = document.getElementById("test-print");
  var form = document.querySelector("form.kds-menu");
  var placeBtn = form ? form.querySelector("button.kds-place") : null;
  var fallbackTimer = 0;

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
    var busy = (statusEl && statusEl.textContent) ||
      (nextBtn && !nextBtn.hidden) ||
      (fallback && !fallback.hidden);
    banner.hidden = !busy;
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || "";
    showBanner();
  }

  function armFallback(url) {
    setStatus("Sending to PassPRNT…");
    if (!fallback) return;
    fallback.href = url;
    fallback.hidden = true;
    showBanner();
    window.clearTimeout(fallbackTimer);
    fallbackTimer = window.setTimeout(function () {
      if (document.visibilityState !== "visible") return;
      setStatus("PassPRNT did not open.");
      fallback.hidden = false;
      showBanner();
    }, 2000);
  }

  function openPassprnt(url) {
    armFallback(url);
    window.location.href = url;
  }

  function syncPost(url, body) {
    var xhr = new XMLHttpRequest();
    xhr.open("POST", url, false);
    if (body) {
      xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
      xhr.setRequestHeader("HX-Request", "true");
    }
    xhr.send(body || null);
    return xhr;
  }

  function formBody(target) {
    var params = new URLSearchParams();
    var elements = target.elements;
    for (var i = 0; i < elements.length; i++) {
      var el = elements[i];
      if (!el.name || el.disabled) continue;
      if ((el.type === "checkbox" || el.type === "radio") && !el.checked) continue;
      params.append(el.name, el.value);
    }
    return params.toString();
  }

  function labelFor(drink) {
    var name = (drink && (drink.drink_name || drink.ticket_name)) || "";
    return name ? "Print next drink · " + name : "Print next drink";
  }

  function refreshNext() {
    if (!nextBtn) return Promise.resolve(false);
    if (!enabled()) {
      nextBtn.hidden = true;
      showBanner();
      return Promise.resolve(false);
    }
    return fetch("/test/drinks/print-feed")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var drinks = (data && data.drinks) || [];
        var cutoff = sinceMs();
        for (var i = 0; i < drinks.length; i++) {
          if (Number(drinks[i].created_ms) > cutoff) {
            nextBtn.hidden = false;
            nextBtn.setAttribute("data-line", String(drinks[i].id));
            nextBtn.textContent = labelFor(drinks[i]);
            showBanner();
            return true;
          }
        }
        nextBtn.hidden = true;
        nextBtn.removeAttribute("data-line");
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
      refreshNext();
    });
  }

  if (testLink) {
    testLink.addEventListener("click", function () {
      armFallback(testLink.href);
    });
  }

  if (fallback) {
    fallback.addEventListener("click", function () {
      setStatus("Sending to PassPRNT…");
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener("click", function () {
      var lineId = nextBtn.getAttribute("data-line");
      if (!lineId) return;
      var xhr;
      try {
        xhr = syncPost("/test/drinks/lines/" + lineId + "/print-claim");
      } catch (err) {
        setStatus("Could not reach the printer link. Tap again.");
        return;
      }
      if (xhr.status === 409) {
        setStatus("That drink is already printing.");
        refreshNext();
        return;
      }
      if (xhr.status !== 200) {
        setStatus("Could not reach the printer link. Tap again.");
        return;
      }
      var data = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch (err) {
        data = {};
      }
      if (!data.passprnt) {
        setStatus("Could not reach the printer link. Tap again.");
        return;
      }
      nextBtn.hidden = true;
      openPassprnt(data.passprnt);
    });
  }

  if (placeBtn && form) {
    placeBtn.addEventListener("click", function (event) {
      if (!enabled()) return;
      event.preventDefault();
      event.stopPropagation();
      var placed;
      try {
        placed = syncPost("/test/drinks/orders", formBody(form));
      } catch (err) {
        setStatus("Could not place the order. Tap again.");
        return;
      }
      var board = document.getElementById("order-board");
      if (board && placed.responseText) {
        board.innerHTML = placed.responseText;
        if (window.htmx) window.htmx.process(board);
      }
      var trigger = placed.getResponseHeader("HX-Trigger") || "";
      if (trigger === "orderPlaced") form.reset();
      var lines = (placed.getResponseHeader("X-Print-Lines") || "").split(",");
      var first = lines[0];
      if (!first) {
        setStatus("");
        return;
      }
      var claimed;
      try {
        claimed = syncPost("/test/drinks/lines/" + first + "/print-claim");
      } catch (err) {
        setStatus("Order saved. Tap Print next drink.");
        refreshNext();
        return;
      }
      if (claimed.status !== 200) {
        setStatus("Order saved. Tap Print next drink.");
        refreshNext();
        return;
      }
      var data = {};
      try {
        data = JSON.parse(claimed.responseText);
      } catch (err) {
        data = {};
      }
      if (!data.passprnt) {
        setStatus("Order saved. Tap Print next drink.");
        refreshNext();
        return;
      }
      openPassprnt(data.passprnt);
    }, true);
  }

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
      refreshNext();
      return;
    }
    var path = code === "0" ? "print-done" : "print-release";
    fetch("/test/drinks/lines/" + lineId + "/" + path, { method: "POST" })
      .finally(function () {
        window.history.replaceState(null, "", "/test/drinks");
        if (fallback) fallback.hidden = true;
        refreshNext().then(function (hasNext) {
          if (hasNext) {
            setStatus(code === "0" ? "Tap Print next drink." : "That drink did not print. Tap Print next drink to try again.");
          } else {
            setStatus(code === "0" ? "Printed." : "That drink did not print.");
          }
        });
      });
  }

  settleReturn();
  window.setInterval(refreshNext, 4000);
})();
