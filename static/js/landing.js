// Landing page behaviour: nav, scroll reveals, hero animation, scroll story, live numbers from the API.
// The hero window is an illustration; the numbers strip is read from /api/dashboard.
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // nav turns solid after scrolling
  const nav = $("#lnav");
  const onScroll = () => nav.classList.toggle("solid", window.scrollY > 30);
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

  // scroll-triggered reveals
  const rv = $$(".rv");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: 0.15 });
    rv.forEach((n) => io.observe(n));
  } else rv.forEach((n) => n.classList.add("in"));

  // hero scene: cycles through four states
  const scene = $("#scene");
  if (scene) {
    let s = 0; const set = (n) => scene.setAttribute("data-s", String(n));
    if (reduce) set(3);
    else { set(0); setInterval(() => { s = (s + 1) % 4; set(s); }, 2600); }
  }
  // the robot's eyes follow the pointer
  const eyes = $("#hero-bot .eyes") || null;
  const botEyes = document.querySelector("#hero-bot use");
  document.addEventListener("pointermove", (e) => {
    if (reduce) return;
    const bot = $("#hero-bot"); if (!bot) return;
    const r = bot.getBoundingClientRect();
    const dx = Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / 400));
    const dy = Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height / 2)) / 400));
    bot.style.translate = (dx * 5) + "px " + (dy * 4) + "px";
  }, { passive: true });
  void eyes; void botEyes;

  // scroll story: the step nearest the middle of the screen drives the sticky visual
  const steps = $$("#steps .st"), panels = $$(".panelv");
  function activate(i) {
    steps.forEach((s, k) => s.classList.toggle("on", k === i));
    panels.forEach((p, k) => p.classList.toggle("on", k === i));
  }
  if ("IntersectionObserver" in window) {
    const so = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) activate(Number(e.target.dataset.i)); }),
      { rootMargin: "-45% 0px -45% 0px" });
    steps.forEach((s) => so.observe(s));
  }

  // live numbers from the real backend (nothing is hard-coded)
  const live = $("#live");
  if (live) {
    fetch("/api/dashboard").then((r) => r.ok ? r.json() : Promise.reject()).then((d) => {
      const items = [[d.repositories, "Repositories"], [d.files_scanned, "Files scanned"], [d.bugs_detected, "Bugs detected"], [d.patches.verified_applied, "Fixes verified"]];
      items.forEach(([v, label]) => {
        const box = document.createElement("div"); box.className = "lstat";
        const n = document.createElement("div"); n.className = "n"; n.textContent = "0";
        const l = document.createElement("div"); l.className = "l"; l.textContent = label; box.append(n, l); live.append(box);
        if (window.APUI) window.APUI.countUp(n, v); else n.textContent = String(v);
      });
      if (!d.repositories) $("#live-note").textContent = "No repositories yet. Upload one to see live numbers here.";
    }).catch(() => { const s = $("#live-note"); if (s) s.textContent = "Live numbers are unavailable right now."; });
  }
  // feature cards: soft spotlight follows the pointer
  $$(".fc").forEach((c) => c.addEventListener("pointermove", (e) => { const r = c.getBoundingClientRect(); c.style.setProperty("--mx", (e.clientX - r.left) + "px"); }, { passive: true }));
})();
