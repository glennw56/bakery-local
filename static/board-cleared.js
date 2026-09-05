/* Pushed/cleared list is client-owned. If an order_id is in the list, do not show it. */
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

  function hideCard(el) {
    if (!el) return;
    try {
      el.hidden = true;
    } catch (e) {}
    if (el.setAttribute) el.setAttribute("hidden", "");
    if (el.style) el.style.display = "none";
    if (el.remove) el.remove();
    else if (el.parentNode) el.parentNode.removeChild(el);
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
      if (oid && ids.has(oid)) hideCard(el);
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
    const oid = card.getAttribute("data-order-id");
    addCleared(oid);
    // Hide now (~1s smoke). Do not wait on DELETE / cookie / hx-vals.
    hideCard(card);
  }

  window.kdsRememberCleared = rememberCleared;
  window.kdsLoadCleared = loadCleared;
  window.kdsFilterCleared = hideClearedTickets;

  if (document.body && document.body.addEventListener) {
    document.body.addEventListener("pointerup", rememberTap, true);
    document.body.addEventListener("click", rememberTap, true);

    // Server cleared= is optional backup only.
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

    document.body.addEventListener("htmx:afterSwap", hideClearedTickets);
    document.body.addEventListener("htmx:afterSettle", hideClearedTickets);
  }

  if (document.addEventListener) {
    document.addEventListener("DOMContentLoaded", hideClearedTickets);
  }
  if (window.addEventListener) {
    window.addEventListener("pageshow", hideClearedTickets);
  }

  hideClearedTickets();
})();
