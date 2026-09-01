/* Kitchen ding for NEW drink lines since last poll. First load is silent. */
(function () {
  const seen = new Set();
  let primed = false;
  let audioCtx = null;

  function lineKeys() {
    return Array.from(document.querySelectorAll("#ticket-list [data-kds-line]")).map(
      (el) => el.getAttribute("data-kds-line") || ""
    ).filter(Boolean);
  }

  function unlock() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!audioCtx) audioCtx = new AC();
    if (audioCtx.state === "suspended") audioCtx.resume();
  }

  function ding() {
    unlock();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(1318.51, now);
    osc.frequency.setValueAtTime(1046.5, now + 0.07);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.14, now + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.28);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(now);
    osc.stop(now + 0.3);
  }

  function scan(allowDing) {
    const keys = lineKeys();
    if (!primed) {
      keys.forEach((k) => seen.add(k));
      primed = true;
      return;
    }
    let fresh = false;
    keys.forEach((k) => {
      if (!seen.has(k)) {
        seen.add(k);
        fresh = true;
      }
    });
    if (allowDing && fresh) ding();
  }

  document.addEventListener("pointerdown", unlock);
  document.addEventListener("DOMContentLoaded", function () {
    scan(false);
  });
  document.body.addEventListener("htmx:afterSwap", function (ev) {
    const target = ev.detail && ev.detail.target;
    if (!target || target.id !== "ticket-list") return;
    scan(true);
  });
})();
