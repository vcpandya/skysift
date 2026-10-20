// Draws the signal page's plots from /api/signals/{id}/plots.json: the folded transit into #fold, then into #plots the
// full curve, odd versus even and the phase 0.5 window, and in expert mode (data-mode) the periodogram and the season
// depths. Each panel is a <figure> with its caption from data-captions. data-layout is the figure template (plots.LAYOUT)
// merged into every layout; data-marks names the CSS custom properties that colour the marks, read again when the
// theme changes. Colour never carries status here: data in ion, faint points in the secondary text colour, the model
// in solar, titles and labels in the text colour (spec 3, "Marks").
(() => {
  const root = document.getElementById("plots");
  if (!root) return;
  const foldSlot = document.getElementById("fold") || root;
  const attr = (name) => { try { return JSON.parse(root.dataset[name] || "{}"); } catch { return {}; } };
  const captions = attr("captions"), template = attr("layout"), marks = attr("marks");
  const expert = root.dataset.mode === "expert";
  const cfg = { displaylogo: false, responsive: true };
  let data = null;

  // server and exception text is untrusted: it goes in as text, never as markup
  const msg = (text, retry) => {
    const p = document.createElement("p"); p.className = "muted"; p.textContent = text;
    if (retry) { const a = document.createElement("a"); a.href = ""; a.textContent = " Retry"; p.appendChild(a); }
    foldSlot.replaceChildren(p);
    if (root !== foldSlot) root.replaceChildren(p.cloneNode(true));   // the light-curve section says it too
  };
  const colours = () => {
    const css = getComputedStyle(document.documentElement);
    return Object.fromEntries(Object.entries(marks).map(([k, v]) => [k, css.getPropertyValue(v).trim()]));
  };
  const layout = (c, { title, xaxis, yaxis, ...rest }) => {
    const axis = { gridcolor: c.grid, linecolor: c.grid, tickcolor: c.grid };
    return {
      ...template, ...rest,
      font: { ...template.font, color: c.text },
      title: { ...template.title, text: title, font: { ...(template.title || {}).font, color: c.text } },
      xaxis: { ...template.xaxis, ...axis, ...xaxis },
      yaxis: { ...template.yaxis, ...axis, ...yaxis },
    };
  };
  const figure = (parent, id) => {
    const fig = document.createElement("figure"); fig.className = "plot-figure";
    const div = document.createElement("div"); div.id = id; div.className = "plot";
    fig.appendChild(div);
    if (captions[id]) { const cap = document.createElement("figcaption"); cap.textContent = captions[id]; fig.appendChild(cap); }
    parent.appendChild(fig);
    return div;
  };
  const note = (parent, id, text) => {
    const p = document.createElement("p"); p.id = id; p.className = "plot-note muted"; p.textContent = text;
    parent.appendChild(p);
  };

  const drawFold = (c) => {
    const fo = data.fold;
    const d = fo.model_depth_ppm * 1e-6, h = fo.model_duration_h / 2;
    Plotly.newPlot(figure(foldSlot, "plot-fold"), [
      { x: fo.phase_h, y: fo.flux, mode: "markers", marker: { color: c.faint, size: 2, opacity: 0.4 }, name: "points" },
      { x: fo.bin_phase_h, y: fo.bin_flux, mode: "markers", marker: { color: c.data, size: 6 }, name: "15 min bins" },
      { x: [-fo.window_h, -h, -h, h, h, fo.window_h], y: [1, 1, 1 - d, 1 - d, 1, 1], mode: "lines",
        line: { color: c.model, width: 2 }, name: "box model" }],
      // the hero: frame the dip, not the outliers (points beyond the range are clipped, the bins stay inside)
      layout(c, { title: "Phase fold", xaxis: { title: "hours from mid-transit" },
                  yaxis: { title: "relative flux", ...(d > 0 ? { range: [1 - 2 * d, 1 + d] } : {}) } }), cfg);
  };
  const drawFull = (c) => {
    const f = data.full;
    Plotly.newPlot(figure(root, "plot-full"), [
      { x: f.time, y: f.flux, mode: "markers", marker: { color: c.data, size: 2, opacity: 0.6 }, name: "flux" },
      { x: f.transit_times, y: f.transit_times.map(() => 1.0), mode: "markers",
        marker: { color: c.model, size: 6, symbol: "line-ns-open" }, name: "transits" }],
      layout(c, { title: "Detrended light curve", xaxis: { title: "time (BKJD d)" }, yaxis: { title: "relative flux" } }), cfg);
  };
  const drawOddEven = (c) => {
    const oe = data.oddeven;
    Plotly.newPlot(figure(root, "plot-oddeven"), [
      { x: oe.bin_phase_h, y: oe.odd_bin, mode: "lines+markers", line: { color: c.data }, name: "odd" },
      { x: oe.bin_phase_h, y: oe.even_bin, mode: "lines+markers", line: { color: c.faint, dash: "dot" }, name: "even" }],
      layout(c, { showlegend: true, title: "Odd vs even transits (binned)", xaxis: { title: "hours from mid-transit" },
                  yaxis: { title: "relative flux" } }), cfg);
  };
  const drawSecondary = (c) => {
    const se = data.secondary, depth = data.fold.model_depth_ppm * 1e-6;
    Plotly.newPlot(figure(root, "plot-secondary"), [
      { x: se.phase_h, y: se.flux, mode: "markers", marker: { color: c.faint, size: 2, opacity: 0.4 }, name: "points" },
      { x: se.bin_phase_h, y: se.bin_flux, mode: "markers", marker: { color: c.data, size: 6 }, name: "bins" }],
      layout(c, { title: "Phase 0.5 window (same vertical scale as the primary)", xaxis: { title: "hours from phase 0.5" },
                  yaxis: { title: "relative flux", range: [1 - 1.5 * depth, 1 + 0.5 * depth] } }), cfg);
  };
  const PERIODOGRAM_NOTES = {
    off: "No periodogram summary was stored for this run (store_periodogram was off, or kept signals only).",
    evicted: "The periodogram summary was evicted by budget retention.",
    missing: "The periodogram summary file is missing from the blob store.",
    unreadable: "The periodogram summary file could not be read.",
  };
  const drawPeriodogram = (c) => {
    const p = data.periodogram;
    if (!p) {
      note(root, "plot-periodogram", PERIODOGRAM_NOTES[data.periodogram_status] || "No periodogram summary is available for this run.");
      return;
    }
    const shapes = [{ type: "line", x0: p.period[0], x1: p.period[p.period.length - 1], y0: p.sde_min, y1: p.sde_min,
                      line: { color: c.faint, dash: "dot" } }];
    p.aliases.concat(p.systematic_periods).forEach((x) => shapes.push({ type: "line", x0: x, x1: x, yref: "paper", y0: 0, y1: 1,
                                                                       line: { color: c.faint, width: 1, dash: "dash" } }));
    shapes.push({ type: "line", x0: p.detected_period, x1: p.detected_period, yref: "paper", y0: 0, y1: 1, line: { color: c.model, width: 1 } });
    Plotly.newPlot(figure(root, "plot-periodogram"), [
      { x: p.period, y: p.sde, mode: "lines", line: { color: c.data, width: 1 }, connectgaps: false, name: "SDE" },
      { x: p.peaks.map((k) => k.period), y: p.peaks.map((k) => k.sde), mode: "markers",
        marker: { color: c.data, size: 7, symbol: "triangle-down" }, name: "peaks" }],
      layout(c, { title: `Periodogram (dotted: ${p.sde_min_label || "threshold"} SDE ${p.sde_min})`,
                  xaxis: { type: "log", title: "period (d)" }, yaxis: { title: "SDE" }, shapes }), cfg);
  };
  const drawSeasons = (c) => {
    const s = data.seasons;
    if (!s) return;
    Plotly.newPlot(figure(root, "plot-seasons"), [
      { x: s.season, y: s.depth_ppm, error_y: { type: "data", array: s.err_ppm, visible: true, color: c.data }, mode: "markers",
        marker: { color: c.data, size: 8 }, name: "season depth" },
      { x: [Math.min(...s.season) - 0.5, Math.max(...s.season) + 0.5], y: [s.mean_ppm, s.mean_ppm], mode: "lines",
        line: { color: c.model, dash: "dot" }, name: "weighted mean" }],
      layout(c, { title: "Depth per season", xaxis: { title: "season (quarter mod 4)", dtick: 1 }, yaxis: { title: "depth (ppm)" } }), cfg);
  };

  const draw = () => {
    if (!data) return;
    const c = colours();
    [foldSlot, root].forEach((el) => { el.querySelectorAll(".js-plotly-plot").forEach((p) => Plotly.purge(p)); el.replaceChildren(); });
    drawFold(c);
    [drawFull, drawOddEven, drawSecondary, ...(expert ? [drawPeriodogram, drawSeasons] : [])].forEach((fn) => fn(c));
  };

  const load = async () => {
    if (!window.Plotly) { msg("Plotly is not available offline yet. Start the dashboard once with network access to download it."); return; }
    try {
      const r = await fetch(`/api/signals/${root.dataset.signal}/plots.json`);
      const body = await r.json();
      if (!r.ok) { msg(`Light curve unavailable (${body.error}): ${body.detail}`, true); return; }
      data = body;
    } catch (e) { msg(`Could not load plot data: ${e}`); return; }
    draw();
  };
  // redraw in the new colours when the theme changes here or, on "system", in the operating system
  document.addEventListener("skysift:theme", draw);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
  document.addEventListener("DOMContentLoaded", load);
})();
