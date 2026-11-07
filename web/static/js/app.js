/* TicketForge browser client: seat map, holds, payment and live updates. */
(() => {
  "use strict";

  const toastEl = document.getElementById("toast");
  let toastTimer = null;

  function toast(message, isError = false) {
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.classList.toggle("error", isError);
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 4200);
  }

  function money(cents, currency = "USD") {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100);
  }

  function wsUrl(path) {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${location.host}${path}`;
  }

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (window.TF_TOKEN) headers.Authorization = `Bearer ${window.TF_TOKEN}`;
    const response = await fetch(`/api/v1${path}`, { ...options, headers });
    if (response.status === 401) {
      location.href = `/accounts/login/?next=${encodeURIComponent(location.pathname)}`;
      throw new Error("Please log in.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = data?.error?.message || data?.detail?.message || `Request failed (${response.status})`;
      throw new Error(message);
    }
    return data;
  }

  const configEl = document.getElementById("tf-config");
  if (!configEl) return;
  const cfg = JSON.parse(configEl.textContent);
  window.TF_TOKEN = cfg.token || "";

  const state = { selected: new Set(), hold: null, timer: null, seatSocket: null, holdSocket: null };
  const buySeatsButton = document.getElementById("buy-seats");
  const holdPanel = document.getElementById("hold-panel");
  const holdTimer = document.getElementById("hold-timer");
  const holdItems = document.getElementById("hold-items");
  const holdTotal = document.getElementById("hold-total");
  const payButton = document.getElementById("pay-button");

  /* ------------------------------------------------------------------ seats */

  function seatButton(seat) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `seat seat-${seat.status}`;
    button.textContent = seat.number;
    button.title = `${seat.row}${seat.number} — ${seat.status}`;
    button.dataset.seatId = String(seat.seat_id);
    if (seat.status !== "available" || !cfg.verified) {
      button.disabled = true;
    } else {
      button.addEventListener("click", () => {
        if (state.selected.has(seat.seat_id)) {
          state.selected.delete(seat.seat_id);
          button.classList.remove("seat-selected");
          button.classList.add("seat-available");
        } else {
          state.selected.add(seat.seat_id);
          button.classList.remove("seat-available");
          button.classList.add("seat-selected");
        }
        updateBuyButton();
      });
    }
    return button;
  }

  function updateBuyButton() {
    if (!buySeatsButton) return;
    const count = state.selected.size;
    buySeatsButton.disabled = count === 0;
    buySeatsButton.textContent = count ? `Hold ${count} selected seat${count === 1 ? "" : "s"}` : "Hold selected seats";
  }

  async function loadSeatMap() {
    const container = document.getElementById("seat-map");
    if (!container) return;
    try {
      const data = await api(`/events/${cfg.slug}/seats`);
      container.innerHTML = "";
      data.sections.forEach((section) => {
        const wrapper = document.createElement("div");
        wrapper.className = "seat-section";
        const title = document.createElement("h3");
        title.textContent = section.name;
        wrapper.appendChild(title);

        const rows = new Map();
        section.seats.forEach((seat) => {
          if (!rows.has(seat.row)) rows.set(seat.row, []);
          rows.get(seat.row).push(seat);
        });
        [...rows.keys()].sort().forEach((row) => {
          const rowEl = document.createElement("div");
          rowEl.className = "seat-row";
          const label = document.createElement("span");
          label.className = "row-label";
          label.textContent = row;
          rowEl.appendChild(label);
          rows.get(row)
            .sort((a, b) => String(a.number).localeCompare(String(b.number), undefined, { numeric: true }))
            .forEach((seat) => rowEl.appendChild(seatButton(seat)));
          wrapper.appendChild(rowEl);
        });
        container.appendChild(wrapper);
      });
    } catch (error) {
      container.innerHTML = "";
      const message = document.createElement("p");
      message.className = "muted";
      message.textContent = "Seat map unavailable right now.";
      container.appendChild(message);
      toast(error.message, true);
    }
  }

  function applySeatUpdates(seats) {
    seats.forEach((seat) => {
      const el = document.querySelector(`.seat[data-seat-id="${seat.seat_id}"]`);
      if (!el) return;
      el.classList.remove("seat-available", "seat-selected", "seat-held", "seat-sold");
      el.classList.add(`seat-${seat.status}`);
      el.disabled = seat.status !== "available" || !cfg.verified;
      if (seat.status !== "available") state.selected.delete(seat.seat_id);
    });
    updateBuyButton();
  }

  function connectSeatSocket() {
    try {
      state.seatSocket = new WebSocket(wsUrl(`/ws/events/${cfg.eventId}/seats`));
      state.seatSocket.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.type === "seat_update" && Array.isArray(payload.seats)) {
          applySeatUpdates(payload.seats);
        }
      };
      state.seatSocket.onclose = () => setTimeout(connectSeatSocket, 3000);
    } catch (error) {
      console.warn("seat socket unavailable", error);
    }
  }

  /* ------------------------------------------------------------------ holds */

  function startHoldTimer() {
    clearInterval(state.timer);
    state.timer = setInterval(() => {
      if (!state.hold) return;
      const expires = new Date(`${state.hold.expires_at.replace("Z", "")}Z`).getTime();
      const secondsLeft = Math.max(0, Math.round((expires - Date.now()) / 1000));
      const minutes = String(Math.floor(secondsLeft / 60)).padStart(2, "0");
      const seconds = String(secondsLeft % 60).padStart(2, "0");
      holdTimer.textContent = `${minutes}:${seconds}`;
      if (secondsLeft <= 0) {
        clearInterval(state.timer);
        holdTimer.textContent = "expired";
        payButton.disabled = true;
        toast("Your hold expired — the seats are back on sale.", true);
      }
    }, 250);
  }

  function connectHoldSocket() {
    if (state.holdSocket) state.holdSocket.close();
    try {
      state.holdSocket = new WebSocket(wsUrl(`/ws/holds/${state.hold.id}?token=${encodeURIComponent(window.TF_TOKEN)}`));
      state.holdSocket.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.type === "hold_tick") {
          const minutes = String(Math.floor(payload.seconds_left / 60)).padStart(2, "0");
          const seconds = String(payload.seconds_left % 60).padStart(2, "0");
          holdTimer.textContent = `${minutes}:${seconds}`;
        }
        if (payload.type === "hold_expired") {
          clearInterval(state.timer);
          payButton.disabled = true;
          toast("Your hold expired — the seats are back on sale.", true);
          loadSeatMap();
        }
      };
    } catch (error) {
      console.warn("hold socket unavailable", error);
    }
  }

  function showHold(hold) {
    state.hold = hold;
    holdPanel.hidden = false;
    holdItems.innerHTML = "";
    hold.items.forEach((item) => {
      const li = document.createElement("li");
      const label = item.seat_label || "General admission";
      li.textContent = `${label} — ${money(cfg.ticketTypes.find((t) => t.id === item.ticket_type_id)?.price_cents || 0)}`;
      holdItems.appendChild(li);
    });
    holdTotal.textContent = money(hold.total_cents);
    payButton.disabled = false;
    startHoldTimer();
    connectHoldSocket();
    holdPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function createHold(payload) {
    try {
      const hold = await api("/holds", { method: "POST", body: JSON.stringify(payload) });
      showHold(hold);
      loadSeatMap();
      toast("Seats held for 10 minutes.");
    } catch (error) {
      toast(error.message, true);
      loadSeatMap();
    }
  }

  if (buySeatsButton) {
    buySeatsButton.addEventListener("click", () => {
      if (!cfg.token) {
        location.href = `/accounts/login/?next=${encodeURIComponent(location.pathname)}`;
        return;
      }
      const seatType = document.getElementById("seat-ticket-type");
      const payload = { event_id: cfg.eventId, seat_ids: [...state.selected] };
      if (seatType) payload.ticket_type_id = Number(seatType.value);
      createHold(payload);
    });
  }

  document.querySelectorAll(".add-ga").forEach((button) => {
    button.addEventListener("click", () => {
      if (!cfg.token) {
        location.href = `/accounts/login/?next=${encodeURIComponent(location.pathname)}`;
        return;
      }
      createHold({
        event_id: cfg.eventId,
        ticket_type_id: Number(button.dataset.ticketType),
        quantity: 1,
      });
    });
  });

  if (payButton) {
    payButton.addEventListener("click", async () => {
      if (!state.hold) return;
      payButton.disabled = true;
      payButton.textContent = "Processing…";
      try {
        const idempotencyKey = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
        const order = await api(`/checkout/${state.hold.id}/pay`, {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
        });
        try {
          // Local demo: the fake gateway has no webhook sender, so confirm it here.
          await api(`/dev/simulate-payment/${order.order_id}`, { method: "POST" });
          toast("Payment complete. Your tickets are ready.");
          setTimeout(() => (location.href = "/accounts/"), 1200);
        } catch (error) {
          toast("Payment intent created. Complete it with Stripe.js.", false);
          console.info("client_secret", order.client_secret);
          payButton.textContent = "Awaiting Stripe";
        }
      } catch (error) {
        toast(error.message, true);
        payButton.disabled = false;
        payButton.textContent = "Pay now";
      }
    });
  }

  if (cfg.seated) {
    loadSeatMap();
    connectSeatSocket();
  }
})();
