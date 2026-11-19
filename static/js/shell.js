// Shared visual layer: animated network background, syntax highlighting, count-up numbers, toasts.
// Standalone (does not need AP). Everything is built with createElement and textContent, never from HTML strings.
(function () {
  "use strict";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- animated background: floating particles joined by glowing lines
  function background() {
    const c = document.getElementById("bg-canvas");
    if (!c || !c.getContext) return;
    const ctx = c.getContext("2d");
    let w = 0, h = 0, dpr = 1, pts = [], raf = 0, running = true;
    const mouse = { x: -9999, y: -9999 };
    const COLORS = ["59,130,246", "32,197,232", "120,101,245"];
    function size() {
      dpr = Math.min(window.devicePixelRatio || 1, 2); w = window.innerWidth; h = window.innerHeight;
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.max(24, Math.min(90, Math.round(w * h / 22000)));
      pts = Array.from({ length: n }, () => ({ x: Math.random() * w, y: Math.random() * h, vx: (Math.random() - .5) * .28, vy: (Math.random() - .5) * .28, r: Math.random() * 1.6 + .6, col: COLORS[(Math.random() * 3) | 0] }));
    }
    function frame() {
      ctx.clearRect(0, 0, w, h);
      const L = 135;
      for (const p of pts) {
        if (!reduce) { p.x += p.vx; p.y += p.vy; if (p.x < -10) p.x = w + 10; if (p.x > w + 10) p.x = -10; if (p.y < -10) p.y = h + 10; if (p.y > h + 10) p.y = -10; }
      }
      for (let i = 0; i < pts.length; i++) {
        const a = pts[i];
        for (let j = i + 1; j < pts.length; j++) {
          const b = pts[j], dx = a.x - b.x, dy = a.y - b.y, d = dx * dx + dy * dy;
          if (d < L * L) { const o = (1 - Math.sqrt(d) / L) * .22; ctx.strokeStyle = "rgba(" + a.col + "," + o.toFixed(3) + ")"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
        }
        const mx = a.x - mouse.x, my = a.y - mouse.y, md = mx * mx + my * my;
        if (md < 170 * 170) { const o = (1 - Math.sqrt(md) / 170) * .5; ctx.strokeStyle = "rgba(32,197,232," + o.toFixed(3) + ")"; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke(); }
        ctx.fillStyle = "rgba(" + a.col + ",.85)"; ctx.shadowColor = "rgba(" + a.col + ",1)"; ctx.shadowBlur = 8; ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, 6.2832); ctx.fill(); ctx.shadowBlur = 0;
      }
      if (running && !reduce) raf = requestAnimationFrame(frame);
    }
    size(); frame();
    let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { size(); if (reduce) frame(); }, 150); });
    window.addEventListener("pointermove", (e) => { mouse.x = e.clientX; mouse.y = e.clientY; }, { passive: true });
    document.addEventListener("visibilitychange", () => { running = !document.hidden; if (running && !reduce) { cancelAnimationFrame(raf); frame(); } });
  }

  // ---------- syntax highlighting (union of Python / JS / Java / C keywords; one line at a time)
  const KW = new Set(("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield self " +
    "const let var function new this typeof instanceof null undefined export default switch case do throw catch extends implements interface public private protected static final void int long double float char bool boolean struct enum typedef include define namespace using").split(" "));
  const RX = /(#.*|\/\/.*)|("(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?|`[^`]*`?)|(@[A-Za-z_][\w.]*)|(\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([A-Za-z_]\w*)(?=\s*\()|([A-Za-z_]\w*)/g;
  function tokens(text) {
    const out = []; let last = 0, m; RX.lastIndex = 0;
    while ((m = RX.exec(text))) {
      if (m.index > last) out.push([null, text.slice(last, m.index)]);
      const cls = m[1] ? "tk-c" : m[2] ? "tk-s" : m[3] ? "tk-d" : m[4] ? "tk-n" : m[5] ? (KW.has(m[5]) ? "tk-k" : "tk-f") : (KW.has(m[6]) ? "tk-k" : null);
      out.push([cls, m[0]]); last = m.index + m[0].length;
    }
    if (last < text.length) out.push([null, text.slice(last)]);
    return out;
  }
  function highlightNode(node) {
    if (node.dataset.hl) return; node.dataset.hl = "1";
    const text = node.textContent; if (!text || text.length > 2000) return;
    const frag = document.createDocumentFragment();
    for (const [cls, t] of tokens(text)) { if (cls) { const s = document.createElement("span"); s.className = cls; s.textContent = t; frag.append(s); } else frag.append(document.createTextNode(t)); }
    node.replaceChildren(frag);
  }
  const queue = []; let scheduled = false;
  function pump() {
    scheduled = false; const t0 = performance.now();
    while (queue.length && performance.now() - t0 < 10) highlightNode(queue.shift());
    if (queue.length) { scheduled = true; requestAnimationFrame(pump); }
  }
  const SEL = ".code .ln > span:last-child, .dv-r .dc, .mini-code span, .codeb .cl > span:last-child";
  function scan(root) {
    if (!root.querySelectorAll) return;
    const found = root.matches && root.matches(SEL) ? [root] : [];
    for (const n of root.querySelectorAll(SEL)) found.push(n);
    for (const n of found) if (!n.dataset.hl) queue.push(n);
    if (queue.length && !scheduled) { scheduled = true; requestAnimationFrame(pump); }
  }
  function watch() {
    scan(document.body);
    new MutationObserver((muts) => { for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) scan(n); }).observe(document.body, { childList: true, subtree: true });
  }

  // ---------- count-up numbers: any element with data-count="123"
  function countUp(node, to, ms) {
    to = Number(to); if (!isFinite(to)) return;
    if (reduce || to === 0) { node.textContent = String(to); return; }
    const t0 = performance.now(); ms = ms || 900;
    (function step(now) { const p = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - p, 3); node.textContent = String(Math.round(to * e)); if (p < 1) requestAnimationFrame(step); })(t0);
  }
  function toast(msg, ms) {
    const t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg; document.body.append(t);
    setTimeout(() => { t.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, fill: "forwards" }).onfinish = () => t.remove(); }, ms || 3200);
  }

  window.APUI = { countUp, toast, tokens, highlightNode };
  const start = () => { background(); watch(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
