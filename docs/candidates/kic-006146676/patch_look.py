"""Add the illustrated 'What it might look like' section: star and planet to scale, orbit against the habitable zone,
the view from the planet, and the composition/atmosphere inference. Deliberately a dark 'space' panel in both themes."""
from pathlib import Path
H = Path(__file__).parent
p = H / "dossier_template.html"; s = p.read_text(encoding="utf-8")

css = """
/* illustrated panels: one deliberate dark look in both themes */
.space{background:#0b1020;color:#f0efec;border-radius:var(--radius);padding:16px 16px 12px;border:1px solid rgba(255,255,255,.08);min-width:0}
.space h3{color:#f0efec;font-weight:500;font-size:.98rem}
.space .sub{color:#b9bccb;font-size:.86rem;margin-top:2px}
.space svg{display:block;width:100%;height:auto;margin-top:8px}
.space .cap{font-family:var(--mono);font-size:11px;fill:#b9bccb}
.space .cap.strong{fill:#f0efec;font-weight:500}
.space .legend{color:#b9bccb}
.look-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.infer{display:grid;gap:10px}
.infer .row{display:grid;grid-template-columns:150px 1fr;gap:12px;align-items:start;padding:10px 0;border-bottom:1px solid var(--grid)}
.infer .row:last-child{border-bottom:0}
.infer .row b{font-weight:500}
.infer .row span{color:var(--ink-2);font-size:.93rem}
@media (max-width:560px){.infer .row{grid-template-columns:1fr}}
.valley{margin-top:6px}
"""
s = s.replace("footer{padding-block:28px 40px;", css + "footer{padding-block:28px 40px;", 1)

# TOC entry and section numbering
s = s.replace('<a href="#star">Star and planet</a><a href="#caveats">Caveats</a>', '<a href="#star">Star and planet</a><a href="#look">How it might look</a><a href="#caveats">Caveats</a>')
s = s.replace('<div class="kicker">06</div>\n    <h2>What is not yet known</h2>', '<div class="kicker">07</div>\n    <h2>What is not yet known</h2>')
s = s.replace('<div class="kicker">07</div>\n    <h2>How to reproduce this</h2>', '<div class="kicker">08</div>\n    <h2>How to reproduce this</h2>')

section = """
<section id="look">
  <div class="sec-head">
    <div class="kicker">06</div>
    <h2>How it might look</h2>
    <p class="lede">Nothing on this page is a photograph. These drawings are to scale and built only from the numbers above: the star's size and colour from its temperature, the planet's size from the transit depth, the orbit from Kepler's third law. The atmosphere panel says what planets of this size and temperature usually are, and what this one cannot yet be shown to be.</p>
  </div>
  <div class="look-grid">
    <div class="space" id="look-star"><h3>The star beside the Sun</h3><div class="sub">Both discs to the same scale. Colour follows surface temperature.</div></div>
    <div class="space" id="look-planet"><h3>The planet beside Earth and Neptune</h3><div class="sub">Radii to the same scale. The candidate's surface is a guess; its size is measured.</div></div>
  </div>
  <div class="look-grid" style="margin-top:14px">
    <div class="space" id="look-orbit"><h3>The orbit, against the habitable zone</h3><div class="sub">Distances to scale. Habitable-zone edges from the Kopparapu et al. (2013) limits for this star's temperature and luminosity.</div></div>
    <div class="space" id="look-sky"><h3>The star as seen from the planet</h3><div class="sub">Apparent size on the sky, compared with the Sun seen from Earth, at the same angular scale.</div></div>
  </div>
  <div class="fig" style="margin-top:14px" id="look-infer">
    <h3>What kind of world, and what kind of air</h3>
    <div class="sub">Inferences from population statistics, not measurements of this planet. Each line says where it comes from.</div>
    <div class="valley" id="valley"></div>
    <div class="infer" id="infer-rows" style="margin-top:8px"></div>
  </div>
</section>
"""
anchor = '<section id="caveats">'
assert anchor in s; s = s.replace(anchor, section + "\n" + anchor, 1)

js = r"""
/* ---------- how it might look ---------- */
if (D.context) {
  const C = D.context, S = C.summary.stellar_baseline, PB = C.summary.planet_baseline;
  const Teff = S.Teff_K.median, Rs = S.R_star_Rsun.median, Ms = S.M_star_Msun.median, a = PB.a_AU.median, Rp = PB.Rp_from_k_Rearth.median;
  const L = Rs ** 2 * (Teff / 5772) ** 4, insol = L / a ** 2;
  const starColour = T => T > 5500 ? '#fff3d6' : T > 4500 ? '#ffd9a0' : T > 3900 ? '#ffbe78' : '#ff9d5c';
  // Kopparapu 2013 (erratum) effective-flux limits
  const kop = (c, T) => { const t = T - 5780; return c[0] + c[1] * t + c[2] * t * t + c[3] * t ** 3 + c[4] * t ** 4; };
  const S_rv = kop([1.7763, 1.4335e-4, 3.3954e-9, -7.6364e-12, -1.1950e-15], Teff), S_rg = kop([1.0512, 1.3242e-4, 1.5418e-8, -7.9895e-12, -1.8328e-15], Teff), S_mg = kop([0.3438, 5.8942e-5, 1.6558e-9, -3.0045e-12, -5.2983e-16], Teff), S_em = kop([0.3179, 5.4513e-5, 1.5313e-9, -2.7786e-12, -4.8997e-16], Teff);
  const d_rv = Math.sqrt(L / S_rv), d_rg = Math.sqrt(L / S_rg), d_mg = Math.sqrt(L / S_mg), d_em = Math.sqrt(L / S_em);
  const Mp = 2.69 * Rp ** 0.93;                       // Weiss & Marcy 2014 population relation, 1.5 < R < 4
  const vesc = 11.19 * Math.sqrt(Mp / Rp);
  const g = 9.81 * Mp / Rp ** 2;
  const Hkm = 1.380649e-23 * PB['Teq_K_A0.3_full_redistribution'].median / (2.3 * 1.6726e-27 * g) / 1000;
  const sigH = 2 * (Rp * 6371) * Hkm / (Rs * 695700) ** 2 * 1e6;
  const angStar = 2 * Math.atan(Rs * 0.00465047 / a) * 180 / Math.PI, angSun = 0.533;
  const glow = (svg, cx, cy, r, col, id) => { const defs = el('defs', {}, svg); const gr = el('radialGradient', { id }, defs); el('stop', { offset: '0%', 'stop-color': col, 'stop-opacity': .55 }, gr); el('stop', { offset: '100%', 'stop-color': col, 'stop-opacity': 0 }, gr); el('circle', { cx, cy, r: r * 1.9, fill: `url(#${id})` }, svg); };
  const disc = (svg, cx, cy, r, col, id, shade = true) => { const defs = el('defs', {}, svg); const gr = el('radialGradient', { id, cx: '38%', cy: '35%', r: '75%' }, defs); el('stop', { offset: '0%', 'stop-color': '#ffffff', 'stop-opacity': shade ? .35 : 0 }, gr); el('stop', { offset: '60%', 'stop-color': col, 'stop-opacity': 1 }, gr); el('stop', { offset: '100%', 'stop-color': '#000', 'stop-opacity': shade ? .55 : .25 }, gr); el('circle', { cx, cy, r, fill: col }, svg); el('circle', { cx, cy, r, fill: `url(#${id})` }, svg); };
  // A. star beside the Sun
  {
    const host = document.getElementById('look-star'), w = 520, hh = 300, svg = el('svg', { viewBox: `0 0 ${w} ${hh}`, role: 'img', 'aria-label': 'The host star beside the Sun, to scale' });
    const R = 95, cxS = 150, cxH = 385, cy = 150;
    glow(svg, cxS, cy, R, '#ffe9b8', 'gS'); disc(svg, cxS, cy, R, starColour(5772), 'dS', false);
    glow(svg, cxH, cy, R * Rs, starColour(Teff), 'gH'); disc(svg, cxH, cy, R * Rs, starColour(Teff), 'dH', false);
    el('text', { x: cxS, y: hh - 28, 'text-anchor': 'middle', class: 'cap strong' }, svg).textContent = 'the Sun';
    el('text', { x: cxS, y: hh - 12, 'text-anchor': 'middle', class: 'cap' }, svg).textContent = '5,772 K · 1 R☉ · 1 L☉';
    el('text', { x: cxH, y: hh - 28, 'text-anchor': 'middle', class: 'cap strong' }, svg).textContent = 'KIC 6146676';
    el('text', { x: cxH, y: hh - 12, 'text-anchor': 'middle', class: 'cap' }, svg).textContent = `${fmt(Teff)} K · ${fmt(Rs, 2)} R☉ · ${fmt(L, 3)} L☉`;
    el('text', { x: cxH, y: 30, 'text-anchor': 'middle', class: 'cap' }, svg).textContent = `half the Sun's width, ${fmt(L * 100, 0)}% of its light, mostly red and infrared`;
    host.appendChild(svg);
  }
  // B. planet beside Earth and Neptune
  {
    const host = document.getElementById('look-planet'), w = 520, hh = 300, svg = el('svg', { viewBox: `0 0 ${w} ${hh}`, role: 'img', 'aria-label': 'The candidate planet beside Earth and Neptune, to scale' });
    const unit = 27, cy = 150;
    const items = [[90, 1, '#3f8fd8', 'Earth', '1 R⊕'], [250, Rp, '#7fa8a3', 'KIC 6146676 b (candidate)', `${fmt(Rp, 2)} R⊕ · ~${fmt(Mp, 0)} M⊕ if typical`], [420, 3.88, '#3a5fc8', 'Neptune', '3.88 R⊕ · 17 M⊕']];
    items.forEach(([cx, r, col, name, sub], i) => { disc(svg, cx, cy, r * unit, col, 'dp' + i); if (i === 1) { el('circle', { cx, cy, r: r * unit + 5, fill: 'none', stroke: '#c7d6d3', 'stroke-opacity': .5, 'stroke-width': 6 }, svg); } el('text', { x: cx, y: hh - 28, 'text-anchor': 'middle', class: 'cap strong' }, svg).textContent = name; el('text', { x: cx, y: hh - 12, 'text-anchor': 'middle', class: 'cap' }, svg).textContent = sub; });
    el('text', { x: 250, y: 30, 'text-anchor': 'middle', class: 'cap' }, svg).textContent = 'the pale ring stands for a possible thick envelope; its depth is not known';
    host.appendChild(svg);
  }
  // C. orbit against the habitable zone
  {
    const host = document.getElementById('look-orbit'), w = 520, hh = 420, svg = el('svg', { viewBox: `0 0 ${w} ${hh}`, role: 'img', 'aria-label': 'The orbit drawn against the habitable zone' });
    const cx = w / 2, cy = hh / 2, sc = (Math.min(w, hh) / 2 - 24) / d_em;
    el('circle', { cx, cy, r: d_em * sc, fill: '#2f8f5b', 'fill-opacity': .18 }, svg);
    el('circle', { cx, cy, r: d_mg * sc, fill: '#2f8f5b', 'fill-opacity': .22 }, svg);
    el('circle', { cx, cy, r: d_rg * sc, fill: '#0b1020' }, svg);
    el('circle', { cx, cy, r: d_rg * sc, fill: '#d9a441', 'fill-opacity': .18 }, svg);
    el('circle', { cx, cy, r: d_rv * sc, fill: '#0b1020' }, svg);
    el('circle', { cx, cy, r: a * sc, fill: 'none', stroke: '#f0efec', 'stroke-width': 1.5, 'stroke-dasharray': '3 4' }, svg);
    glow(svg, cx, cy, 10, starColour(Teff), 'gO'); el('circle', { cx, cy, r: 7, fill: starColour(Teff) }, svg);
    const ang = -0.6, px = cx + a * sc * Math.cos(ang), py = cy + a * sc * Math.sin(ang);
    el('circle', { cx: px, cy: py, r: 7, fill: '#0b1020' }, svg); el('circle', { cx: px, cy: py, r: 5, fill: '#9fc3bf' }, svg);
    el('text', { x: px + 10, y: py - 8, class: 'cap strong' }, svg).textContent = `planet, ${fmt(a, 3)} AU`;
    el('text', { x: px + 10, y: py + 8, class: 'cap' }, svg).textContent = `${fmt(insol, 1)}× Earth's sunlight, ${fmt(PB['Teq_K_A0.3_full_redistribution'].median)} K`;
    const lab = (d, t, dy) => { el('text', { x: cx + 6, y: cy - d * sc + dy, class: 'cap' }, svg).textContent = t; };
    lab(d_rv, `optimistic inner edge ${fmt(d_rv, 2)} AU`, -4); lab(d_rg, `conservative inner edge ${fmt(d_rg, 2)} AU`, -4); lab(d_mg, `conservative outer edge ${fmt(d_mg, 2)} AU`, 12); lab(d_em, `optimistic outer edge ${fmt(d_em, 2)} AU`, 12);
    el('text', { x: 10, y: hh - 10, class: 'cap' }, svg).textContent = `for comparison, Mercury orbits the Sun at 0.39 AU; Earth's orbit would be ${fmt(1 / d_em, 1)}× wider than this frame`;
    host.appendChild(svg);
    const leg = h('div', 'legend', `<span><i style="background:#2f8f5b;opacity:.6"></i>habitable zone (lighter: optimistic)</span><span><i style="background:#d9a441;opacity:.5"></i>between the two inner edges</span><span><i class="line" style="background:#f0efec"></i>orbit of the candidate</span>`);
    host.appendChild(leg);
  }
  // D. the star seen from the planet
  {
    const host = document.getElementById('look-sky'), w = 520, hh = 300, svg = el('svg', { viewBox: `0 0 ${w} ${hh}`, role: 'img', 'aria-label': 'Apparent size of the host star from the planet versus the Sun from Earth' });
    const sc = 60, cy = 140;   // px per degree
    glow(svg, 140, cy, angSun / 2 * sc, '#fff3d6', 'gE'); disc(svg, 140, cy, angSun / 2 * sc, '#fff3d6', 'dE', false);
    glow(svg, 380, cy, angStar / 2 * sc, starColour(Teff), 'gP'); disc(svg, 380, cy, angStar / 2 * sc, starColour(Teff), 'dP', false);
    el('text', { x: 140, y: hh - 44, 'text-anchor': 'middle', class: 'cap strong' }, svg).textContent = 'the Sun from Earth';
    el('text', { x: 140, y: hh - 28, 'text-anchor': 'middle', class: 'cap' }, svg).textContent = `${fmt(angSun, 2)}° across`;
    el('text', { x: 380, y: hh - 44, 'text-anchor': 'middle', class: 'cap strong' }, svg).textContent = 'KIC 6146676 from the planet';
    el('text', { x: 380, y: hh - 28, 'text-anchor': 'middle', class: 'cap' }, svg).textContent = `${fmt(angStar, 2)}° across, ${fmt(angStar / angSun, 1)}× wider, orange-red`;
    el('text', { x: 260, y: hh - 8, 'text-anchor': 'middle', class: 'cap' }, svg).textContent = `a year of ${fmt(E.period_days, 1)} days; at 0.18 AU from an M dwarf the planet is probably tidally locked, so one side always faces the star`;
    host.appendChild(svg);
  }
  // E. radius valley strip and inference rows
  {
    const host = document.getElementById('valley'), w = 760, hh = 96, svg = el('svg', { viewBox: `0 0 ${w} ${hh}`, class: 'chart', role: 'img', 'aria-label': 'Where the candidate sits on the planet radius scale' });
    const x = r => 40 + (r - 0.8) / (4.2 - 0.8) * (w - 60), y = 46;
    el('rect', { x: x(0.8), y: y - 14, width: x(1.6) - x(0.8), height: 28, rx: 4, fill: 'var(--s2-wash)' }, svg);
    el('rect', { x: x(1.6), y: y - 14, width: x(2.0) - x(1.6), height: 28, rx: 0, fill: 'var(--surface-2)' }, svg);
    el('rect', { x: x(2.0), y: y - 14, width: x(4.2) - x(2.0), height: 28, rx: 4, fill: 'var(--s1-wash)' }, svg);
    [1, 1.5, 2, 2.5, 3, 3.5, 4].forEach(v => { el('line', { x1: x(v), x2: x(v), y1: y + 14, y2: y + 19, stroke: 'var(--axis)' }, svg); el('text', { x: x(v), y: y + 32, 'text-anchor': 'middle', class: 'axis' }, svg).textContent = v; });
    el('text', { x: x(1.2), y: y + 4, 'text-anchor': 'middle', class: 'lab' }, svg).textContent = 'rocky';
    el('text', { x: x(1.8), y: y + 4, 'text-anchor': 'middle', class: 'lab' }, svg).textContent = 'the valley';
    el('text', { x: x(3.1), y: y + 4, 'text-anchor': 'middle', class: 'lab' }, svg).textContent = 'sub-Neptunes with volatile envelopes';
    el('text', { x: w - 20, y: y + 46, 'text-anchor': 'end', class: 'axis-title' }, svg).textContent = 'planet radius, Earth radii';
    const lo = Rp - PB.Rp_from_k_Rearth.minus, hi = Rp + PB.Rp_from_k_Rearth.plus;
    el('line', { x1: x(lo), x2: x(hi), y1: y - 24, y2: y - 24, stroke: 'var(--ink)', 'stroke-width': 2 }, svg);
    el('circle', { cx: x(Rp), cy: y - 24, r: 6, fill: 'var(--ink)' }, svg);
    el('text', { x: x(Rp), y: y - 34, 'text-anchor': 'middle', class: 'lab strong' }, svg).textContent = `candidate, ${fmt(Rp, 2)} R⊕`;
    host.appendChild(svg);
    const rows = [
      ['Rocky or gassy?', `Almost certainly not a bare rock. Across the Kepler population, planets above about 1.6 to 2 Earth radii (the "radius valley", Fulton et al. 2017; lower for M dwarfs) nearly all carry a volatile envelope: a few percent of hydrogen and helium by mass over a rocky core, or a large water fraction. At ${fmt(Rp, 2)} Earth radii this one sits well on the gassy side.`],
      ['How heavy?', `Not measured. If it follows the population mass-radius relation (Weiss & Marcy 2014), roughly ${fmt(Mp, 0)} Earth masses, with a factor-of-two scatter; a radial-velocity campaign would need to reach about ${fmt(28.4 * Mp / (Ms ** (2 / 3)) * (E.period_days / 365.25) ** (-1 / 3) / 317.8, 1)} m/s on a G 16 star, which is beyond current spectrographs.`],
      ['Can it hold an atmosphere?', `Yes. With that mass the escape velocity is about ${fmt(vesc, 0)} km/s, against a few km/s for thermal hydrogen at ${fmt(PB['Teq_K_A0.3_full_redistribution'].median)} K, and the star's quiet Gaia record and 40-day orbit mean the planet is not strongly irradiated. A hydrogen-rich envelope would survive for billions of years here.`],
      ['What would the air be like?', `Unknown. The two common pictures for a temperate sub-Neptune are a hydrogen-helium envelope over a rocky core, with water clouds possible high up, or a water-rich world with a steam or supercritical layer. The 267 K equilibrium temperature is a top-of-atmosphere number; a thick envelope keeps the deep layers far hotter than anything habitable.`],
      ['Could a telescope read it?', `Not soon. One atmospheric scale height (${fmt(Hkm, 0)} km for a hydrogen envelope) changes the transit depth by only about ${fmt(sigH, 0)} ppm, on a star of magnitude 16. That is out of reach for JWST transmission spectroscopy at any practical observing time; the host's faintness, not the planet, is the limit.`],
      ['Habitable?', `Borderline at best, and only in the sense of location. The orbit receives ${fmt(insol, 1)} times Earth's insolation, between the optimistic and conservative inner edges of the habitable zone for this star. A planet of ${fmt(Rp, 1)} Earth radii is not Earth-like regardless of where it orbits.`],
    ];
    const wrap = document.getElementById('infer-rows');
    rows.forEach(([a_, b_]) => { const r = h('div', 'row'); r.innerHTML = `<b>${a_}</b><span>${b_}</span>`; wrap.appendChild(r); });
  }
}
"""
anchor = "/* ---------- caveats ---------- */"
assert anchor in s; s = s.replace(anchor, js + anchor, 1)
p.write_text(s, encoding="utf-8", newline="\n")
print("look section added")
