/*
 * Event page: live seat map, general admission steppers, the ten minute hold
 * and payment.
 *
 * The flow is a small state machine rendered into the fixed checkout bar:
 *
 *   idle -> selecting -> holding -> held -> paying -> paid
 *                                    |
 *                                    +-> expired (hold ran out) -> idle
 *
 * The server is the source of truth for every seat. This script only mirrors
 * it: it refetches the seat map after any error or socket reconnect.
 */
(() => {
  "use strict";

  const TF = window.TF;
  const cfg = TF.config();
  if (!cfg) return;

  const HOLD_KEY = `tf:hold:${cfg.eventId}`;
  const URGENT_SECONDS = 60;
  const typesById = new Map(cfg.ticketTypes.map((t) => [t.id, t]));
  const seatedTypes = cfg.ticketTypes.filter((t) => t.inventory_mode === "seated");
  const currency = cfg.ticketTypes[0]?.currency || "USD";

  const state = {
    phase: "idle",
    seats: new Map(), // seat_id -> seat (with .el and .section)
    selected: new Set(), // seat ids picked but not held yet
    mine: new Set(), // seat ids in the current hold
    ga: null, // { typeId, qty }
    hold: null,
    deadline: 0, // performance.now() timestamp when the hold expires
    timer: null,
    holdSocket: null,
    payment: null, // { orderId, stripe, elements }
  };

  const el = {
    sections: document.getElementById("seat-sections"),
    bar: document.getElementById("checkout-bar"),
    title: document.getElementById("cb-title"),
    detail: document.getElementById("cb-detail"),
    timer: document.getElementById("cb-timer"),
    clock: document.getElementById("cb-clock"),
    primary: document.getElementById("cb-primary"),
    secondary: document.getElementById("cb-secondary"),
    paymentPanel: document.getElementById("payment-panel"),
  };

  /* ------------------------------------------------------------ helpers */

  const seatLabel = (seat) => `${seat.row}${seat.number}`;
  const seatName = (seat) => `${seat.section}, row ${seat.row}, seat ${seat.number}`;
  const seatType = (seat) => typesById.get(seat.ticket_type_id) || seatedTypes[0];
  const seatPrice = (seat) => seat.price_cents ?? seatType(seat)?.price_cents ?? 0;

  function goToLogin() {
    location.href = cfg.loginUrl;
  }

  function persistHold() {
    if (!state.hold) return TF.store.remove(HOLD_KEY);
    const saved = TF.store.get(HOLD_KEY) || {};
    TF.store.set(HOLD_KEY, {
      id: state.hold.id,
      seatIds: [...state.mine],
      payKey: saved.id === state.hold.id ? saved.payKey : TF.uuid(),
    });
  }

  function payKey() {
    return TF.store.get(HOLD_KEY)?.payKey || TF.uuid();
  }

  /* ------------------------------------------------------------ seat map */

  function seatClass(seat) {
    if (state.mine.has(seat.seat_id)) return "seat is-mine";
    if (seat.status === "held") return "seat is-held";
    if (seat.status === "sold") return "seat is-sold";
    if (seat.status === "blocked") return "seat is-blocked";
    return "seat";
  }

  function paintSeat(seat) {
    const button = seat.el;
    if (!button) return;
    const free = seat.status === "available";
    const picked = state.selected.has(seat.seat_id);
    button.className = seatClass(seat);
    button.setAttribute("aria-pressed", String(picked));
    button.disabled = !cfg.onSale || (!free && !state.mine.has(seat.seat_id));
    const status = state.mine.has(seat.seat_id)
      ? "held for you"
      : picked
        ? "selected"
        : free
          ? "available"
          : seat.status === "held"
            ? "on hold"
            : "sold";
    button.setAttribute(
      "aria-label",
      `${seatName(seat)}, ${TF.money(seatPrice(seat), currency)}, ${status}`,
    );
    button.title = `${seatType(seat)?.name || seat.section} ${seatLabel(seat)}, ${TF.money(seatPrice(seat), currency)}`;
  }

  function renderSeatMap(sections) {
    const fragment = document.createDocumentFragment();
    state.seats.clear();
    for (const section of sections) {
      const wrapper = document.createElement("div");
      wrapper.className = "seat-section";
      const head = document.createElement("div");
      head.className = "seat-section-head";
      const title = document.createElement("h3");
      title.textContent = section.name;
      const count = document.createElement("span");
      count.className = "muted";
      count.dataset.section = section.name;
      head.append(title, count);
      wrapper.appendChild(head);

      const rows = new Map();
      for (const seat of section.seats) {
        if (!rows.has(seat.row)) rows.set(seat.row, []);
        rows.get(seat.row).push(seat);
      }
      const grid = document.createElement("div");
      grid.className = "seat-rows";
      for (const row of [...rows.keys()].sort()) {
        const rowEl = document.createElement("div");
        rowEl.className = "seat-row";
        const left = document.createElement("span");
        left.className = "row-label";
        left.textContent = row;
        rowEl.appendChild(left);
        rows
          .get(row)
          .sort((a, b) => String(a.number).localeCompare(String(b.number), undefined, { numeric: true }))
          .forEach((seat) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = seat.number;
            button.dataset.seatId = String(seat.seat_id);
            const record = { ...seat, section: section.name, el: button };
            state.seats.set(seat.seat_id, record);
            paintSeat(record);
            rowEl.appendChild(button);
          });
        const right = left.cloneNode(true);
        rowEl.appendChild(right);
        grid.appendChild(rowEl);
      }
      wrapper.appendChild(grid);
      fragment.appendChild(wrapper);
    }
    el.sections.replaceChildren(fragment);
    el.sections.setAttribute("aria-busy", "false");
    paintCounts();
  }

  /** "12 of 45 free" per section, recomputed after every live change. */
  function paintCounts() {
    if (!el.sections) return;
    const totals = new Map();
    for (const seat of state.seats.values()) {
      const entry = totals.get(seat.section) || { free: 0, all: 0 };
      entry.all += 1;
      if (seat.status === "available") entry.free += 1;
      totals.set(seat.section, entry);
    }
    for (const count of el.sections.querySelectorAll("[data-section]")) {
      const entry = totals.get(count.dataset.section) || { free: 0, all: 0 };
      count.textContent = `${entry.free} of ${entry.all} free`;
    }
  }

  async function loadSeatMap() {
    if (!el.sections) return;
    try {
      const data = await TF.api(`/events/${cfg.slug}/seats`);
      renderSeatMap(data.sections);
      // Drop selections that are no longer free (e.g. after a reconnect).
      for (const id of [...state.selected]) {
        if (state.seats.get(id)?.status !== "available") state.selected.delete(id);
      }
      render();
    } catch (error) {
      const message = document.createElement("p");
      message.className = "muted";
      message.textContent = "The seat map didn’t load. Refresh the page to try again.";
      el.sections.replaceChildren(message);
      TF.toast(error.message, "error");
    }
  }

  el.sections?.addEventListener("click", (event) => {
    const button = event.target.closest(".seat[data-seat-id]");
    if (!button || button.disabled) return;
    const seat = state.seats.get(Number(button.dataset.seatId));
    if (!seat || state.mine.has(seat.seat_id)) return;
    if (!canChangeSelection()) return;

    if (state.ga) clearGa();
    if (state.selected.has(seat.seat_id)) {
      state.selected.delete(seat.seat_id);
    } else {
      const type = seatType(seat);
      const sameType = [...state.selected].filter((id) => seatType(state.seats.get(id)) === type);
      if (type && sameType.length >= type.max_per_order) {
        TF.toast(`You can choose up to ${type.max_per_order} ${type.name} seats per order.`);
        return;
      }
      state.selected.add(seat.seat_id);
    }
    paintSeat(seat);
    state.phase = state.selected.size ? "selecting" : "idle";
    render();
  });

  /** Live seat changes from other buyers (and from our own holds). */
  function applySeatUpdates(changes) {
    const taken = [];
    for (const change of changes) {
      const seat = state.seats.get(change.seat_id);
      if (!seat) continue;
      seat.status = change.status;
      if (change.status === "available") {
        state.mine.delete(seat.seat_id);
      } else if (!state.mine.has(seat.seat_id)) {
        // While our own hold request is in flight, its seats echo back as
        // "held" before the HTTP response does. They are ours, not "taken".
        const ours = state.phase === "holding" && state.selected.has(seat.seat_id);
        if (!ours) {
          if (state.selected.delete(seat.seat_id)) {
            TF.toast(`${seatName(seat)} was just taken, so we removed it from your selection.`, "live");
          } else {
            taken.push(seatName(seat));
          }
          seat.el?.classList.add("just-taken");
          setTimeout(() => seat.el?.classList.remove("just-taken"), 1300);
        }
      }
      paintSeat(seat);
    }
    paintCounts();
    if (taken.length === 1) TF.toast(`${taken[0]} just got taken`, "live", 2500);
    else if (taken.length > 1) TF.toast(`${taken.length} seats just got taken`, "live", 2500);
    if (state.phase === "selecting" && state.selected.size === 0) state.phase = "idle";
    render();
  }

  /* ---------------------------------------------------- general admission */

  const gaRows = [...document.querySelectorAll(".ticket-option[data-ticket-type]")];

  function paintGa() {
    for (const row of gaRows) {
      const typeId = Number(row.dataset.ticketType);
      const max = Number(row.dataset.max);
      const qty = state.ga?.typeId === typeId ? state.ga.qty : 0;
      const output = row.querySelector("output");
      if (!output) continue;
      output.textContent = String(qty);
      row.classList.toggle("is-selected", qty > 0);
      row.querySelector('[data-step="-1"]').disabled = qty === 0;
      row.querySelector('[data-step="1"]').disabled = !cfg.onSale || qty >= max;
    }
  }

  function clearGa() {
    state.ga = null;
    paintGa();
  }

  for (const row of gaRows) {
    row.addEventListener("click", (event) => {
      const button = event.target.closest("[data-step]");
      if (!button || button.disabled || !canChangeSelection()) return;
      const typeId = Number(row.dataset.ticketType);
      const max = Number(row.dataset.max);
      if (state.selected.size) {
        state.selected.clear();
        state.seats.forEach(paintSeat);
      }
      const current = state.ga?.typeId === typeId ? state.ga.qty : 0;
      const qty = Math.min(max, Math.max(0, current + Number(button.dataset.step)));
      state.ga = qty ? { typeId, qty } : null;
      state.phase = state.ga ? "selecting" : "idle";
      paintGa();
      render();
    });
  }

  function canChangeSelection() {
    if (["holding", "held", "paying"].includes(state.phase)) {
      TF.toast("Release your current hold first to change your tickets.");
      return false;
    }
    if (state.phase === "paid" || state.phase === "expired") state.phase = "idle";
    return true;
  }

  /* -------------------------------------------------------------- holds */

  function selectionSummary() {
    if (state.ga) {
      const type = typesById.get(state.ga.typeId);
      return {
        title: `${state.ga.qty} × ${type.name}`,
        total: type.price_cents * state.ga.qty,
        detail: "General admission",
      };
    }
    const seats = [...state.selected].map((id) => state.seats.get(id)).filter(Boolean);
    return {
      title: `${seats.length} seat${seats.length === 1 ? "" : "s"}`,
      total: seats.reduce((sum, seat) => sum + seatPrice(seat), 0),
      detail: seats.map((seat) => `${seat.section} ${seatLabel(seat)}`).join(", "),
    };
  }

  function holdSummary(hold) {
    const seats = hold.items.filter((item) => item.seat_label).map((item) => item.seat_label);
    const ga = hold.items
      .filter((item) => !item.seat_label)
      .map((item) => `${item.quantity} × ${typesById.get(item.ticket_type_id)?.name || "Ticket"}`);
    return [...ga, ...(seats.length ? [`Seats ${seats.join(", ")}`] : [])].join(", ");
  }

  async function createHold() {
    if (!cfg.authenticated) return goToLogin();
    const body = { event_id: cfg.eventId };
    if (state.ga) {
      body.ticket_type_id = state.ga.typeId;
      body.quantity = state.ga.qty;
    } else {
      body.seat_ids = [...state.selected];
      // Only consulted by the server when a section maps to no ticket type.
      if (seatedTypes.length) body.ticket_type_id = seatedTypes[0].id;
    }
    state.phase = "holding";
    render();
    try {
      const hold = await TF.api("/holds", { method: "POST", body, token: cfg.token });
      state.mine = new Set(state.selected);
      state.selected.clear();
      clearGa();
      startHold(hold);
      TF.toast("Held for 10 minutes. Pay before the timer runs out.");
    } catch (error) {
      if (error.status === 401) return goToLogin();
      state.phase = "selecting";
      TF.toast(error.message, "error");
      if (error.code === "SEAT_UNAVAILABLE" || error.code === "SOLD_OUT") loadSeatMap();
      render();
    }
  }

  function startHold(hold) {
    state.hold = hold;
    state.phase = "held";
    state.deadline = performance.now() + hold.seconds_left * 1000;
    persistHold();
    for (const id of state.mine) {
      const seat = state.seats.get(id);
      if (seat && seat.status === "available") seat.status = "held";
    }
    state.seats.forEach(paintSeat);
    paintCounts();
    clearInterval(state.timer);
    state.timer = setInterval(tick, 250);
    tick();
    connectHoldSocket(hold.id);
    render();
  }

  function tick() {
    if (state.phase !== "held" && state.phase !== "paying") return;
    const left = (state.deadline - performance.now()) / 1000;
    el.clock.textContent = TF.clock(left);
    el.timer.classList.toggle("is-urgent", left <= URGENT_SECONDS);
    if (left <= 0 && state.phase === "held") expireHold();
  }

  function connectHoldSocket(holdId) {
    state.holdSocket?.close();
    state.holdSocket = TF.socket(`/ws/holds/${holdId}?token=${encodeURIComponent(cfg.token)}`, {
      onMessage(message) {
        if (message.type === "hold_tick") {
          // The server clock wins: resync our local countdown to it.
          state.deadline = performance.now() + message.seconds_left * 1000;
        } else if (message.type === "hold_expired" && state.phase === "held") {
          expireHold();
        }
      },
    });
  }

  function endHold() {
    clearInterval(state.timer);
    state.holdSocket?.close();
    state.holdSocket = null;
    state.hold = null;
    state.mine.clear();
    TF.store.remove(HOLD_KEY);
  }

  function expireHold() {
    endHold();
    state.phase = "expired";
    render();
    loadSeatMap();
  }

  async function releaseHold() {
    if (!state.hold) return;
    const id = state.hold.id;
    el.secondary.disabled = true;
    try {
      await TF.api(`/holds/${id}`, { method: "DELETE", token: cfg.token });
      endHold();
      state.phase = "idle";
      TF.toast("Released. Those tickets are back on sale.");
      loadSeatMap();
    } catch (error) {
      TF.toast(error.message, "error");
    } finally {
      el.secondary.disabled = false;
      render();
    }
  }

  /* ------------------------------------------------------------ payment */

  function loadStripe() {
    if (window.Stripe) return Promise.resolve(window.Stripe);
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://js.stripe.com/v3/";
      script.onload = () => resolve(window.Stripe);
      script.onerror = () => reject(new Error("The card form couldn’t load. Check your connection."));
      document.head.appendChild(script);
    });
  }

  async function waitForPaidOrder(orderId) {
    // Stripe confirms in the browser first; the order flips to "paid" when the
    // webhook lands. Poll briefly rather than trusting the browser alone.
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const orders = await TF.api("/me/orders", { token: cfg.token });
      const order = orders.find((item) => item.id === orderId);
      if (order?.status === "paid") return "fulfilled";
      if (order?.status === "refunded") return "refunded_late_payment";
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    return "pending";
  }

  async function pay() {
    if (!state.hold) return;
    state.phase = "paying";
    render();
    try {
      if (state.payment?.stripe) {
        const { error } = await state.payment.stripe.confirmPayment({
          elements: state.payment.elements,
          redirect: "if_required",
          confirmParams: { return_url: `${location.origin}/accounts/` },
        });
        if (error) throw new TF.ApiError(error.message || "The payment didn’t go through.");
        return finish(await waitForPaidOrder(state.payment.orderId));
      }

      const payment = await TF.api(`/checkout/${state.hold.id}/pay`, {
        method: "POST",
        token: cfg.token,
        headers: { "Idempotency-Key": payKey() },
      });

      if (cfg.stripeKey) {
        // Real Stripe: show the card form, then the next click confirms it.
        const Stripe = await loadStripe();
        const stripe = Stripe(cfg.stripeKey);
        const elements = stripe.elements({ clientSecret: payment.client_secret });
        elements.create("payment").mount("#payment-element");
        el.paymentPanel.hidden = false;
        el.paymentPanel.scrollIntoView({ behavior: "smooth", block: "center" });
        state.payment = { orderId: payment.order_id, stripe, elements };
        state.phase = "held";
        return render();
      }

      // Local mode: the fake gateway never sends a webhook, so ask the API to
      // emit the exact event Stripe would. Disabled when real Stripe is on.
      const result = await TF.api(`/dev/simulate-payment/${payment.order_id}`, {
        method: "POST",
        token: cfg.token,
      });
      finish(result.result);
    } catch (error) {
      if (error.status === 401) return goToLogin();
      if (error.code === "HOLD_EXPIRED") return expireHold();
      state.phase = "held";
      TF.toast(error.message, "error");
      render();
    }
  }

  function finish(result) {
    endHold();
    el.paymentPanel.hidden = true;
    state.payment = null;
    if (result === "refunded_late_payment") {
      state.phase = "expired";
      TF.toast("Your hold ran out and the tickets sold, so we refunded you in full.", "error", 8000);
    } else {
      state.phase = "paid";
    }
    render();
    loadSeatMap();
  }

  /* ------------------------------------------------------- checkout bar */

  function render() {
    const { phase } = state;
    const open = phase !== "idle";
    el.bar.classList.toggle("is-open", open);
    el.bar.toggleAttribute("inert", !open);
    el.timer.hidden = !(phase === "held" || phase === "paying");
    el.secondary.hidden = phase !== "held";
    el.primary.disabled = false;

    if (phase === "selecting" || phase === "holding") {
      const summary = selectionSummary();
      el.title.textContent = `${summary.title}, ${TF.money(summary.total, currency)}`;
      el.detail.textContent = summary.detail;
      if (phase === "holding") {
        el.primary.textContent = "Holding…";
        el.primary.disabled = true;
      } else if (!cfg.authenticated) {
        el.primary.textContent = "Log in to continue";
      } else if (!cfg.verified) {
        el.primary.textContent = "Confirm your email first";
        el.primary.disabled = true;
      } else {
        el.primary.textContent = "Hold for 10 minutes";
      }
    } else if (phase === "held" || phase === "paying") {
      el.title.textContent = `Held for you, ${TF.money(state.hold.total_cents, state.hold.currency)}`;
      el.detail.textContent = holdSummary(state.hold);
      el.primary.textContent =
        phase === "paying"
          ? "Paying…"
          : `${state.payment ? "Confirm" : "Pay"} ${TF.money(state.hold.total_cents, state.hold.currency)}`;
      el.primary.disabled = phase === "paying";
    } else if (phase === "paid") {
      el.title.textContent = "You’re going";
      el.detail.textContent = "Your tickets are in My tickets, and a copy is on its way to your inbox.";
      el.primary.textContent = "View my tickets";
    } else if (phase === "expired") {
      el.title.textContent = "Your hold ran out";
      el.detail.textContent = "Those tickets went back on sale. Choose again to try for them.";
      el.primary.textContent = "Choose again";
    }
  }

  el.primary.addEventListener("click", () => {
    if (state.phase === "selecting") return createHold();
    if (state.phase === "held") return pay();
    if (state.phase === "paid") return void (location.href = "/accounts/");
    if (state.phase === "expired") {
      state.phase = "idle";
      render();
      document.getElementById("tickets")?.scrollIntoView({ behavior: "smooth" });
    }
  });
  el.secondary.addEventListener("click", releaseHold);

  /* --------------------------------------------------------------- boot */

  async function resumeHold() {
    const saved = TF.store.get(HOLD_KEY);
    if (!saved?.id || !cfg.token) return;
    try {
      const hold = await TF.api(`/holds/${saved.id}`, { token: cfg.token });
      if (hold.status === "active" && hold.seconds_left > 0) {
        state.mine = new Set(saved.seatIds || []);
        startHold(hold);
        return;
      }
    } catch {
      /* hold gone or not ours any more */
    }
    TF.store.remove(HOLD_KEY);
  }

  render();
  paintGa();
  if (cfg.seated) {
    loadSeatMap().then(resumeHold);
    TF.socket(`/ws/events/${cfg.eventId}/seats`, {
      onMessage(message) {
        if (message.type === "seat_update" && Array.isArray(message.seats)) {
          applySeatUpdates(message.seats);
        }
      },
      onReconnect: loadSeatMap,
    });
  } else {
    resumeHold();
  }
})();
