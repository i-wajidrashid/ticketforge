/* My tickets: refund requests, sent to the checkout API without a reload. */
(() => {
  "use strict";

  const TF = window.TF;
  const cfg = TF.config("tf-account");
  if (!cfg) return;

  document.querySelectorAll(".order-card").forEach((card) => {
    const toggle = card.querySelector("[data-refund-toggle]");
    const form = card.querySelector("[data-refund-form]");
    if (!toggle || !form) return;

    const setOpen = (open) => {
      form.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      if (open) form.querySelector("textarea")?.focus();
    };
    toggle.addEventListener("click", () => setOpen(form.hidden));
    form.querySelector("[data-refund-cancel]").addEventListener("click", () => setOpen(false));

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector('[type="submit"]');
      submit.disabled = true;
      try {
        await TF.api(`/me/orders/${card.dataset.order}/refund-request`, {
          method: "POST",
          token: cfg.token,
          body: { reason: form.reason.value.trim() },
        });
        form.remove();
        toggle.remove();
        card.querySelector("[data-order-status]").innerHTML = '<span class="tag tag-warn">Refund requested</span>';
        TF.toast("Refund requested. We’ll email you when the organizer decides.");
      } catch (error) {
        submit.disabled = false;
        TF.toast(error.message, "error");
      }
    });
  });
})();
