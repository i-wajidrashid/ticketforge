/*
 * Organizer event dashboard: a live sales counter fed by
 * ws/org/events/{id}/sales, plus the chart's hover tooltip.
 */
(() => {
  "use strict";

  const TF = window.TF;
  const cfg = TF.config("tf-dash");
  if (!cfg) return;

  const counts = { tickets: cfg.ticketsSold, revenue: cfg.revenueCents };
  const kpi = (name) => document.querySelector(`[data-kpi="${name}"]`);
  const status = document.getElementById("live-status");
  const activity = document.getElementById("activity");

  function bump(name, text) {
    const card = kpi(name);
    if (!card) return;
    card.querySelector("[data-value]").textContent = text;
    card.classList.remove("is-bumped");
    void card.offsetWidth; // restart the animation
    card.classList.add("is-bumped");
  }

  function onSale(message) {
    counts.tickets += message.tickets;
    counts.revenue += message.revenue_cents;
    bump("tickets", String(counts.tickets));
    bump("revenue", TF.money(counts.revenue));
    bump("remaining", String(Math.max(0, cfg.capacity - counts.tickets)));

    activity.querySelector("[data-empty]")?.remove();
    const item = document.createElement("li");
    item.className = "is-new";
    const what = document.createElement("span");
    what.textContent = `${message.tickets} ticket${message.tickets === 1 ? "" : "s"}, ${TF.money(message.revenue_cents)}`;
    const when = document.createElement("span");
    when.className = "muted";
    when.textContent = "just now";
    item.append(what, when);
    activity.prepend(item);
    while (activity.children.length > 12) activity.lastElementChild.remove();
  }

  TF.socket(`/ws/org/events/${cfg.eventId}/sales?token=${encodeURIComponent(cfg.token)}`, {
    onMessage(message) {
      if (message.type === "sale") onSale(message);
    },
    onStatus(state) {
      status.classList.toggle("is-live", state === "live");
      status.textContent = state === "live" ? "Live" : "Reconnecting";
    },
    // Counts may have moved while we were offline: reload for exact figures.
    onReconnect: () => location.reload(),
  });

  /* chart tooltip: one shared element, positioned over the hovered day */
  const chart = document.getElementById("sales-chart");
  const tip = document.getElementById("chart-tip");
  if (chart && tip) {
    chart.addEventListener("pointermove", (event) => {
      const hit = event.target.closest?.(".chart-hit");
      if (!hit) return void (tip.hidden = true);
      const box = hit.getBoundingClientRect();
      const frame = chart.parentElement.getBoundingClientRect();
      tip.textContent = hit.dataset.tip;
      tip.style.left = `${box.left - frame.left + box.width / 2}px`;
      tip.style.top = `${event.clientY - frame.top}px`;
      tip.hidden = false;
    });
    chart.addEventListener("pointerleave", () => (tip.hidden = true));
  }
})();
