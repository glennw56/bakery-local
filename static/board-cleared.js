/* Persist tap-to-clear on iPad Safari; hide already-cleared tickets after every poll swap. */
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

  function hideClearedTickets() {
    const list = document.getElementById && document.getElementById("ticket-list");
    if (!list || !list.querySelectorAll) return;
    const ids = new Set(loadCleared());
    if (!ids.size) return;
    const cards = list.querySelectorAll(".kds-ticket[data-order-id]");
    for (let i = 0; i < cards.length; i++) {
      const el = cards[i];
      const oid = (el.getAttribute && el.getAttribute("data-order-id")) || "";
      if (!oid || !ids.has(oid)) continue;
      if (el.remove) el.remove();
      else if (el.parentNode) el.parentNode.removeChild(el);
    }
  }

  function rememberCleared(oid) {
    addCleared(oid);
    hideClearedTickets();
  }

  function ticketFromEvent(ev) {
    const detail = ev && ev.detail;
    const elt = detail && detail.elt;
    if (elt && elt.getAttribute) {
      const oid = elt.getAttribute("data-order-id");
      if (oid) return oid;
    }
    const t = ev && ev.target;
    const card = t && t.closest && t.closest("#ticket-list .kds-ticket[data-order-id]");
    return card ? card.getAttribute("data-order-id") || "" : "";
  }

  function rememberTap(ev) {
    const t = ev && ev.target;
    const card = t && t.closest && t.closest("#ticket-list .kds-ticket[data-order-id]");
    if (!card) return;
    addCleared(card.getAttribute("data-order-id"));
  }

  window.kdsRememberCleared = rememberCleared;
  window.kdsLoadCleared = loadCleared;
  window.kdsFilterCleared = hideClearedTickets;

  if (document.body && document.body.addEventListener) {
    // Write on tap immediately. Do not remove the card here — HTMX still needs the click to DELETE.
    document.body.addEventListener("pointerup", rememberTap, true);
    document.body.addEventListener("click", rememberTap, true);

    document.body.addEventListener("htmx:configRequest", function (ev) {
      if (ev.detail && ev.detail.parameters) {
        ev.detail.parameters.cleared = loadCleared().join(",");
      }
    });

    document.body.addEventListener("htmx:afterRequest", function (ev) {
      if (!ev.detail || !ev.detail.successful) return;
      const oid = ticketFromEvent(ev);
      if (oid) addCleared(oid);
      hideClearedTickets();
    });

    // Must-have: after every #ticket-list swap / poll, hide cards already in the local list.
    // Do not require ev.detail.target.id — iPad Safari / HTMX detail shape varies.
    document.body.addEventListener("htmx:afterSwap", hideClearedTickets);
    document.body.addEventListener("htmx:afterSettle", hideClearedTickets);
  }

  hideClearedTickets();
})();
