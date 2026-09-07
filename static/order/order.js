(() => {
  const CART_KEY = "sunshine_qr_cart_v1";
  const app = document.getElementById("app");
  if (!app) return;

  const state = {
    menu: null,
    cart: loadCart(),
    drinkQty: 1,
    drinkMods: {},
    paying: false,
    statusTimer: 0,
    filter: "coffee",
  };

  function loadCart() {
    try {
      const raw = sessionStorage.getItem(CART_KEY);
      const data = raw ? JSON.parse(raw) : null;
      if (data && Array.isArray(data.items)) return data;
    } catch {
      /* ignore */
    }
    return { items: [], pickup: "to-go", name: "", phone: "" };
  }

  function saveCart() {
    sessionStorage.setItem(CART_KEY, JSON.stringify(state.cart));
  }

  function drinkCount() {
    return state.cart.items.reduce((n, it) => n + (it.qty || 1), 0);
  }

  function pathOf() {
    return location.pathname.replace(/\/+$/, "") || "/";
  }

  function qs(name) {
    return new URLSearchParams(location.search).get(name) || "";
  }

  function drinkById(id) {
    return (state.menu?.drinks || []).find((d) => d.id === id) || null;
  }

  function optionLabel(drink, groupId, optionId) {
    const group = (drink.groups || []).find((g) => g.id === groupId);
    const opt = (group?.options || []).find((o) => o.id === optionId);
    return opt?.label || optionId;
  }

  function itemDetail(item) {
    const drink = drinkById(item.id);
    if (!drink) return "";
    const bits = [];
    for (const group of drink.groups || []) {
      if (group.type === "multi") {
        for (const oid of item.modifiers?.[group.id] || []) {
          bits.push(optionLabel(drink, group.id, oid));
        }
      } else if (item.modifiers?.[group.id]) {
        bits.push(optionLabel(drink, group.id, item.modifiers[group.id]));
      }
    }
    return bits.join(" · ");
  }

  function defaultMods(drink) {
    const mods = {};
    const defaults = drink.defaults || {};
    for (const group of drink.groups || []) {
      if (group.type === "multi") {
        mods[group.id] = Array.isArray(defaults[group.id]) ? [...defaults[group.id]] : [];
      } else {
        mods[group.id] = defaults[group.id] || group.options?.[0]?.id || "";
      }
    }
    return mods;
  }

  function lineCents(item) {
    const drink = drinkById(item.id);
    if (!drink) return 0;
    let unit = drink.price_cents || 0;
    for (const group of drink.groups || []) {
      if (group.type === "multi") {
        for (const oid of item.modifiers?.[group.id] || []) {
          const opt = (group.options || []).find((o) => o.id === oid);
          unit += opt?.price_cents || 0;
        }
      } else {
        const oid = item.modifiers?.[group.id];
        const opt = (group.options || []).find((o) => o.id === oid);
        unit += opt?.price_cents || 0;
      }
    }
    return unit * (item.qty || 1);
  }

  function cartTotal() {
    return state.cart.items.reduce((n, it) => n + lineCents(it), 0);
  }

  function money(cents) {
    return `$${(cents / 100).toFixed(2)}`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function imgTag(src, alt, cls) {
    const fallback = "/static/order/drinks/viet-iced-coffee.svg";
    return `<img class="${cls || ""}" src="${escapeHtml(src)}" alt="${escapeHtml(alt)}" onerror="this.onerror=null;this.src='${fallback}'">`;
  }

  function sticky(label, extraClass = "") {
    return `<div class="sticky"><button class="cta ${extraClass}" type="button" id="primary-cta">${escapeHtml(label)}</button></div>`;
  }

  function backLink(label, href) {
    return `<button class="back" type="button" data-go="${escapeHtml(href)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M15 19l-7-7 7-7"/></svg> ${escapeHtml(label)}</button>`;
  }

  function addItem(drinkId, mods, qty) {
    state.cart.items.push({
      id: drinkId,
      qty: qty || 1,
      modifiers: JSON.parse(JSON.stringify(mods)),
    });
    saveCart();
  }

  function renderMenu() {
    const drinks = state.menu?.drinks || [];
    const coffee = drinks.filter((d) => d.category === "coffee");
    const tea = drinks.filter((d) => d.category === "tea");
    const showCoffee = true;
    const showTea = true;
    const n = drinkCount();
    const cta = n ? `Review order · ${n} drink${n === 1 ? "" : "s"}` : "Review order";
    app.innerHTML = `
      <section class="screen">
        <div class="brand-block">
          <h1 class="brand">Sunshine's</h1>
          <p class="sub">Drinks</p>
        </div>
        <div class="pills">
          <button class="pill ${state.filter === "coffee" ? "on" : ""}" type="button" data-filter="coffee">Coffee</button>
          <button class="pill ${state.filter === "tea" ? "on" : ""}" type="button" data-filter="tea">Tea</button>
        </div>
        <p class="hint">Tap a drink. Pay on this phone.</p>
        ${showCoffee ? section("Coffee", coffee, "coffee") : ""}
        ${showTea ? section("Tea", tea, "tea") : ""}
      </section>
      ${sticky(cta)}`;
    app.querySelectorAll("[data-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.filter = btn.getAttribute("data-filter");
        renderMenu();
        document.getElementById(`sec-${state.filter}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    app.querySelectorAll("[data-open]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        history.pushState({}, "", `/order/d/${el.getAttribute("data-open")}`);
        route();
      });
    });
    app.querySelectorAll("[data-add]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const drink = drinkById(btn.getAttribute("data-add"));
        if (!drink) return;
        addItem(drink.id, defaultMods(drink), 1);
        renderMenu();
      });
    });
    document.getElementById("primary-cta")?.addEventListener("click", () => {
      history.pushState({}, "", "/order/review");
      route();
    });
  }

  function section(title, drinks, key) {
    if (!drinks.length) return "";
    return `<h2 class="section-label" id="sec-${escapeHtml(key)}">${escapeHtml(title)}</h2>
      <div class="drink-list">
        ${drinks.map((d) => `
          <a class="drink-card" href="/order/d/${escapeHtml(d.id)}" data-open="${escapeHtml(d.id)}">
            ${imgTag(d.photo, d.name)}
            <div>
              <strong>${escapeHtml(d.name)}</strong>
              <span>${escapeHtml(d.description)}</span>
            </div>
            <button class="add" type="button" data-add="${escapeHtml(d.id)}" aria-label="Add ${escapeHtml(d.name)}">+</button>
          </a>`).join("")}
      </div>`;
  }

  function renderDrink(id) {
    const drink = drinkById(id);
    if (!drink) {
      history.replaceState({}, "", "/order");
      renderMenu();
      return;
    }
    if (!state.drinkMods.id || state.drinkMods.id !== drink.id) {
      state.drinkMods = { id: drink.id, ...defaultMods(drink) };
      state.drinkQty = 1;
    }
    const groups = (drink.groups || []).map((group) => {
      if (group.type === "multi") {
        const selected = new Set(state.drinkMods[group.id] || []);
        return `<div class="mod-block"><h2>${escapeHtml(group.label)}</h2>
          <div class="mod-row">${(group.options || []).map((opt) => `
            <button class="pill check ${selected.has(opt.id) ? "on" : ""}" type="button" data-multi="${escapeHtml(group.id)}" data-opt="${escapeHtml(opt.id)}">${escapeHtml(opt.label)}</button>
          `).join("")}</div></div>`;
      }
      const current = state.drinkMods[group.id];
      return `<div class="mod-block"><h2>${escapeHtml(group.label)}</h2>
        <div class="mod-row">${(group.options || []).map((opt) => `
          <button class="pill check ${current === opt.id ? "on" : ""}" type="button" data-single="${escapeHtml(group.id)}" data-opt="${escapeHtml(opt.id)}">${escapeHtml(opt.label)}</button>
        `).join("")}</div></div>`;
    }).join("");
    app.innerHTML = `
      <section class="screen">
        ${backLink("Drinks", "/order")}
        <div class="hero">${imgTag(drink.photo, drink.name)}</div>
        <h1 class="drink-title">${escapeHtml(drink.name)}</h1>
        <p class="drink-desc">${escapeHtml(drink.description)}</p>
        ${groups}
        <div class="mod-block"><h2>Qty</h2>
          <div class="qty">
            <button type="button" data-qty="-1" aria-label="Less">−</button>
            <span>${state.drinkQty}</span>
            <button type="button" data-qty="1" aria-label="More">+</button>
          </div>
        </div>
      </section>
      ${sticky("Add to order")}`;
    app.querySelector("[data-go]")?.addEventListener("click", go);
    app.querySelectorAll("[data-single]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.drinkMods[btn.getAttribute("data-single")] = btn.getAttribute("data-opt");
        renderDrink(id);
      });
    });
    app.querySelectorAll("[data-multi]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const gid = btn.getAttribute("data-multi");
        const oid = btn.getAttribute("data-opt");
        const cur = new Set(state.drinkMods[gid] || []);
        if (cur.has(oid)) cur.delete(oid);
        else cur.add(oid);
        state.drinkMods[gid] = [...cur];
        renderDrink(id);
      });
    });
    app.querySelectorAll("[data-qty]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.drinkQty = Math.min(9, Math.max(1, state.drinkQty + Number(btn.getAttribute("data-qty"))));
        renderDrink(id);
      });
    });
    document.getElementById("primary-cta")?.addEventListener("click", () => {
      const { id: _id, ...mods } = state.drinkMods;
      addItem(drink.id, mods, state.drinkQty);
      history.pushState({}, "", "/order");
      route();
    });
  }

  function renderReview() {
    if (!state.cart.items.length) {
      app.innerHTML = `
        <section class="screen">
          <div class="brand-block">
            <h1 class="brand">Sunshine's</h1>
            <p class="sub">Review and pay</p>
          </div>
          <p class="hint">Your order is empty. Pick a drink first.</p>
        </section>
        ${sticky("Back to drinks")}`;
      document.getElementById("primary-cta")?.addEventListener("click", () => {
        history.pushState({}, "", "/order");
        route();
      });
      return;
    }
    const cards = state.cart.items.map((item, idx) => {
      const drink = drinkById(item.id);
      return `<article class="cart-card">
        ${imgTag(drink?.photo || "", drink?.name || item.id)}
        <div>
          <h3>${escapeHtml(drink?.name || item.id)}</h3>
          <p>${escapeHtml(itemDetail(item))}</p>
          <div class="qty">
            <button type="button" data-line-qty="${idx}" data-delta="-1">−</button>
            <span>${item.qty}</span>
            <button type="button" data-line-qty="${idx}" data-delta="1">+</button>
          </div>
        </div>
      </article>`;
    }).join("");
    const mode = state.menu?.pay_mode || "off";
    const banner = mode === "off"
      ? `<p class="banner">Pay is not configured on this drinks service yet. Glenn needs the Square token on Cloud Run.</p>`
      : mode === "demo"
        ? `<p class="banner">Laptop demo pay — no Square charge. Status screens still run.</p>`
        : "";
    const cta = mode === "square" ? `Pay now · ${money(cartTotal())}` : mode === "demo" ? `Demo pay · ${money(cartTotal())}` : "Pay unavailable";
    app.innerHTML = `
      <section class="screen">
        <div class="brand-block">
          <h1 class="brand">Sunshine's</h1>
          <p class="sub">Review and pay</p>
        </div>
        ${banner}
        ${cards}
        <div class="pickup-box">
          <h2>Pickup</h2>
          <div class="pills">
            <button class="pill ${state.cart.pickup === "for-here" ? "on" : ""}" type="button" data-pickup="for-here">For here</button>
            <button class="pill ${state.cart.pickup === "to-go" ? "on" : ""}" type="button" data-pickup="to-go">To go</button>
          </div>
          <div class="field">
            <label for="pickup-name">Name for pickup</label>
            <input id="pickup-name" name="name" autocomplete="name" placeholder="Your name" maxlength="40" value="${escapeHtml(state.cart.name)}">
          </div>
          <div class="field">
            <label for="pickup-phone">Phone for a ready text (optional)</label>
            <input id="pickup-phone" name="phone" autocomplete="tel" inputmode="tel" placeholder="205…" value="${escapeHtml(state.cart.phone)}">
          </div>
        </div>
        <p class="pay-note">Apple Pay, card, or Google Pay.</p>
        <p class="err" id="pay-err" hidden></p>
      </section>
      ${sticky(cta)}`;
    app.querySelectorAll("[data-pickup]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.cart.pickup = btn.getAttribute("data-pickup");
        saveCart();
        renderReview();
      });
    });
    app.querySelectorAll("[data-line-qty]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.getAttribute("data-line-qty"));
        const delta = Number(btn.getAttribute("data-delta"));
        const item = state.cart.items[idx];
        if (!item) return;
        item.qty += delta;
        if (item.qty < 1) state.cart.items.splice(idx, 1);
        saveCart();
        renderReview();
      });
    });
    const name = document.getElementById("pickup-name");
    const phone = document.getElementById("pickup-phone");
    name?.addEventListener("input", () => {
      state.cart.name = name.value;
      saveCart();
    });
    phone?.addEventListener("input", () => {
      state.cart.phone = phone.value;
      saveCart();
    });
    document.getElementById("primary-cta")?.addEventListener("click", pay);
  }

  async function pay() {
    if (state.paying) return;
    const err = document.getElementById("pay-err");
    const name = (state.cart.name || "").trim();
    if (!name) {
      if (err) {
        err.hidden = false;
        err.textContent = "Add a name for pickup.";
      }
      document.getElementById("pickup-name")?.focus();
      return;
    }
    const mode = state.menu?.pay_mode || "off";
    if (mode === "off") return;
    state.paying = true;
    const cta = document.getElementById("primary-cta");
    if (cta) {
      cta.disabled = true;
      cta.textContent = "Starting checkout…";
    }
    try {
      const res = await fetch("/order/api/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          name,
          phone: state.cart.phone || "",
          pickup: state.cart.pickup || "to-go",
          items: state.cart.items,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || "Could not start checkout.");
      }
      if (data.order_id) sessionStorage.setItem("sunshine_qr_oid", data.order_id);
      if (data.url) {
        location.href = data.url;
        return;
      }
      throw new Error("Checkout URL missing.");
    } catch (ex) {
      state.paying = false;
      if (cta) {
        cta.disabled = false;
        cta.textContent = "Pay now";
      }
      if (err) {
        err.hidden = false;
        err.textContent = ex.message || "Checkout failed.";
      }
    }
  }

  function stepperHtml(status) {
    const rank = status === "ready" ? 3 : status === "making" || status === "paid" ? 2 : 1;
    const cls = rank === 3 ? "three" : rank === 2 ? "two" : "";
    const steps = ["Paid", "Making", "Ready"];
    return `<div class="stepper ${cls}" aria-label="Order status">
      ${steps.map((label, i) => {
        const n = i + 1;
        const on = n < rank ? "done" : n === rank ? "on" : "";
        return `<div class="step ${on}"><span class="dot"></span><span>${label}</span></div>`;
      }).join("")}
    </div>`;
  }

  function renderStatus(payload) {
    const status = payload?.status || "pending";
    const items = payload?.items?.length ? payload.items : state.cart.items.map((it) => {
      const drink = drinkById(it.id);
      return { name: drink?.name || it.id, detail: itemDetail(it), photo: drink?.photo, qty: it.qty };
    });
    const names = items.map((it) => it.name).filter(Boolean).join(" · ");
    const hero = items[0]?.photo || "/static/order/drinks/viet-iced-coffee.svg";
    const pickup = payload?.pickup || (state.cart.pickup === "for-here" ? "for here" : "to go");
    const number = payload?.order_number || "—";
    const textOn = payload?.text_opt_in || Boolean((state.cart.phone || "").trim());
    if (status === "ready") {
      app.innerHTML = `
        <section class="screen pad-status">
          <div class="brand-block">
            <h1 class="brand">Sunshine's</h1>
            <p class="sub">Head to the pickup counter</p>
          </div>
          ${stepperHtml("ready")}
          <article class="status-card">
            ${imgTag(hero, items[0]?.name || "Drink")}
            <div class="status-inner">
              <h2>Ready. Go to pickup.</h2>
              <p>${escapeHtml(names)}</p>
            </div>
          </article>
        </section>
        ${sticky("Go to pickup")}`;
      document.getElementById("primary-cta")?.addEventListener("click", () => {
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
      return;
    }
    if (status === "pending") {
      app.innerHTML = `
        <section class="screen pad-status">
          <div class="brand-block">
            <h1 class="brand">Sunshine's</h1>
            <p class="sub">Waiting for payment</p>
          </div>
          ${stepperHtml("pending")}
          <p class="hint">If you already paid, this page will catch up in a few seconds.</p>
        </section>`;
      return;
    }
    app.innerHTML = `
      <section class="screen pad-status">
        <div class="brand-block">
          <h1 class="brand">Sunshine's</h1>
          <p class="sub">We're making it</p>
        </div>
        ${stepperHtml("making")}
        <article class="status-card">
          ${imgTag(hero, items[0]?.name || "Drink")}
        </article>
        <p class="order-meta">Order ${escapeHtml(String(number))} • ${escapeHtml(pickup)}</p>
        <p class="order-items">${items.map((it) => escapeHtml(it.name)).join("<br>")}</p>
        <p class="order-note">${textOn ? "We'll text when it's at pickup." : "We'll have it at pickup. Optional phone is only for a Square ready text."}</p>
      </section>
      ${sticky(textOn ? "We will text you" : "See you at pickup", "rose")}`;
  }

  async function loadStatus() {
    const oid = qs("oid") || qs("orderId") || qs("order_id") || qs("transactionId") || sessionStorage.getItem("sunshine_qr_oid") || "";
    const checkoutId = qs("checkoutId") || "";
    const params = new URLSearchParams();
    if (oid) params.set("oid", oid);
    if (checkoutId) params.set("checkoutId", checkoutId);
    try {
      const res = await fetch(`/order/api/status?${params.toString()}`, { headers: { Accept: "application/json" } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        renderStatus({ status: oid?.startsWith("demo") ? "making" : "pending", items: [] });
        return data;
      }
      renderStatus(data);
      return data;
    } catch {
      renderStatus({ status: "pending", items: [] });
      return null;
    }
  }

  function startStatusPoll() {
    clearInterval(state.statusTimer);
    loadStatus();
    state.statusTimer = setInterval(async () => {
      const data = await loadStatus();
      if (data?.status === "ready" || data?.status === "canceled") {
        clearInterval(state.statusTimer);
      }
    }, 4000);
  }

  function go(ev) {
    const href = ev.currentTarget.getAttribute("data-go");
    if (!href) return;
    history.pushState({}, "", href);
    route();
  }

  function route() {
    clearInterval(state.statusTimer);
    const path = pathOf();
    if (path === "/order/status") {
      renderStatus({ status: "making", items: [] });
      startStatusPoll();
      return;
    }
    if (path === "/order/review") {
      renderReview();
      return;
    }
    const drinkMatch = path.match(/^\/order\/d\/([^/]+)$/);
    if (drinkMatch) {
      renderDrink(drinkMatch[1]);
      return;
    }
    renderMenu();
  }

  async function boot() {
    try {
      const res = await fetch("/order/api/menu", { headers: { Accept: "application/json" } });
      state.menu = await res.json();
    } catch {
      app.innerHTML = `<p class="boot">Could not load the drink menu.</p>`;
      return;
    }
    route();
  }

  window.addEventListener("popstate", route);
  boot();
})();
