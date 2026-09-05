/* Persist tap-to-clear on iPad Safari: cookie alone is dropped; polls send localStorage. */
(function () {
  const STORE = "kds_cleared";
  const MAX = 80;

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
    if (!oid) return;
    const ids = loadCleared();
    if (ids.indexOf(oid) === -1) ids.push(oid);
    saveCleared(ids);
  }

  window.kdsRememberCleared = addCleared;
  window.kdsLoadCleared = loadCleared;

  document.body.addEventListener("htmx:afterRequest", function (ev) {
    if (!ev.detail || !ev.detail.successful) return;
    const elt = ev.detail.elt;
    const oid = elt && elt.getAttribute && elt.getAttribute("data-order-id");
    if (oid) addCleared(oid);
  });
})();
