/* Barista tablet: Star PassPRNT on the paired TSP100IV (58mm, Bluetooth).
   The checkbox stays on this browser only. Orders from other devices print
   once, oldest first, and a failed return can try again. */
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

  function claim(orderId) {
    if (leaving) return;
    fetch("/test/drinks/orders/" + orderId + "/print-claim", { method: "POST" })
      .then(function (response) {
        if (!response.ok) return null;
        return response.json();
      })
      .then(function (data) {
        if (data && data.passprnt) go(data.passprnt);
      })
      .catch(function () {});
  }

  function pullNext() {
    if (!enabled() || leaving) return;
    fetch("/test/drinks/print-feed")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var orders = (data && data.orders) || [];
        var cutoff = sinceMs();
        for (var i = 0; i < orders.length; i++) {
          if (Number(orders[i].created_ms) > cutoff) {
            claim(orders[i].id);
            return;
          }
        }
      })
      .catch(function () {});
  }

  function settleReturn() {
    var params = new URLSearchParams(window.location.search);
    var orderId = params.get("print_order");
    var code = params.get("passprnt_code");
    if (!orderId || code === null) {
      pullNext();
      return;
    }
    var path = code === "0" ? "print-done" : "print-release";
    fetch("/test/drinks/orders/" + orderId + "/" + path, { method: "POST" })
      .finally(function () {
        window.history.replaceState(null, "", "/test/drinks");
        pullNext();
      });
  }

  document.body.addEventListener("htmx:afterRequest", function (event) {
    var xhr = event.detail && event.detail.xhr;
    if (!xhr || !event.detail.successful) return;
    if ((xhr.getResponseHeader("HX-Trigger") || "") !== "orderPlaced") return;
    var id = xhr.getResponseHeader("X-Print-Order");
    if (enabled() && id) claim(id);
  });

  settleReturn();
  window.setInterval(pullNext, 4000);
})();
