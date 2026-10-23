/* SkySift dossier: the charts, the event gallery, the per-season S/N maps and the "How it might look" panels, drawn
   from the JSON in #dossier-data with the dashboard's colour tokens (--fg, --fg-2, --muted, --ion, --solar, --line,
   --space). Ported from the hand-built KIC 6146676 page (docs/candidates/kic-006146676/dossier_template.html).

   Two hosts. On the dashboard the page is rendered by the server and every figure is an empty [data-chart] box that
   this script fills. In the standalone export (dossier.export_html) <main id="dossier"> arrives empty: the script
   first writes the eight sections from the JSON, with their own Normal / Expert toggle, then fills the same boxes.
   Text goes in through textContent only. */
(() => {
  "use strict";
  const holder = document.getElementById("dossier-data");
  if (!holder) return;
  let D;
  try {
    D = JSON.parse(holder.textContent);
  } catch (err) {
    // a damaged data block: say so where every figure would be (or in the empty standalone page), draw nothing
    const say = (box, what) => {
      const p = document.createElement("p");
      p.className = "dossier-notice"; p.setAttribute("role", "note");
      p.textContent = `The dossier's data could not be read, so ${what} is not drawn. Generate the dossier again.`;
      box.replaceChildren(p);
    };
    document.querySelectorAll("[data-chart]").forEach((host) => say(host, "this figure"));
    const standalone = document.getElementById("dossier");
    if (standalone && !standalone.childElementCount) say(standalone, "the page");
    if (window.console) console.error(err);
    return;
  }
  const root = document.documentElement;
  const NS = "http://www.w3.org/2000/svg";
  const P = D.plots || {};
  const E = D.ephemeris || {};

  // ---- small helpers
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  const fmt = (x, d = 0) => (isNum(x) ? x.toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }) : "–");
  const pm = (v, e, d = 0) => `${fmt(v, d)} ± ${fmt(e, d)}`;
  // an error as " ± e", or " +p −m" when its two sides differ at these decimals: a number or {minus, plus}
  const pmErr = (err, d = 2) => {
    if (isNum(err)) return ` ± ${fmt(err, d)}`;
    if (!err || !isNum(err.minus) || !isNum(err.plus)) return "";
    const p = fmt(err.plus, d), m = fmt(err.minus, d);
    return p === m ? ` ± ${p}` : ` +${p} −${m}`;
  };
  // the decimals that print an error to two significant figures, never more than it carries:
  // skysift.pack_text.pm_digits, the one precision rule of the page (its Jinja helper err_digits) and the pack
  // what the shape table's columns mean (the dashboard prints the same words)
  // the shape block's a/R* names the fit it comes from (pack_rows.SHAPE_A_OVER_R_NAMES)
  const AR_FIT = { prior: " (density-prior fit)", free: " (free fit)" };
  const SHAPE_WORDS = "For the fitted rows (k, b, a/R*, T14 and the model depth) the value is the median of the resamples, minus and plus the 16th to 50th and 50th to 84th percentile spread about it, and the full-data fit stands beside it. The box depth carries the per-event scaled error, and the box duration's value is the median of the resamples.";
  // the decimals a stored number carries (0.1 has 1, 9.1e-05 has 6, 5 has none): Python's _places
  const places = (x) => {
    const [mant, exp] = String(x).toLowerCase().split("e");
    const dec = mant.includes(".") ? mant.split(".")[1].length : 0;
    return Math.max(0, dec - (exp ? parseInt(exp, 10) : 0));
  };
  const errDigits = (err) => (isNum(err) && err > 0 ? Math.min(Math.max(0, 1 - Math.floor(Math.log10(err))), places(err)) : 2);
  const el = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };
  const h = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  };
  const text = (parent, attrs, words) => { el("text", attrs, parent).textContent = words; };
  const metrics = (rung) => ((D.tests || []).find((t) => t.rung === rung) || {}).metrics || {};
  const extent = (values, pad = 0.08) => {
    const v = values.filter(isNum);
    if (!v.length) return [-1, 1];
    let lo = Math.min(...v), hi = Math.max(...v);
    if (lo === hi) { lo -= 1; hi += 1; }
    const span = hi - lo;
    return [lo - span * pad, hi + span * pad];
  };

  // ---- the chart kit (niceTicks, chart, hover, errbar, dot, path) from the hand-built page
  function niceTicks(lo, hi, n = 5) {
    const span = hi - lo, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw)), r = raw / mag;
    const step = (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * mag, out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const kfmt = (v) => (Math.abs(v) >= 1000 ? `${(v / 1000).toLocaleString("en-GB", { maximumFractionDigits: 1 })}k` : fmt(v));
  function chart(host, o) {
    const w = o.w || 760, hh = o.h || 340, m = Object.assign({ l: 58, r: 18, t: 14, b: 44 }, o.m || {});
    const [x0, x1] = o.x.dom, [y0, y1] = o.y.dom;
    const xlog = !!o.x.log, fx = xlog ? Math.log10 : (v) => v;
    const sx = (v) => m.l + (fx(v) - fx(x0)) / (fx(x1) - fx(x0)) * (w - m.l - m.r);
    const sy = (v) => hh - m.b - (v - y0) / (y1 - y0) * (hh - m.t - m.b);
    const svg = el("svg", { viewBox: `0 0 ${w} ${hh}`, class: "chart", role: "img", "aria-label": o.aria || "" });
    const grid = el("g", { class: "grid" }, svg);
    (o.y.ticks || niceTicks(y0, y1, o.y.n || 5)).forEach((v) => {
      el("line", { x1: m.l, x2: w - m.r, y1: sy(v), y2: sy(v) }, grid);
      text(svg, { x: m.l - 8, y: sy(v) + 4, "text-anchor": "end", class: "axis" }, (o.y.fmt || kfmt)(v));
    });
    const xt = o.x.ticks || (xlog ? [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500].filter((v) => v >= x0 && v <= x1)
      : niceTicks(x0, x1, o.x.n || 6));
    xt.forEach((v) => text(svg, { x: sx(v), y: hh - m.b + 18, "text-anchor": "middle", class: "axis" }, (o.x.fmt || fmt)(v)));
    el("line", { x1: m.l, x2: w - m.r, y1: sy(y0), y2: sy(y0), class: "baseline" }, svg);
    if (y0 < 0 && y1 > 0) el("line", { x1: m.l, x2: w - m.r, y1: sy(0), y2: sy(0), class: "baseline" }, svg);
    if (o.x.label) text(svg, { x: (m.l + w - m.r) / 2, y: hh - 6, "text-anchor": "middle", class: "axis-title" }, o.x.label);
    if (o.y.label) {
      const cy = (m.t + hh - m.b) / 2;
      text(svg, { x: 14, y: cy, "text-anchor": "middle", class: "axis-title", transform: `rotate(-90 14 ${cy})` }, o.y.label);
    }
    const g = el("g", {}, svg);
    host.appendChild(svg);
    return { svg, g, sx, sy, w, h: hh, m, x0, x1, y0, y1 };
  }
  function tipFor(host) {
    let tip = host.querySelector(":scope > .dossier-tip");
    if (!tip) { tip = h("div", "dossier-tip"); tip.setAttribute("aria-hidden", "true"); host.appendChild(tip); }
    return tip;
  }
  function hover(c, host, pts, lines, mode = "near") {
    if (!pts.length) return;
    const tip = tipFor(host);
    const ov = el("rect", { x: c.m.l, y: c.m.t, width: c.w - c.m.l - c.m.r, height: c.h - c.m.t - c.m.b, fill: "transparent" }, c.svg);
    const cross = el("line", { y1: c.m.t, y2: c.h - c.m.b, class: "hover-line", opacity: 0 }, c.svg);
    const ring = el("circle", { r: 7, class: "hover-ring", opacity: 0 }, c.svg);
    const out = () => { tip.style.opacity = 0; cross.setAttribute("opacity", 0); ring.setAttribute("opacity", 0); };
    ov.addEventListener("pointermove", (ev) => {
      const r = c.svg.getBoundingClientRect(), k = c.w / r.width;
      const px = (ev.clientX - r.left) * k, py = (ev.clientY - r.top) * k;
      let best = null, bd = Infinity;
      for (const p of pts) {
        const dx = c.sx(p.x) - px, dy = mode === "x" ? 0 : c.sy(p.y) - py, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = p; }
      }
      if (!best || bd > (mode === "x" ? 1e9 : 40 * 40)) { out(); return; }
      cross.setAttribute("x1", c.sx(best.x)); cross.setAttribute("x2", c.sx(best.x)); cross.setAttribute("opacity", 0.8);
      ring.setAttribute("cx", c.sx(best.x)); ring.setAttribute("cy", c.sy(best.y)); ring.setAttribute("opacity", 1);
      tip.replaceChildren(...lines(best).map((line, i) => h(i ? "span" : "b", null, line)));
      const hr = host.getBoundingClientRect();
      tip.style.left = `${c.sx(best.x) / k + r.left - hr.left}px`;
      tip.style.top = `${c.sy(best.y) / k + r.top - hr.top}px`;
      tip.style.opacity = 1;
    });
    ov.addEventListener("pointerleave", out);
  }
  const errbar = (c, g, x, y, e, col) => {
    if (!isNum(e)) return;
    el("line", { x1: c.sx(x), x2: c.sx(x), y1: c.sy(y - e), y2: c.sy(y + e), stroke: col, "stroke-width": 1.5, "stroke-linecap": "round" }, g);
  };
  const dot = (c, g, x, y, col, r = 4) => {
    el("circle", { cx: c.sx(x), cy: c.sy(y), r: r + 2, fill: "var(--surface)" }, g);
    el("circle", { cx: c.sx(x), cy: c.sy(y), r, fill: col }, g);
  };
  const path = (c, g, pts, col, w = 2, extra = {}) => el("path", Object.assign({
    d: pts.map((p, i) => `${i ? "L" : "M"}${c.sx(p[0]).toFixed(1)} ${c.sy(p[1]).toFixed(1)}`).join(""),
    fill: "none", stroke: col, "stroke-width": w, "stroke-linejoin": "round", "stroke-linecap": "round" }, extra), g);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const vline = (c, x, cls) => el("line", { x1: c.sx(x), x2: c.sx(x), y1: c.m.t, y2: c.h - c.m.b, class: cls }, c.g);

  // ---- the charts, by data-chart name
  const win = P.window_h || 12;
  const foldRange = () => {
    const ys = [...(P.fold_bins || []).map((b) => b[1] - (b[2] || 0)), ...(P.model || []).map((p) => p[1])];
    const lo = Math.min(-100, ...ys.filter(isNum)) * 1.35;
    return [lo, Math.max(-lo * 0.4, ...(P.fold_bins || []).map((b) => b[1] + (b[2] || 0)).filter(isNum))];
  };
  const DRAW = {
    fold(host) {
      const [lo, hi] = foldRange();
      const c = chart(host, { h: 360, x: { dom: [-win, win], label: "hours from mid-transit", n: 8 },
        y: { dom: [lo, hi], label: "relative flux, ppm" }, aria: `Folded light curve on ${fmt(E.period_days, 4)} days, with its bins and the transit model` });
      (P.fold_points || []).forEach(([x, y]) => { if (y > lo && y < hi) el("circle", { cx: c.sx(x), cy: c.sy(y), r: 1.5, class: "pt" }, c.g); });
      if ((P.model || []).length) path(c, c.g, P.model, "var(--solar)", 2.5);
      (P.fold_bins || []).forEach(([x, y, e]) => { errbar(c, c.g, x, y, e, "var(--ion)"); dot(c, c.g, x, y, "var(--ion)", 3.5); });
      hover(c, host, (P.fold_bins || []).map(([x, y, e, n]) => ({ x, y, e, n })),
        (p) => [`${fmt(p.x, 2)} h`, `${pm(p.y, p.e)} ppm`, `${fmt(p.n)} cadences`]);
    },
    spectrum(host) {
      const S = (P.spectrum || []).filter((p) => isNum(p[0]) && isNum(p[1]) && p[0] > 0);
      if (!S.length) { host.appendChild(h("p", "dossier-notice", "No stored periodogram for this signal.")); return; }
      const x0 = Math.min(...S.map((p) => p[0])), x1 = Math.max(...S.map((p) => p[0]));
      const c = chart(host, { h: 300, x: { dom: [x0, x1], log: true, label: "trial period, days" },
        y: { dom: [0, Math.max(...S.map((p) => p[1])) * 1.12], label: "SDE" }, aria: "Detection spectrum: SDE against trial period" });
      const L = P.spectrum_lines || {};
      (L.systematic_periods || []).filter((v) => v >= x0 && v <= x1).forEach((v) => vline(c, v, "mark-muted"));
      (L.aliases || []).filter((v) => v >= x0 && v <= x1).forEach((v) => vline(c, v, "mark-solar dashed"));
      if (isNum(E.period_days)) vline(c, E.period_days, "mark-solar");
      path(c, c.g, S, "var(--ion)", 1.4);
      hover(c, host, S.map(([x, y]) => ({ x, y })), (p) => [`${fmt(p.x, 3)} d`, `SDE ${fmt(p.y, 1)}`], "x");
    },
    events(host) {
      const ev = (P.events || []).filter((e) => isNum(e.t_mid) && isNum(e.depth_ppm));
      if (!ev.length) { host.appendChild(h("p", "dossier-notice", "No transit could be measured on its own.")); return; }
      const [t0, t1] = extent(ev.map((e) => e.t_mid), 0.04);
      const top = Math.max(...ev.map((e) => e.depth_ppm + (e.err_ppm || 0)));
      const c = chart(host, { h: 320, x: { dom: [t0, t1], label: "time, BKJD (days since 2009 January 1)", n: 8 },
        y: { dom: [Math.min(0, ...ev.map((e) => e.depth_ppm - (e.err_ppm || 0))), top * 1.15], label: "single-transit depth, ppm" },
        aria: `Depth of each of ${ev.length} transits against time` });
      (P.quarters || []).forEach((q, i) => {
        if (q.t_end < t0 || q.t_start > t1) return;
        const a = c.sx(clamp(q.t_start, t0, t1)), b = c.sx(clamp(q.t_end, t0, t1));
        if (i % 2) el("rect", { x: a, y: c.m.t, width: Math.max(0, b - a), height: c.h - c.m.t - c.m.b, class: "band" }, c.g);
        if (b - a > 24) text(c.g, { x: (a + b) / 2, y: c.m.t + 12, "text-anchor": "middle", class: "axis" }, `Q${q.q}`);
      });
      const w = ev.map((e) => (isNum(e.err_ppm) && e.err_ppm > 0 ? 1 / e.err_ppm ** 2 : 1));
      const mean = ev.reduce((s, e, i) => s + e.depth_ppm * w[i], 0) / w.reduce((s, x) => s + x, 0);
      el("line", { x1: c.m.l, x2: c.w - c.m.r, y1: c.sy(mean), y2: c.sy(mean), stroke: "var(--solar)", "stroke-width": 1.5 }, c.g);
      text(c.g, { x: c.w - c.m.r - 4, y: c.sy(mean) - 7, "text-anchor": "end", class: "lab strong" }, `mean ${fmt(mean)} ppm`);
      ev.forEach((e) => { errbar(c, c.g, e.t_mid, e.depth_ppm, e.err_ppm, "var(--ion)"); dot(c, c.g, e.t_mid, e.depth_ppm, "var(--ion)", 3.5); });
      hover(c, host, ev.map((e) => ({ x: e.t_mid, y: e.depth_ppm, e })),
        (p) => [`epoch ${p.e.epoch} · Q${p.e.quarter}`, `BKJD ${fmt(p.e.t_mid, 2)}`, `${pm(p.e.depth_ppm, p.e.err_ppm)} ppm`]);
    },
    gallery(host) {
      const G = P.event_gallery || [];
      if (!G.length) { host.appendChild(h("p", "dossier-notice", "No transit has data close enough to draw.")); return; }
      const half = P.gallery_half_hours || 8, depth = Math.abs(E.depth_ppm || 1000);
      const lo = -2.2 * depth, hi = 1.0 * depth, w = 150, hh = 90;
      const x = (v) => (v + half) / (2 * half) * (w - 8) + 4, y = (v) => 6 + (hi - clamp(v, lo, hi)) / (hi - lo) * (hh - 12);
      const grid = h("div", "gallery");
      G.forEach((ev) => {
        const box = h("figure", "gallery-cell");
        const svg = el("svg", { viewBox: `0 0 ${w} ${hh}`, role: "img",
          "aria-label": `Transit ${ev.epoch}, quarter ${ev.quarter}: depth ${pm(ev.depth_ppm, ev.err_ppm)} ppm` });
        el("line", { x1: 4, x2: w - 4, y1: y(0), y2: y(0), class: "baseline" }, svg);
        if ((P.model || []).length) el("path", { d: P.model.filter((p) => Math.abs(p[0]) <= half).map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(""), fill: "none", stroke: "var(--solar)", "stroke-width": 1.5 }, svg);
        (ev.pts || []).forEach(([a, b]) => { if (isNum(a) && isNum(b)) el("circle", { cx: x(a), cy: y(b), r: 1.6, fill: "var(--ion)" }, svg); });
        const cap = h("figcaption", "gallery-cap");
        cap.append(h("span", null, `#${ev.epoch} Q${ev.quarter}`), h("span", null, `${fmt(ev.depth_ppm)} ± ${fmt(ev.err_ppm)}`));
        box.append(svg, cap);
        grid.appendChild(box);
      });
      host.appendChild(grid);
    },
    oddeven(host) {
      const odd = P.odd_bins || [], even = P.even_bins || [];
      const [lo, hi] = foldRange();
      const c = chart(host, { w: 420, h: 300, m: { l: 50, r: 12, t: 14, b: 40 }, x: { dom: [-win, win], label: "hours from mid-transit", n: 6 },
        y: { dom: [lo, hi], label: "ppm" }, aria: "Odd and even transits folded apart" });
      const dx = win / 120;
      const drawSet = (bins, col, off) => bins.forEach(([bx, by, e]) => { errbar(c, c.g, bx + off, by, e, col); dot(c, c.g, bx + off, by, col, 3); });
      drawSet(odd, "var(--ion)", -dx); drawSet(even, "var(--solar)", dx);
      const r2 = metrics("rung2_shape");
      if (isNum(r2.odd_ppm) && isNum(r2.even_ppm)) {
        text(c.g, { x: c.m.l + 6, y: c.m.t + 14, class: "lab" }, `odd ${fmt(r2.odd_ppm)} · even ${fmt(r2.even_ppm)} ppm${isNum(r2.parity_sigma) ? ` · ${fmt(r2.parity_sigma, 1)} sigma apart` : ""}`);
      }
      hover(c, host, [...odd.map(([bx, by, e]) => ({ x: bx - dx, y: by, e, w: "odd" })), ...even.map(([bx, by, e]) => ({ x: bx + dx, y: by, e, w: "even" }))],
        (p) => [`${p.w} · ${fmt(p.x, 1)} h`, `${pm(p.y, p.e)} ppm`]);
    },
    seasons(host) {
      const m = metrics("rung3_stability"), depths = m.season_depths || {}, errs = m.season_errs || {};
      const rows = Object.keys(depths).map((s) => ({ x: +s, y: depths[s], e: errs[s] })).filter((p) => isNum(p.y));
      if (!rows.length) { host.appendChild(h("p", "dossier-notice", "The depth was not measured by season.")); return; }
      const [lo, hi] = extent(rows.flatMap((p) => [p.y - (p.e || 0), p.y + (p.e || 0)]), 0.3);
      const c = chart(host, { w: 420, h: 300, m: { l: 54, r: 12, t: 14, b: 40 }, x: { dom: [-0.6, 3.6], label: "Kepler season", ticks: [0, 1, 2, 3] },
        y: { dom: [lo, hi], label: "depth, ppm" }, aria: "Transit depth in each Kepler season" });
      const mean = rows.reduce((s, p) => s + p.y, 0) / rows.length;
      el("line", { x1: c.m.l, x2: c.w - c.m.r, y1: c.sy(mean), y2: c.sy(mean), stroke: "var(--solar)", "stroke-width": 1.5 }, c.g);
      rows.forEach((p) => { errbar(c, c.g, p.x, p.y, p.e, "var(--ion)"); dot(c, c.g, p.x, p.y, "var(--ion)", 4.5); });
      hover(c, host, rows, (p) => [`season ${p.x}`, `${pm(p.y, p.e)} ppm`]);
    },
    series(host) {
      const S = (P.series_binned || []).filter((p) => isNum(p[0]) && isNum(p[1]));
      if (!S.length) return;
      const ys = S.map((p) => p[1]).sort((a, b) => a - b);
      const q = (f) => ys[Math.min(ys.length - 1, Math.floor(f * ys.length))];
      const lo = Math.min(q(0.005), -Math.abs(E.depth_ppm || 0)) * 1.2, hi = Math.max(q(0.995), 100) * 1.2;
      const [t0, t1] = extent(S.map((p) => p[0]), 0.01);
      const c = chart(host, { h: 280, x: { dom: [t0, t1], label: "BKJD", n: 8 }, y: { dom: [lo, hi], label: "ppm" }, aria: "The light curve in time bins" });
      S.forEach(([t, y]) => el("circle", { cx: c.sx(t), cy: c.sy(clamp(y, lo, hi)), r: 1.3, class: "pt" }, c.g));
      if (isNum(E.period_days) && isNum(E.t0_bkjd) && E.period_days > 0) {
        for (let t = E.t0_bkjd + Math.ceil((t0 - E.t0_bkjd) / E.period_days) * E.period_days; t <= t1; t += E.period_days) {
          el("line", { x1: c.sx(t), x2: c.sx(t), y1: c.sy(lo), y2: c.sy(lo) + (c.sy(lo * 0.85) - c.sy(lo)), stroke: "var(--solar)", "stroke-width": 2 }, c.g);
        }
      }
      hover(c, host, S.map(([x, y, n]) => ({ x, y: clamp(y, lo, hi), raw: y, n })), (p) => [`BKJD ${fmt(p.x, 2)}`, `${fmt(p.raw)} ppm · ${fmt(p.n)} cadences`], "x");
    },
    pixmaps(host) {
      const PX = (D.pixels || []).filter((s) => Array.isArray(s.snr) && s.snr.length);
      if (!PX.length) return;
      const vmax = Math.max(1, ...PX.flatMap((s) => s.snr.flat().filter(isNum)));
      const wrap = h("div", "pix-grid");
      PX.forEach((s) => {
        const nr = s.snr.length, nc = s.snr[0].length, cs = 28, pad = 16, w = nc * cs + pad * 2, hh = nr * cs + pad * 2;
        const X = (col) => pad + (col + 0.5) * cs, Y = (row) => pad + (nr - 0.5 - row) * cs;    // row 0 at the bottom
        const peak = Math.max(...s.snr.flat().filter(isNum));
        const svg = el("svg", { viewBox: `0 0 ${w} ${hh}`, role: "img",
          "aria-label": `Season ${s.season} difference image, peak S/N ${fmt(peak, 1)}, nearer ${s.nearer === "target" ? "this star" : s.nearer || "neither"}` });
        for (let r = 0; r < nr; r++) {
          for (let cc = 0; cc < nc; cc++) {
            const v = s.snr[r][cc], x = pad + cc * cs + 1, y = pad + (nr - 1 - r) * cs + 1;
            const pct = isNum(v) ? Math.round(clamp(v / vmax, 0, 1) * 100) : 0;
            const fill = isNum(v) ? (v <= 0 ? "var(--surface)" : `color-mix(in srgb, var(--ion) ${pct}%, var(--surface))`) : "var(--bg)";
            const rect = el("rect", { x, y, width: cs - 2, height: cs - 2, rx: 2, fill, class: "pix" }, svg);
            el("title", {}, rect).textContent = isNum(v) ? `S/N ${fmt(v, 1)}` : "no valid pixel";
            if (!isNum(v)) el("line", { x1: x + 5, y1: y + cs - 7, x2: x + cs - 7, y2: y + 5, class: "baseline" }, svg);
          }
        }
        const [tc, tr] = s.pos_target || [], [nc2, nr2] = s.pos_neighbour || [];
        if (isNum(nc2) && isNum(nr2)) el("rect", { x: X(nc2) - 7, y: Y(nr2) - 7, width: 14, height: 14, class: "mark-neighbour" }, svg);
        if (isNum(tc) && isNum(tr)) {
          el("circle", { cx: X(tc), cy: Y(tr), r: 9, class: "mark-halo" }, svg);
          el("circle", { cx: X(tc), cy: Y(tr), r: 9, class: "mark-target" }, svg);
        }
        const [xc, xr] = s.centroid || [];
        if (isNum(xc) && isNum(xr)) {
          const cx = X(xc), cy = Y(xr), e = Math.max((s.centroid_sigma || 0) * cs, 2);
          el("line", { x1: cx - e, x2: cx + e, y1: cy, y2: cy, class: "mark-centroid" }, svg);
          el("line", { x1: cx, x2: cx, y1: cy - e, y2: cy + e, class: "mark-centroid" }, svg);
          el("line", { x1: cx - 5, x2: cx + 5, y1: cy - 5, y2: cy + 5, class: "mark-centroid" }, svg);
          el("line", { x1: cx - 5, x2: cx + 5, y1: cy + 5, y2: cy - 5, class: "mark-centroid" }, svg);
        }
        const cell = h("figure", "pix-cell");
        cell.append(svg, h("figcaption", null, `Season ${s.season}${(s.quarters || []).length ? ` · Q${s.quarters.join(", Q")}` : ""} · peak S/N ${fmt(peak, 1)}`));
        wrap.appendChild(cell);
      });
      host.appendChild(wrap);
      host.appendChild(h("p", "dossier-small", `Colour runs from no lost light to S/N ${fmt(vmax, 0)}. A crossed square is a pixel outside the stored aperture.`));
    },
  };

  // ---- "How it might look", from the look block (dossier.look_numbers)
  const L = D.look;
  const STAR = (t) => (t >= 6000 ? "#fff3d6" : t >= 5200 ? "#ffd9a0" : t >= 3900 ? "#ffbe78" : "#ff9d5c");    // planet_svg.STAR_SCALE
  const PLANET = (t) => (!isNum(t) ? "#a7b0c3" : t < 200 ? "#cfe6ff" : t < 320 ? "#7fb7a3" : t < 700 ? "#c9a96e" : "#e0784a");
  let gid = 0;
  const space = (host, w, hh, aria) => {
    const svg = el("svg", { viewBox: `0 0 ${w} ${hh}`, class: "space", role: "img", "aria-label": aria });
    el("rect", { width: w, height: hh, rx: 12, fill: "var(--space)" }, svg);
    host.appendChild(svg);
    return svg;
  };
  const glow = (svg, cx, cy, r, col) => {
    const id = `dg${gid++}`, gr = el("radialGradient", { id }, el("defs", {}, svg));
    el("stop", { offset: "0%", "stop-color": col, "stop-opacity": 0.5 }, gr);
    el("stop", { offset: "100%", "stop-color": col, "stop-opacity": 0 }, gr);
    el("circle", { cx, cy, r: r * 1.8, fill: `url(#${id})` }, svg);
  };
  const disc = (svg, cx, cy, r, col, shade = true) => {
    const id = `dd${gid++}`, gr = el("radialGradient", { id, cx: "38%", cy: "35%", r: "75%" }, el("defs", {}, svg));
    el("stop", { offset: "0%", "stop-color": "#ffffff", "stop-opacity": shade ? 0.35 : 0 }, gr);
    el("stop", { offset: "60%", "stop-color": col, "stop-opacity": 1 }, gr);
    el("stop", { offset: "100%", "stop-color": "#000000", "stop-opacity": shade ? 0.5 : 0.2 }, gr);
    el("circle", { cx, cy, r, fill: col }, svg);
    el("circle", { cx, cy, r, fill: `url(#${id})` }, svg);
  };
  const cap = (svg, x, y, words, strong) => text(svg, { x, y, "text-anchor": "middle", class: strong ? "cap strong" : "cap" }, words);
  const name = (D.target && D.target.name) || "the star";
  const LOOK = L ? {
    "look-star"(host) {
      const svg = space(host, 520, 300, `${name} beside the Sun, to scale`);
      const R = 95, rs = L.radius_rsun || 1, k = R * Math.min(rs, 1.5);
      glow(svg, 150, 150, R, "#ffe9b8"); disc(svg, 150, 150, R, STAR(5772), false);
      glow(svg, 385, 150, k, STAR(L.teff_k)); disc(svg, 385, 150, k, STAR(L.teff_k), false);
      cap(svg, 150, 272, "the Sun", true); cap(svg, 150, 288, "5,772 K · 1 solar radius");
      cap(svg, 385, 272, name, true); cap(svg, 385, 288, `${fmt(L.teff_k)} K · ${fmt(rs, 2)} solar radii`);
      cap(svg, 385, 30, `${fmt(L.luminosity_lsun, 3)} of the Sun's light`);
    },
    "look-planet"(host) {
      const svg = space(host, 520, 300, "The candidate planet beside Earth and Neptune, to scale");
      const rp = L.rp_rearth || 1, unit = Math.min(27, 95 / Math.max(rp, 3.88));
      [[90, 1, "#3f8fd8", "Earth", "1 Earth radius"], [255, rp, PLANET(L.teq_k), "the candidate", `${fmt(rp, 2)} Earth radii`],
        [420, 3.88, "#3a5fc8", "Neptune", "3.88 Earth radii"]].forEach(([cx, r, col, n1, n2]) => {
        disc(svg, cx, 140, r * unit, col); cap(svg, cx, 262, n1, true); cap(svg, cx, 278, n2);
      });
    },
    "look-orbit"(host) {
      const svg = space(host, 520, 420, "The orbit drawn against the habitable zone");
      const cx = 260, cy = 210, a = L.a_au, outer = Math.max(L.hz_optimistic_outer_au || 0, L.hz_outer_au || 0, a || 0);
      if (!isNum(outer) || outer <= 0) { cap(svg, cx, cy, "The orbit or the habitable zone is not known."); return; }
      const sc = 186 / outer, ring = (d, fill, op) => { if (isNum(d)) el("circle", { cx, cy, r: d * sc, fill, "fill-opacity": op }, svg); };
      ring(L.hz_optimistic_outer_au, "#2f8f5b", 0.2); ring(L.hz_outer_au, "#2f8f5b", 0.25);
      ring(L.hz_inner_au, "var(--space)", 1); ring(L.hz_inner_au, "#d9a441", 0.15); ring(L.hz_optimistic_inner_au, "var(--space)", 1);
      if (isNum(a)) el("circle", { cx, cy, r: a * sc, fill: "none", stroke: "var(--space-fg)", "stroke-width": 1.5, "stroke-dasharray": "3 4" }, svg);
      glow(svg, cx, cy, 10, STAR(L.teff_k)); el("circle", { cx, cy, r: 7, fill: STAR(L.teff_k) }, svg);
      if (isNum(a)) {
        const px = cx + a * sc * Math.cos(-0.6), py = cy + a * sc * Math.sin(-0.6);
        el("circle", { cx: px, cy: py, r: 5, fill: PLANET(L.teq_k) }, svg);
        text(svg, { x: px + 10, y: py - 8, class: "cap strong" }, `planet, ${fmt(a, 3)} AU`);
        text(svg, { x: px + 10, y: py + 8, class: "cap" }, `${fmt(L.insolation_earth, 1)} times Earth's sunlight, ${fmt(L.teq_k)} K`);
      }
      const lab = (d, words, dy) => { if (isNum(d)) text(svg, { x: cx + 6, y: cy - d * sc + dy, class: "cap" }, words); };
      lab(L.hz_inner_au, `inner edge ${fmt(L.hz_inner_au, 2)} AU`, -4); lab(L.hz_outer_au, `outer edge ${fmt(L.hz_outer_au, 2)} AU`, 12);
    },
    "look-sky"(host) {
      const svg = space(host, 520, 300, "The star seen from the planet beside the Sun seen from Earth");
      const sc = 60, sun = 0.533, ang = L.angular_size_deg;
      glow(svg, 140, 140, (sun / 2) * sc, "#fff3d6"); disc(svg, 140, 140, (sun / 2) * sc, "#fff3d6", false);
      if (isNum(ang)) { const r = Math.min(110, (ang / 2) * sc); glow(svg, 380, 140, r, STAR(L.teff_k)); disc(svg, 380, 140, r, STAR(L.teff_k), false); }
      cap(svg, 140, 256, "the Sun from Earth", true); cap(svg, 140, 272, `${fmt(sun, 2)} degrees across`);
      cap(svg, 380, 256, "the star from the planet", true);
      cap(svg, 380, 272, isNum(ang) ? `${fmt(ang, 2)} degrees across, ${fmt(L.angular_size_sun, 1)} times wider` : "not known");
    },
  } : {};

  // ---- the standalone export: write the eight sections first (the dashboard renders them on the server)
  const main = document.getElementById("dossier");
  if (main && !main.childElementCount) buildStandalone(main);

  function buildStandalone(app) {
    const toggle = h("div", "dossier-toggle");
    toggle.setAttribute("role", "group"); toggle.setAttribute("aria-label", "Level of detail");
    const buttons = {};
    const setLevel = (level) => {
      root.setAttribute("data-level", level);
      root.setAttribute("data-mode", level === "expert" ? "expert" : "simple");
      Object.keys(buttons).forEach((k) => buttons[k].setAttribute("aria-pressed", String(k === level)));
    };
    [["normal", "Normal"], ["expert", "Expert"]].forEach(([level, label]) => {
      const b = h("button", null, label); b.type = "button";
      b.addEventListener("click", () => setLevel(level));
      buttons[level] = b; toggle.appendChild(b);
    });
    setLevel(root.getAttribute("data-level") === "expert" ? "expert" : "normal");
    const head = h("header", "dossier-hero");
    const st = D.status || {};
    head.append(h("p", "dossier-kicker", `Candidate dossier · ${(D.target || {}).target_id || ""}`), h("h1", null, name),
      h("p", "hero-summary", D.summary), h("p", "dossier-status", st.headline || st.label || ""), toggle);
    const tiles = h("div", "tiles dossier-tiles");
    const hd = isNum(E.period_err_days) ? Math.max(4, errDigits(E.period_err_days)) : 4;
    [["period, days", fmt(E.period_days, hd), pmErr(E.period_err_days, hd)],
      ["depth, ppm", fmt(E.depth_ppm), pmErr(E.depth_ppm_err, 0)], ["duration, hours", fmt(E.duration_h, 2), pmErr(E.duration_h_err, 2)],
      ["SDE", fmt(E.sde, 1), ""]]
      .forEach(([k, v, err]) => {
        const t = h("div", "tile"), value = h("div", "stat-value", v);
        if (err) value.appendChild(h("span", "only-expert pm", err));
        t.append(value, h("div", "stat-label", k)); tiles.appendChild(t);
      });
    head.appendChild(tiles);
    app.appendChild(head);
    const ids = ["signal", "repeat", "checks", "pixels", "star", "look", "caveats", "reproduce"];
    const nav = h("nav", "dossier-sections"); nav.setAttribute("aria-label", "Sections");
    const secs = (D.sections || []).map((s, i) => {
      const a = h("a", null, `${s.number} ${s.title}`); a.href = `#${ids[i]}`; nav.appendChild(a);
      const sec = h("section", "dossier-section"); sec.id = ids[i];
      sec.append(h("p", "dossier-kicker mono", s.number), h("h2", null, s.title));
      return sec;
    });
    app.appendChild(nav);
    const sec = (i) => secs[i] || app;
    const fig = (i, chartName, caption, expert) => {
      const f = h("figure", `dossier-fig${expert ? " only-expert" : ""}`);
      const host = h("div", chartName === "gallery" ? "gallery-host" : chartName === "pixmaps" ? "pix-host" : "chart-host");
      host.dataset.chart = chartName;
      f.append(h("figcaption", null, caption), host);
      sec(i).appendChild(f);
    };
    const para = (i, words, cls) => { if (words) sec(i).appendChild(h("p", cls || null, words)); };
    const list = (i, items, extra) => {
      const ul = h("ul", `dossier-caveats${extra ? ` ${extra}` : ""}`);
      items.forEach(([b, rest, cls]) => { const li = h("li", cls || null); li.append(h("strong", null, b), document.createTextNode(` ${rest || ""}`)); ul.appendChild(li); });
      sec(i).appendChild(ul);
    };
    // 01 to 08
    para(0, `Every ${fmt(E.period_days, 2)} days the star dims by ${fmt(E.depth_ppm)} parts per million for ${fmt(E.duration_h, 1)} hours.`, "lede");
    // the pack's own sentences (dossier.page_texts, spec 11.1 and 11.2): the prior claim with its sigma comparison,
    // the TRICERATOPS headline and the in-house lower bound after it. The page words none of them itself, so the
    // relationship words have one source, skysift.pack_text.RELATIONSHIP_WORDS
    const PT = D.page_texts || {};
    para(0, [PT.prior, PT.comparison].filter(Boolean).join(" "), "prior-claim");
    fig(0, "fold", "The folded light curve: single cadences, their bins and the model.");
    fig(0, "spectrum", "The detection spectrum, with the aliases of the period marked.", true);
    const T = D.timing;
    const pd = Math.max(6, errDigits(E.period_err_days)), td = Math.max(4, errDigits(E.t0_bkjd_err));
    const rd = Math.max(4, errDigits(E.t_ref_err_days));
    const eph = [["Period", `${fmt(E.period_days, pd)}${pmErr(E.period_err_days, pd)} d${E.period_source ? ` (${E.period_source})` : ""}`],
      ["Mid-transit T0", `BKJD ${fmt(E.t0_bkjd, td)} (BJD ${fmt(E.t0_bjd, td)})${pmErr(E.t0_bkjd_err, td)}${isNum(E.t0_bkjd_err) ? " d" : ""}`]];
    if (isNum(E.t_ref_bjd)) {
      eph.push(["T_ref (BJD_TDB)", `${fmt(E.t_ref_bjd, rd)}${pmErr(E.t_ref_err_days, rd)} d, epoch ${E.epoch_ref}`],
        ["T_ref (BKJD)", `${fmt(E.t_ref_bkjd, rd)}${pmErr(E.t_ref_err_days, rd)} d`]);
    }
    eph.push([`Duration (${E.duration_source || "search box"})`, `${fmt(E.duration_h, 2)}${pmErr(E.duration_h_err, 2)} h`],
      ["Depth", `${fmt(E.depth_ppm)}${pmErr(E.depth_ppm_err, 0)} ppm${E.depth_source ? ` (${E.depth_source})` : ""}`]);
    if (T) {
      eph.push(["Transit timing", T.ok ? `${T.n_used} of ${T.n_measured} timed transits used, rms ${fmt(T.rms_min, 1)} min.`
        : `No ephemeris error: ${T.reason}.`]);
    }
    list(0, eph.map(([k, val]) => [`${k}:`, val]), "only-expert");
    if (T && T.method) para(0, `The period and T0 come from a ${T.method}. T_ref is the mid-transit time at the centre of the series, where its error is smallest.`, "only-expert dossier-small");
    para(1, `${(P.events || []).length} transits with data were measured one by one.`, "lede");
    fig(1, "events", "The depth of every transit, measured on its own.");
    fig(1, "gallery", "Every transit, one by one, with the model.");
    fig(1, "oddeven", "Odd and even transits, folded apart.");
    fig(1, "seasons", "The depth in each Kepler season.");
    fig(1, "series", "The whole light curve in time bins.", true);
    const PAR = D.parity, SEC = D.secondary;
    const side = (x) => (x && isNum(x.depth_ppm) ? `${fmt(x.depth_ppm)}${pmErr(x.err_ppm, 0)} ppm, ${x.n} transit${x.n === 1 ? "" : "s"}` : "not measured");
    if (PAR) {
      sec(1).appendChild(h("h3", null, "Odd and even"));
      list(1, [["Odd transits:", side(PAR.odd)], ["Even transits:", side(PAR.even)],
        ["Difference:", isNum(PAR.difference_sigma) ? `${fmt(PAR.difference_sigma, 1)} sigma` : "not measured"]]);
      const vet = metrics("rung2_shape");
      if (isNum(PAR.difference_sigma) && isNum(vet.parity_sigma) && Math.abs(PAR.difference_sigma - vet.parity_sigma) > 0.5) {
        para(1, `Odd and even differ by ${fmt(PAR.difference_sigma, 1)} sigma here and by ${fmt(vet.parity_sigma, 1)} sigma in the vetting check: ${vet.stacked
          ? "the vetting number is the stacked fold's; the reporting number is from the per-event depths"
          : "the vetting number uses the scaled per-event errors; the reporting number inflates each parity's error by its own scatter"}.`, "dossier-small");
      }
    }
    if (SEC) {
      const none = SEC.albedo_note || (isNum(SEC.albedo_limit) && (SEC.albedo_limit > 1 || (D.shape && D.shape.degenerate)));
      const albedo = none ? `no constraint on the geometric albedo${SEC.albedo_reason ? `: ${SEC.albedo_reason}` : ""}`
        : isNum(SEC.albedo_limit) ? `below ${fmt(SEC.albedo_limit, 2)}` : "not known without a transit fit";
      sec(1).appendChild(h("h3", null, "Secondary eclipse"));
      list(1, [["Depth at phase 0.5:", `${fmt(SEC.depth_ppm)}${pmErr(SEC.err_ppm, 0)} ppm`], ["3-sigma limit:", `${fmt(SEC.limit_3sigma_ppm)} ppm`],
        ["Strongest secondary:", isNum(SEC.strongest_ppm) ? `${fmt(SEC.strongest_ppm)} ppm at phase ${fmt(SEC.strongest_phase, 2)} (${fmt(SEC.strongest_sigma, 1)} sigma)` : "not searched"],
        ["Geometric albedo limit:", albedo]]);
    }
    para(2, st.headline ? `${st.headline}.` : "", "lede");
    list(2, (D.why || []).map((w) => [`${w.name}: ${w.status}.`, w.sentence]));
    const fap = D.fap || {}, fpp = D.fpp || {};
    // the false-positive items lead with the TRICERATOPS headline when a current cross-check exists; the in-house
    // estimate follows as a lower bound that names the scenarios it lacks (spec 11.2)
    list(2, [[`False alarm: ${fap.words || "not yet calibrated"}.`, fap.sentence_not_covered],
      ...(PT.fpp_lead ? [["False-positive probability:", PT.fpp_lead]] : []),
      [`${fpp.label || "False positive"}, in-house: ${fpp.words || "not computed"}${fpp.signed_off ? "" : " (provisional)"}.`,
        [PT.in_house, fpp.sentence_not_covered].filter(Boolean).join(" ")]]);
    // the cross-check, as the dashboard's section 03 draws it
    const CC = D.cross_check;
    const g3 = (x) => (isNum(x) ? String(Number(x.toPrecision(3))) : "–");
    // the median with its 16th to 84th percentile range (referee pack ruling s)
    const spread = (m) => `${g3(CC[m])} (16th to 84th percentile ${g3(CC[`${m}_p16`])} to ${g3(CC[`${m}_p84`])})`;
    sec(2).appendChild(h("h3", null, "Cross-check"));
    if (CC && Array.isArray(CC.runs)) {
      // the class name is only the label of where the medians fall (crosscheck.class_label_text, spec 11.2)
      para(2, `An estimate with TRICERATOPS ${CC.tool_version} (Giacalone et al. 2021) over ${CC.n_runs} runs${CC.n_degenerate ? `, ${CC.n_degenerate} degenerate and left out` : ""}: median false-positive probability ${spread("fpp")}, ${spread("fpp_no_stp")} without a planet transiting an unresolved bound companion (STP), which SkySift's model leaves out, and nearby false-positive probability ${spread("nfpp")}. ${PT.class_text ? `${PT.class_text} ` : ""}The cross-check never blocks the live draft. ${CC.current
        ? "It was made for this dossier's period, depth and neighbours."
        : "It was made for an earlier dossier (the period, the depth or the neighbours have changed since), so it does not count until it is run again."}`);
      if ((CC.caveats || []).length) {
        const ul = h("ul", "dossier-small");
        CC.caveats.forEach((c) => ul.appendChild(h("li", null, c)));
        sec(2).appendChild(ul);
      }
    } else if (CC) {
      para(2, "This cross-check was stored by an earlier version of the tool, before its median and range and the FPP without STP; run it again.");
    } else {
      para(2, "No TRICERATOPS cross-check is stored yet.");
    }
    // the TESS statement, as the dashboard's section 03 draws it (referee pack spec 10)
    const TS = D.tess;
    sec(2).appendChild(h("h3", null, "TESS"));
    if (TS && TS.available) {
      para(2, TS.sentence || "");
      if (TS.methods) para(2, TS.methods, "dossier-small");
      const assessed = TS.verdict !== "not assessed";       // no counted sector: no table and no S/N line
      const dil = TS.dilution || {}, noise = TS.noise || {}, sg = TS.significance || {};
      const cad = (m) => (Math.abs(m - Math.round(m)) < 1e-9 ? `${Math.round(m)} min` : `${Math.round(m * 60)} s`);
      if (assessed) list(2, (TS.sectors || []).map((s) => [`Sector ${s.sector} (cycle ${s.cycle}, ${cad(s.cadence_min)}):`,
        `${s.n_transits} transit${s.n_transits === 1 ? "" : "s"} in the windows, timing error ${isNum(s.timing_error_days) ? `${fmt(s.timing_error_days * 24, 2)} h` : "not known"}, ${s.uncertain ? "uncertain" : "firm"}.`]), "only-expert");
      if (assessed) para(2, `TIC ${TS.tic} · Tmag ${fmt(TS.tmag, 2)} (${TS.tmag_source || "source not stored"}) · contamination c = ${fmt(dil.c, 2)} over ${dil.aperture_pixels} pixels, ${fmt(dil.c_hard42, 2)} as the two-pixel-radius sum, for comparison · blended T = ${fmt(dil.t_total, 2)} · diluted depth ${fmt(dil.depth_tess_ppm)} ppm, ${fmt(dil.depth_tess_hard42_ppm)} ppm with that sum · noise over the transit ${fmt(noise.nominal_t14_ppm)} to ${fmt(noise.conservative_t14_ppm)} ppm · per-transit S/N ${fmt(sg.per_transit_conservative, 2)} to ${fmt(sg.per_transit_nominal, 2)} · about ${fmt(TS.n_expected, 1)} transits expected from phase coverage · inputs queried ${TS.queried || "on an unknown date"}`, "only-expert mono dossier-small");
      if ((TS.caveats || []).length) {
        const ul = h("ul", "dossier-small");
        TS.caveats.forEach((c) => ul.appendChild(h("li", null, c)));
        sec(2).appendChild(ul);
      }
    } else {
      const why = TS && (TS.caveats || []).length ? ` (${TS.caveats.join("; ")})` : "";
      para(2, `TESS coverage was not assessed${why}.`);
    }
    const kinds = Object.keys(fap.k_by_kind || {}).sort();
    if (kinds.length) para(2, kinds.map((kind, i) => `${kind}${i ? "" : " nulls"} ${fmt(fap.k_by_kind[kind])} of ${fmt((fap.n_by_kind || {})[kind])}`).join(", "), "prob-split");
    if (isNum(fap.n)) para(2, `${fmt(fap.k)} of ${fmt(fap.n)} null searches reached SDE ${fmt(fap.sde, 1)}.`, "only-expert mono");
    const SH = D.shape;
    if (SH) {
      sec(2).appendChild(h("h3", "only-expert", "Shape with errors"));
      const method = SH.method ? `${SH.method[0].toUpperCase()}${SH.method.slice(1)}` : "Bootstrap over transits";
      para(2, `${method}: ${fmt(SH.n_converged)} of ${fmt(SH.n_boot)} resamples converged. ${SHAPE_WORDS}${SH.ok ? "" : ` No bootstrap errors: ${SH.reason}.`}`, "only-expert");
      if (SH.degenerate) para(2, `The fit is degenerate (${SH.degeneracy_reason}).`, "only-expert dossier-caveat");
      const row = (name, x, d) => (x && isNum(x.median) ? [`${name}:`, `${fmt(x.median, d)}${pmErr(x, d)}${isNum(x.point) && isNum(x.minus) ? `, full-data fit ${fmt(x.point, d)}` : ""}`] : null);
      const rows = [row("k, radius ratio", SH.k, 4), row("b, impact parameter", SH.b, 2), row(`a / R★${AR_FIT[(SH.comparison || {}).quoted] || ""}`, SH.a_over_r, 1),
        row("T14, hours", SH.t14_h, 2), row("model depth, ppm", SH.model_depth_ppm, 0), row("box depth, ppm", SH.box_depth_ppm, 0),
        row("box duration, hours", SH.box_duration_h, 2), isNum(SH.ingress_min) ? ["ingress, minutes:", fmt(SH.ingress_min, 1)] : null];
      list(2, rows.filter(Boolean), "only-expert");
    }
    if (D.pixels) fig(3, "pixmaps", "Difference images by season: the ring marks this star, the square the neighbour, the cross the centroid.");
    else para(3, "The pixel step has not produced difference images for this signal.");
    const r5 = metrics("rung5_pixels");
    // voting seasons first, then those that did not vote, then those whose vote was not recorded (as the dashboard)
    const voteRank = (s) => (s.voting === true ? 0 : s.voting === false ? 1 : 2);
    const seasons = (r5.seasons || []).filter((s) => s && typeof s === "object")
      .sort((a, b) => voteRank(a) - voteRank(b) || (a.season ?? 99) - (b.season ?? 99));
    const where = (s) => `nearer ${s.nearer === "target" ? "this star" : s.nearer || "neither"}, ${fmt(s.dist_target_pix, 2)} px from it and ${fmt(s.dist_neighbour_pix, 2)} px from the neighbour.`;
    list(3, seasons.map((s) => [`Season ${s.season}:`,
      s.voting === true ? `voting, ${where(s)}` : s.voting === false ? `not voting, ${s.non_voting_reason || "no reason stored"}.` : `vote not recorded, ${where(s)}`,
      s.voting === true ? null : "is-muted"]));
    // the pixel offset as skysift.pack.offset_sentence words it (spec 11.5, after ruling e): the Gaia offset and the
    // exclusion radius first, then the offset against the star's own image (Bryson et al. 2013)
    const OFF = D.centroid_offset;
    if (OFF && isNum(OFF.catalogue_referenced_arcsec)) {
      const pmA = (x, e) => (isNum(e) && e > 0 ? `${fmt(x, errDigits(e))} ± ${fmt(e, errDigits(e))}` : fmt(x, 2));
      const catErr = OFF.catalogue_referenced_err_arcsec;
      const radius = isNum(OFF.exclusion_radius_arcsec) ? OFF.exclusion_radius_arcsec
        : (isNum(catErr) ? Math.max(3 * catErr, 0.5) : null);
      const nb = OFF.nearest_beyond;
      let lead = `Against the Gaia position projected through each stamp's WCS, a reference that carries the WCS zero point and the registration, the difference-image centroid sits ${pmA(OFF.catalogue_referenced_arcsec, catErr)} arcsec away`;
      if (radius !== null) {
        lead += `, with an exclusion radius of ${fmt(radius, 2)} arcsec (max(3 sigma, 0.5 arcsec)) within which a background source is not excluded`;
        if (nb && nb.source_id && isNum(nb.centroid_sep_arcsec)) lead += `; the nearest Gaia source beyond it is Gaia DR3 ${nb.source_id} at ${fmt(nb.centroid_sep_arcsec, 1)} arcsec from the centroid${isNum(nb.gmag) ? ` (G = ${fmt(nb.gmag, 1)})` : ""}`;
      }
      const same = "Both errors are the larger of the seasons' combined error and their scatter.";
      const own = OFF.reference === "the star's own direct-image centroid";
      const off = `${pmA(OFF.self_referenced_arcsec, OFF.self_referenced_err_arcsec)} arcsec (${fmt(OFF.sigma, 1)} sigma)`;
      const pull = OFF.pulled === true && isNum(OFF.direct_pull_pix) ? `; its reference is pulled by the neighbour, up to ${fmt(OFF.direct_pull_pix, 2)} px from the Gaia position` : "";
      para(3, isNum(OFF.self_referenced_arcsec) && isNum(OFF.sigma)
        ? (own ? `${lead}. Against the star's own out-of-transit image the difference-image centroid is offset by ${off}, ${OFF.counts ? "an offset" : "not an offset"} by the rule of Bryson et al. (2013), which asks for more than ${OFF.sigma_level} sigma and more than ${OFF.level_arcsec} arcsec${pull}. ${same}`
          : `${lead}. Against ${OFF.reference || "the fitted position of the star"} (the star's direct-image centroid is not stored for every season) the difference-image centroid is offset by ${off}, not assessed from the star's own image. ${same}`)
        : `${lead}.`, "pixel-offset");
    }
    const close = (D.caveats || []).find((c) => c && c.key === "close_call");
    if (close) para(3, close.text, "dossier-caveat");
    const S = D.star || {}, PL = D.planet || {};
    const v = (x, d, unit) => (x && isNum(x.median) ? `${fmt(x.median, d)} (+${fmt(x.plus, d)} / -${fmt(x.minus, d)}) ${unit}` : "not known");
    list(4, [["Spectral type", S.spectral_type || "not known"], ["Distance", v(S.distance_pc, 0, "pc")], ["Temperature", v(S.teff, 0, "K")],
      ["Radius", v(S.radius, 3, "solar radii")], ["Mass", v(S.mass, 3, "solar masses")], ["Planet radius", v(PL.rp_rearth, 2, "Earth radii")],
      ["Orbit", v(PL.a_au, 3, "AU")], ["Equilibrium temperature", v(PL.teq_k, 0, "K")]]);
    if (L) {
      [["look-star", "The star beside the Sun, to scale. The colour follows the surface temperature."],
        ["look-planet", "The planet beside Earth and Neptune, to scale. Its size is measured; its surface is a guess."],
        ["look-orbit", "The orbit against the habitable zone, to scale (Kopparapu et al. 2013 edges; lighter is the optimistic zone)."],
        ["look-sky", "The star seen from the planet, beside the Sun seen from Earth, at the same angular scale."]]
        .forEach(([n, caption]) => fig(5, n, caption));
    }
    else para(5, "The star could not be characterised, so the panel is not drawn.");
    list(6, (D.caveats || []).map((c) => [`${c.title}.`, c.text]));
    const prov = D.provenance || {};
    sec(7).appendChild(h("pre", "dossier-pre", (prov.commands || []).join("\n")));
    para(7, `Search version ${prov.search_version}, ladder version ${prov.ladder_version}, commit ${prov.git_commit}, ${(prov.queries || []).length} logged queries.`);
    const body = h("div", "dossier-body");
    secs.forEach((s) => body.appendChild(s));
    app.appendChild(body);
  }

  // ---- fill every box
  document.querySelectorAll("[data-chart]").forEach((host) => {
    const draw = DRAW[host.dataset.chart] || LOOK[host.dataset.chart];
    if (!draw || host.childElementCount) return;
    try {
      draw(host);
      if (!host.childElementCount) host.appendChild(h("p", "dossier-notice", "No data for this figure."));
    } catch (err) {
      host.replaceChildren(h("p", "dossier-notice", "This figure could not be drawn from the stored data."));
      if (window.console) console.error(err);
    }
  });
})();
