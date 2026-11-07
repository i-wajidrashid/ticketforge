/*
 * Door scanner. Works with keyboard-wedge handheld scanners (they "type" the
 * code and press Enter) and, where the browser supports BarcodeDetector, with
 * the device camera.
 */
(() => {
  "use strict";

  const TF = window.TF;
  const cfg = TF.config("tf-scan");
  if (!cfg) return;

  const form = document.getElementById("scan-form");
  const input = document.getElementById("scan-input");
  const result = document.getElementById("scan-result");
  const verdict = document.getElementById("scan-verdict");
  const detail = document.getElementById("scan-detail");
  const history = document.getElementById("scan-history");
  const video = document.getElementById("camera");
  const startButton = document.getElementById("camera-start");
  const stopButton = document.getElementById("camera-stop");

  const REPEAT_WINDOW_MS = 3000; // ignore the same code read twice by the camera
  let lastCode = "";
  let lastAt = 0;
  let busy = false;

  // The API returns naive UTC timestamps; read them as UTC, show local time.
  const localTime = (iso) =>
    iso ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }) : "";

  function show(kind, title, text) {
    result.className = `scan-result is-${kind}`;
    verdict.textContent = title;
    detail.textContent = text;
    void result.offsetWidth;
    result.classList.add("flash");
    history.querySelector("[data-empty]")?.remove();
    const item = document.createElement("li");
    const what = document.createElement("strong");
    what.textContent = title;
    const when = document.createElement("span");
    when.className = "muted";
    when.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
    item.append(what, when);
    history.prepend(item);
    while (history.children.length > 20) history.lastElementChild.remove();
  }

  async function check(code, { fromCamera = false } = {}) {
    const token = code.trim();
    if (!token || busy) return;
    if (fromCamera) {
      // The camera sees the same code on many consecutive frames. A person
      // typing or a handheld scanner re-sending it means it, so they always go through.
      const now = Date.now();
      if (token === lastCode && now - lastAt < REPEAT_WINDOW_MS) return;
      lastCode = token;
      lastAt = now;
    }
    busy = true;
    try {
      const data = await TF.api("/scan", { method: "POST", body: { qr_token: token }, token: cfg.token });
      show("valid", "Let in", `Valid ticket. Checked in at ${localTime(data.checked_in_at)}.`);
    } catch (error) {
      if (error.code === "TICKET_ALREADY_USED") {
        show("used", "Already used", `This ticket was first scanned at ${localTime(error.details?.checked_in_at)}.`);
      } else if (error.status === 401) {
        show("invalid", "Signed out", "Reload the page to sign in again.");
      } else {
        show("invalid", "Not valid", error.message);
      }
    } finally {
      busy = false;
      input.value = "";
      input.focus();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    check(input.value);
  });

  /* camera scanning (progressive enhancement) */
  let stream = null;
  let detector = null;

  async function startCamera() {
    try {
      detector = new window.BarcodeDetector({ formats: ["qr_code"] });
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    } catch {
      TF.toast("The camera isn’t available. Allow camera access, or use a handheld scanner.", "error");
      return;
    }
    video.srcObject = stream;
    video.hidden = false;
    startButton.hidden = true;
    stopButton.hidden = false;
    await video.play();
    const loop = async () => {
      if (!stream) return;
      try {
        const codes = await detector.detect(video);
        if (codes[0]?.rawValue) await check(codes[0].rawValue, { fromCamera: true });
      } catch {
        /* a frame that couldn't be read; try the next one */
      }
      setTimeout(loop, 250);
    };
    loop();
  }

  function stopCamera() {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    video.hidden = true;
    stopButton.hidden = true;
    startButton.hidden = false;
  }

  if ("BarcodeDetector" in window && navigator.mediaDevices?.getUserMedia) {
    startButton.hidden = false;
    startButton.addEventListener("click", startCamera);
    stopButton.addEventListener("click", stopCamera);
  }
})();
