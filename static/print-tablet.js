/* Barista tablet: Star PassPRNT on the paired TSP100IV (58mm, Bluetooth).
   One drink is one ticket. The checkbox stays on this browser only.
   Drinks from any device print once, oldest first. A failed return
   puts just that drink back; the others in the order keep going. */
(function () {
  var KEY = "sunshine-print-tablet";
  var SINCE = "sunshine-print-since";
  var box = document.getElementById("print-tablet");
  var leaving = false;

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

  if (box) {
    box.checked = enabled();
    box.addEventListener("change", function () {
      if (box.checked) {
        try {
          localStorage.setItem(SINCE, String(Date.now()));
        } catch (err) {}
      }
      setEnabled(box.checked);
      if (box.checked) pullNext();
    });
  }

  function go(url) {
    leaving = true;
    window.location.href = url;
  }

  function claim(lineId) {
    if (leaving || !lineId) return;
    fetch("/test/drinks/lines/" + lineId + "/print-claim", { method: "POST" })
      .then(function (response) {
        if (!response.ok) return null;
        return response.json();
      })
      .then(function (data) {
        if (data && data.passprnt) go(data.passprnt);
      })
      .catch(function () {});
  }

  function pullNext(skipId) {
    if (!enabled() || leaving) return;
    fetch("/test/drinks/print-feed")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var drinks = (data && data.drinks) || [];
        var cutoff = sinceMs();
        for (var i = 0; i < drinks.length; i++) {
          if (skipId && String(drinks[i].id) === String(skipId)) continue;
          if (Number(drinks[i].created_ms) > cutoff) {
            claim(drinks[i].id);
            return;
          }
        }
      })
      .catch(function () {});
  }

  function settleReturn() {
    var params = new URLSearchParams(window.location.search);
    var lineId = params.get("print_line");
    var code = params.get("passprnt_code");
    if (!lineId || code === null) {
      pullNext();
      return;
    }
    var path = code === "0" ? "print-done" : "print-release";
    fetch("/test/drinks/lines/" + lineId + "/" + path, { method: "POST" })
      .finally(function () {
        window.history.replaceState(null, "", "/test/drinks");
        /* A failed drink stays queued. Skip it once so the next drink still prints. */
        pullNext(code === "0" ? null : lineId);
      });
  }

  document.body.addEventListener("htmx:afterRequest", function (event) {
    var xhr = event.detail && event.detail.xhr;
    if (!xhr || !event.detail.successful) return;
    if ((xhr.getResponseHeader("HX-Trigger") || "") !== "orderPlaced") return;
    var lines = xhr.getResponseHeader("X-Print-Lines") || "";
    var first = lines.split(",")[0];
    if (enabled() && first) claim(first);
  });

  settleReturn();
  window.setInterval(pullNext, 4000);
})();
