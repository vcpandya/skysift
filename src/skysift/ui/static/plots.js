// Renders the six panels from /api/signals/{id}/plots.json. Colour carries status only; traces use the theme text colour.
async function skysiftPlots(signalId) {
  const root = document.getElementById("plots");
  if (!root) return;
  // server and exception text is untrusted: it goes in as text, never as markup
  const msg = (text, retry) => {
    const p = document.createElement("p"); p.className = "muted"; p.textContent = text;
    if (retry) { const a = document.createElement("a"); a.href = ""; a.textContent = " retry"; p.appendChild(a); }
    root.replaceChildren(p);
  };
  if (!window.Plotly) { msg("Plotly is not available offline yet; start the dashboard once with network access to download it."); return; }
  let data;
  try {
    const r = await fetch(`/api/signals/${signalId}/plots.json`);
    data = await r.json();
    if (!r.ok) { msg(`Light curve unavailable (${data.error}): ${data.detail}`, true); return; }
  } catch (e) { msg(`Could not load plot data: ${e}`); return; }
  const fg = getComputedStyle(document.documentElement).getPropertyValue("--fg").trim();
  const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted").trim();
  const base = { paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: fg, size: 11 },
                 margin: { l: 56, r: 12, t: 28, b: 40 }, showlegend: false };
  const cfg = { displaylogo: false, responsive: true };
  const panel = (id) => { const d = document.createElement("div"); d.id = id; d.className = "plot"; root.appendChild(d); return d; };
  if (data.periodogram) {
    const p = data.periodogram;
    const shapes = [{ type: "line", x0: p.period[0], x1: p.period[p.period.length - 1], y0: p.sde_min, y1: p.sde_min, line: { color: muted, dash: "dot" } }];
    const thresholdNote = `${p.sde_min_label || "threshold"} SDE ${p.sde_min}`;
    p.aliases.concat(p.systematic_periods).forEach((x) => shapes.push({ type: "line", x0: x, x1: x, yref: "paper", y0: 0, y1: 1, line: { color: muted, width: 1, dash: "dash" } }));
    shapes.push({ type: "line", x0: p.detected_period, x1: p.detected_period, yref: "paper", y0: 0, y1: 1, line: { color: fg, width: 1 } });
    Plotly.newPlot(panel("plot-periodogram"), [
      { x: p.period, y: p.sde, mode: "lines", line: { color: fg, width: 1 }, connectgaps: false, name: "SDE" },
      { x: p.peaks.map((k) => k.period), y: p.peaks.map((k) => k.sde), mode: "markers", marker: { color: fg, size: 7, symbol: "triangle-down" }, name: "peaks" }],
      { ...base, title: `Periodogram (per-band SDE envelope; dotted line: ${thresholdNote})`, xaxis: { type: "log", title: "period (d)" }, yaxis: { title: "SDE" }, shapes }, cfg);
  } else {
    const notes = {
      off: "No periodogram summary was stored for this run (store_periodogram was off, or kept signals only).",
      evicted: "The periodogram summary was evicted by budget retention.",
      missing: "The periodogram summary file is missing from the blob store.",
      unreadable: "The periodogram summary file could not be read.",
    };
    const d = panel("plot-periodogram"); d.className = "muted";
    d.textContent = notes[data.periodogram_status] || "No periodogram summary is available for this run.";
  }
  const f = data.full;
  Plotly.newPlot(panel("plot-full"), [
    { x: f.time, y: f.flux, mode: "markers", marker: { color: fg, size: 2, opacity: 0.6 }, name: "flux" },
    { x: f.transit_times, y: f.transit_times.map(() => 1.0), mode: "markers", marker: { color: fg, size: 6, symbol: "line-ns-open" }, name: "transits" }],
    { ...base, title: "Detrended light curve", xaxis: { title: "time (BKJD d)" }, yaxis: { title: "relative flux" } }, cfg);
  const box = (fold) => {
    const d = fold.model_depth_ppm * 1e-6, h = fold.model_duration_h / 2;
    return { x: [-fold.window_h, -h, -h, h, h, fold.window_h], y: [1, 1, 1 - d, 1 - d, 1, 1], mode: "lines", line: { color: muted, width: 1.5 }, name: "box model" };
  };
  const fo = data.fold;
  Plotly.newPlot(panel("plot-fold"), [
    { x: fo.phase_h, y: fo.flux, mode: "markers", marker: { color: muted, size: 2, opacity: 0.4 }, name: "points" },
    { x: fo.bin_phase_h, y: fo.bin_flux, mode: "markers", marker: { color: fg, size: 6 }, name: "15 min bins" }, box(fo)],
    { ...base, title: "Phase fold", xaxis: { title: "hours from mid-transit" }, yaxis: { title: "relative flux" } }, cfg);
  const oe = data.oddeven;
  Plotly.newPlot(panel("plot-oddeven"), [
    { x: oe.bin_phase_h, y: oe.odd_bin, mode: "lines+markers", line: { color: fg }, name: "odd" },
    { x: oe.bin_phase_h, y: oe.even_bin, mode: "lines+markers", line: { color: muted, dash: "dot" }, name: "even" }],
    { ...base, showlegend: true, title: "Odd vs even transits (binned)", xaxis: { title: "hours from mid-transit" }, yaxis: { title: "relative flux" } }, cfg);
  const se = data.secondary;
  const yr = [1 - 1.5 * fo.model_depth_ppm * 1e-6, 1 + 0.5 * fo.model_depth_ppm * 1e-6];
  Plotly.newPlot(panel("plot-secondary"), [
    { x: se.phase_h, y: se.flux, mode: "markers", marker: { color: muted, size: 2, opacity: 0.4 }, name: "points" },
    { x: se.bin_phase_h, y: se.bin_flux, mode: "markers", marker: { color: fg, size: 6 }, name: "bins" }],
    { ...base, title: "Phase 0.5 window (same vertical scale as the primary)", xaxis: { title: "hours from phase 0.5" }, yaxis: { title: "relative flux", range: yr } }, cfg);
  if (data.seasons) {
    const s = data.seasons;
    Plotly.newPlot(panel("plot-seasons"), [
      { x: s.season, y: s.depth_ppm, error_y: { type: "data", array: s.err_ppm, visible: true }, mode: "markers", marker: { color: fg, size: 8 }, name: "season depth" },
      { x: [Math.min(...s.season) - 0.5, Math.max(...s.season) + 0.5], y: [s.mean_ppm, s.mean_ppm], mode: "lines", line: { color: muted, dash: "dot" }, name: "weighted mean" }],
      { ...base, title: "Depth per season", xaxis: { title: "season (quarter mod 4)", dtick: 1 }, yaxis: { title: "depth (ppm)" } }, cfg);
  }
}
document.addEventListener("DOMContentLoaded", () => { const el = document.getElementById("plots"); if (el) skysiftPlots(el.dataset.signal); });
