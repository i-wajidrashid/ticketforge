/*
 * TicketForge browser core: shared helpers for every page script.
 *
 * Exposes a single global, window.TF, so page scripts (event.js, account.js,
 * dashboard.js, scan.js) stay small and dependency free. No build step: these
 * files are served as-is by nginx.
 */
(() => {
  "use strict";

  const toastRegion = document.getElementById("toasts");

  /** Show a short, non-blocking message. kind: "info" | "error" | "live". */
  function toast(message, kind = "info", ms = 4500) {
    if (!toastRegion) return;
    const el = document.createElement("div");
    el.className = `toast${kind === "error" ? " is-error" : kind === "live" ? " is-live" : ""}`;
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    el.textContent = message;
    toastRegion.appendChild(el);
    while (toastRegion.children.length > 3) toastRegion.firstElementChild.remove();
    setTimeout(() => el.remove(), ms);
  }

  const formatters = new Map();
  /** Integer cents to a localised price. Whole amounts drop the ".00". */
  function money(cents, currency = "USD") {
    const whole = cents % 100 === 0;
    const key = `${currency}:${whole}`;
    if (!formatters.has(key)) {
      formatters.set(
        key,
        new Intl.NumberFormat("en-US", {
          style: "currency",
          currency,
          minimumFractionDigits: whole ? 0 : 2,
          maximumFractionDigits: whole ? 0 : 2,
        }),
      );
    }
    return formatters.get(key).format(cents / 100);
  }

  function clock(seconds) {
    const s = Math.max(0, Math.round(seconds));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  /** An API error that carries the server's machine-readable code. */
  class ApiError extends Error {
    constructor(message, { status = 0, code = "ERROR", details = {} } = {}) {
      super(message);
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }

  /**
   * JSON fetch against our APIs. Every error response uses one envelope:
   * {"error": {"code", "message", "details"}}, so callers can show `message`.
   */
  async function api(path, { method = "GET", body, token, headers = {}, base = "/api/v1" } = {}) {
    const init = { method, headers: { Accept: "application/json", ...headers } };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    if (token) init.headers.Authorization = `Bearer ${token}`;
    let response;
    try {
      response = await fetch(`${base}${path}`, init);
    } catch {
      throw new ApiError("You appear to be offline. Check your connection and try again.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = data.error || data.detail || {};
      throw new ApiError(err.message || `The request failed (HTTP ${response.status}).`, {
        status: response.status,
        code: err.code,
        details: err.details,
      });
    }
    return data;
  }

  /**
   * A WebSocket that reconnects with exponential backoff and jitter.
   * `onReconnect` runs after every reconnection so pages can refetch state:
   * a missed message must never leave a stale screen.
   */
  function socket(path, { onMessage, onOpen, onReconnect, onStatus } = {}) {
    let ws = null;
    let attempt = 0;
    let closed = false;
    let pingTimer = null;
    let retryTimer = null;
    const scheme = location.protocol === "https:" ? "wss" : "ws";

    function connect() {
      ws = new WebSocket(`${scheme}://${location.host}${path}`);
      ws.addEventListener("open", () => {
        const reconnected = attempt > 0;
        attempt = 0;
        onStatus?.("live");
        onOpen?.();
        if (reconnected) onReconnect?.();
        clearInterval(pingTimer);
        // Keep idle proxies from dropping the connection.
        pingTimer = setInterval(() => ws.readyState === 1 && ws.send('{"type":"ping"}'), 25000);
      });
      ws.addEventListener("message", (event) => {
        try {
          onMessage?.(JSON.parse(event.data));
        } catch (error) {
          console.warn("ignored malformed socket message", error);
        }
      });
      ws.addEventListener("close", (event) => {
        clearInterval(pingTimer);
        onStatus?.("offline");
        // 4403: the server refused us (bad or expired token). Don't hammer it.
        if (closed || event.code === 4403) return;
        const delay = Math.min(15000, 1000 * 2 ** attempt) * (0.75 + Math.random() * 0.5);
        attempt += 1;
        retryTimer = setTimeout(connect, delay);
      });
    }

    connect();
    return {
      close() {
        closed = true;
        clearTimeout(retryTimer);
        clearInterval(pingTimer);
        ws?.close();
      },
    };
  }

  /** sessionStorage that never throws (private mode, blocked storage). */
  const store = {
    get(key) {
      try {
        return JSON.parse(sessionStorage.getItem(key));
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        sessionStorage.setItem(key, JSON.stringify(value));
      } catch {
        /* storage unavailable: state just won't survive a reload */
      }
    },
    remove(key) {
      try {
        sessionStorage.removeItem(key);
      } catch {
        /* ignore */
      }
    },
  };

  function config(id = "tf-config") {
    const el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  // A redirect to "#section" lands below the flash messages at the top of the
  // page, so repeat them as toasts where they will actually be seen.
  if (location.hash) {
    for (const notice of document.querySelectorAll(".flash-region .notice")) {
      toast(notice.textContent.trim(), notice.classList.contains("notice-error") ? "error" : "info", 6000);
    }
  }

  window.TF = { toast, money, clock, api, ApiError, socket, store, config, uuid };
})();
