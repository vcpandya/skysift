"""Add the star/planet section, the Gaia neighbourhood map, the neighbour leak check and the archive table."""
from pathlib import Path
H = Path(__file__).parent
p = H / "dossier_template.html"; s = p.read_text(encoding="utf-8")

# 1. verdict sentence and hero coords
old = "no planet or binary catalogue lists it. It stays a candidate until independent follow-up confirms it.</p>"
new = ("no planet or binary catalogue lists it. If real, it is a planet of about 2.6 Earth radii on a temperate 40-day orbit "
       "around an M dwarf 303 parsecs away. It stays a candidate until independent follow-up confirms it.</p>")
assert old in s; s = s.replace(old, new)
old = '<span>BP−RP <b class="mono">2.10</b></span>'
new = '<span>BP−RP <b class="mono">2.10</b></span>\n      <span>M1 dwarf · <b class="mono">303 pc</b></span>'
assert old in s; s = s.replace(old, new)

# 2. leak-test check row (after the pixel check, before the archive check)
old = "    ['pass', 'Not already known',"
new = """    ['pass', 'The bright neighbour does not show the dip', (() => { const A = D.context && D.context.A_leak_test; if (!A) return ''; const n = A.neighbour_KIC6146666.pdcsap, x = A.neighbour_expectation; return `KIC 6146666 has its own Kepler light curve. At this ephemeris it shows ${pm(n.depth_ppm, n.err_ppm)} ppm. If the candidate's dip were light leaking from that star, it would have to dim by at least ${fmt(x.leak_case_max_depth_on_target_ppm)} ppm itself.`; })(), (() => { const A = D.context && D.context.A_leak_test; if (!A) return ''; const n = A.neighbour_KIC6146666.pdcsap, x = A.neighbour_expectation; return `${fmt((x.leak_case_max_depth_on_target_ppm - n.depth_ppm) / n.err_ppm, 0)}σ below leak case`; })()],
    ['pass', 'Not already known',"""
assert old in s; s = s.replace(old, new, 1)

# 3. star & planet section
start = s.index("/* ---------- star & planet ---------- */"); end = s.index("/* ---------- caveats ---------- */")
block = r"""/* ---------- star & planet ---------- */
{
  const body = document.getElementById('star-body');
  const C = D.context;
  if (!C) {
    body.innerHTML = `<div class="note">Stellar parameters are being compiled.</div>`;
  } else {
    const S = C.summary.stellar_baseline, PB = C.summary.planet_baseline, dist = C.summary.distance_pc, DS = C.D_stellar_planet, B = C.B_gaia_neighbourhood;
    const q = (o, d = 2) => `${fmt(o.median, d)} <span style="font-size:.8em;color:var(--muted)">+${fmt(o.plus, d)} −${fmt(o.minus, d)}</span>`;
    const L = S.R_star_Rsun.median ** 2 * (S.Teff_K.median / 5772) ** 4, insol = L / PB.a_AU.median ** 2;
    const tiles = h('div', 'tiles');
    [['Host star', 'M1 dwarf', '', `T<sub>eff</sub> ${fmt(S.Teff_K.median)} K · ${fmt(S.R_star_Rsun.median, 2)} R<sub>☉</sub> · ${fmt(S.M_star_Msun.median, 2)} M<sub>☉</sub>`],
     ['Distance', fmt(dist.median), 'pc', `Gaia DR3 parallax ${fmt(+B.candidate_gaia_source.parallax, 2)} ± ${fmt(+B.candidate_gaia_source.parallax_error, 2)} mas`],
     ['Planet radius', fmt(PB.Rp_from_k_Rearth.median, 2), 'R<sub>⊕</sub>', `+${fmt(PB.Rp_from_k_Rearth.plus, 2)} −${fmt(PB.Rp_from_k_Rearth.minus, 2)}; a sub-Neptune, not a rocky world`],
     ['Orbit', fmt(PB.a_AU.median, 3), 'AU', `${fmt(PB.a_AU.median * 149.6, 1)} million km from the star`],
     ['Equilibrium temperature', fmt(PB['Teq_K_A0.3_full_redistribution'].median), 'K', 'albedo 0.3, full heat redistribution (Earth on the same basis: 255 K)'],
     ['Starlight received', fmt(insol, 2), '× Earth', `from L = ${fmt(L, 3)} L<sub>☉</sub> at ${fmt(PB.a_AU.median, 3)} AU`],
    ].forEach(([k, v, u, s_]) => { const t = h('div', 'tile'); t.append(h('div', 'k', k), h('div', 'v num', `${v}${u ? `<small>${u}</small>` : ''}`), h('div', 's', s_)); tiles.appendChild(t); });
    body.appendChild(tiles);
    const prose = h('div', 'prose'); prose.style.marginTop = '18px';
    prose.innerHTML = `<p><b>What kind of star.</b> Gaia DR3 measures a parallax of ${fmt(+B.candidate_gaia_source.parallax, 3)} ± ${fmt(+B.candidate_gaia_source.parallax_error, 3)} mas (RUWE ${fmt(+B.candidate_gaia_source.ruwe, 2)}, no astrometric excess noise), so the star is ${fmt(dist.median)} pc away and its colour of BP−RP 2.10 with absolute magnitude M<sub>G</sub> = ${fmt(DS.abs_G_mag_noext, 2)} places it on the main sequence as an M1 dwarf. Three independent routes agree on its size: the Mamajek colour table (${fmt(S.R_star_Rsun.median, 3)} R<sub>☉</sub>), the Mann et al. K-band relations (${fmt(DS.mann_MKs_check.R_Mann2015_Rsun.median, 3)} R<sub>☉</sub>) and the TESS Input Catalog (${fmt(+DS.tic_v82.R, 3)} R<sub>☉</sub>, ${fmt(+DS.tic_v82.Teff)} K).</p>
      <p><b>What the planet would be.</b> The radius ratio of ${fmt(F.k, 3)} on a ${fmt(S.R_star_Rsun.median, 2)} R<sub>☉</sub> star gives ${fmt(PB.Rp_from_k_Rearth.median, 2)} Earth radii. Kepler's third law with the stellar mass puts the orbit at ${fmt(PB.a_AU.median, 3)} AU, where the planet receives about ${fmt(insol, 1)} times the starlight Earth does and would have an equilibrium temperature near ${fmt(PB['Teq_K_A0.3_full_redistribution'].median)} K. That is close to the inner edge of the classical habitable zone, although a planet of this size most likely carries a thick volatile envelope.</p>
      <p><b>The duration fits.</b> A central transit of such a star at this period lasts ${fmt(PB.T14_central_h.median, 1)} h; the fitted duration is ${fmt(F.t14_h, 1)} h, consistent with a low to moderate impact parameter. The light-curve fit on its own prefers b = ${fmt(F.b, 2)} with a/R<sub>★</sub> = ${fmt(F.a_over_r, 0)}, but those two parameters are degenerate at this signal-to-noise; the stellar density from Gaia implies a/R<sub>★</sub> = ${fmt(PB.a_over_Rstar.median, 0)}, which the grazing solution cannot match.</p>`;
    body.appendChild(prose);
    // neighbourhood map
    const fig = h('div', 'fig'); fig.style.marginTop = '14px';
    fig.innerHTML = `<h3>The neighbourhood, 30″ around the candidate</h3><div class="sub">Every Gaia DR3 source within 30″ (7.5 Kepler pixels). Marker area scales with brightness. The dashed circle is one Kepler pixel; anything inside it cannot be separated by the pixel test. ${B.n_neighbours_30arcsec} sources; ${B.neighbours_able_to_mimic_by_magnitude.length} are faint enough that, in principle, a deep eclipse on them could mimic the dip, which is why the pixel localisation above matters.</div>`;
    const w = 520, hh = 520, cx = w / 2, cy = hh / 2, sc = (w / 2 - 30) / 30;   // px per arcsec
    const svg = el('svg', { viewBox: `0 0 ${w} ${hh}`, class: 'chart', style: 'max-width:520px;margin:8px auto 0' });
    [10, 20, 30].forEach(r => { el('circle', { cx, cy, r: r * sc, fill: 'none', stroke: 'var(--grid)' }, svg); el('text', { x: cx + 4, y: cy - r * sc - 4, class: 'axis' }, svg).textContent = r + '″'; });
    el('circle', { cx, cy, r: D.pixels.arcsec_per_pixel * sc, fill: 'none', stroke: 'var(--s2)', 'stroke-dasharray': '4 3', 'stroke-width': 1.5 }, svg);
    el('text', { x: cx + D.pixels.arcsec_per_pixel * sc + 4, y: cy + 4, class: 'lab' }, svg).textContent = '1 Kepler pixel';
    el('text', { x: cx, y: 16, 'text-anchor': 'middle', class: 'axis' }, svg).textContent = 'N ↑';
    el('text', { x: 14, y: cy + 4, class: 'axis' }, svg).textContent = 'E';
    const cosd = Math.cos(C.target.dec * Math.PI / 180);
    const pts = [];
    B.neighbours.forEach(n => {
      const dx = -(n.ra - C.target.ra) * 3600 * cosd, dy = (n.dec - C.target.dec) * 3600;   // east to the left
      const r = Math.max(3, 16 - (n.G - 12) * 1.6);
      const isT = n.source_id === '2073896190925687296';
      el('circle', { cx: cx + dx * sc, cy: cy - dy * sc, r: r + 2, fill: 'var(--surface)' }, svg);
      el('circle', { cx: cx + dx * sc, cy: cy - dy * sc, r, fill: isT ? 'var(--s4)' : 'var(--muted)' }, svg);
      if (isT) el('text', { x: cx + dx * sc, y: cy - dy * sc - r - 6, 'text-anchor': 'middle', class: 'lab strong' }, svg).textContent = 'KIC 6146666, G 12.8';
      pts.push({ x: dx, y: dy, n });
    });
    el('circle', { cx, cy, r: 11, fill: 'var(--surface)' }, svg); el('circle', { cx, cy, r: 9, fill: 'var(--s2)' }, svg);
    el('text', { x: cx, y: cy + 26, 'text-anchor': 'middle', class: 'lab strong' }, svg).textContent = 'KIC 6146676, G 16.2';
    fig.appendChild(svg);
    const leg = h('div', 'legend', `<span><i style="background:var(--s2)"></i>candidate</span><span><i style="background:var(--s4)"></i>Kepler target</span><span><i style="background:var(--muted)"></i>other Gaia sources, sized by G</span>`);
    fig.appendChild(leg);
    body.appendChild(fig);
    // hover on the map
    const c = { svg, sx: v => cx + v * sc, sy: v => cy - v * sc, w, h: hh, m: { l: 0, r: 0, t: 0, b: 0 } };
    hover(c, fig, pts, p => `<b>Gaia DR3 ${p.n.source_id}</b>${p.n.kic ? ' · KIC ' + p.n.kic : ''}<br>G ${fmt(p.n.G, 2)} · ${fmt(p.n.sep_arcsec, 1)}″ (${fmt(p.n.sep_kepler_px, 1)} px)<br>BP−RP ${p.n.bp_rp == null ? '–' : fmt(p.n.bp_rp, 2)} · parallax ${fmt(p.n.parallax, 2)} mas${p.n.required_eclipse_depth_frac != null ? '<br>would need a ' + fmt(p.n.required_eclipse_depth_frac * 100, 1) + '% eclipse to mimic' : ''}`);
    // expert: stellar sources kv + archive table
    const kv = h('div', 'kv x'); kv.style.marginTop = '14px';
    [['Gaia DR3 G / BP−RP', `${fmt(DS.inputs.G, 3)} / ${fmt(DS.inputs.BP_RP, 3)}`], ['2MASS Ks', `${fmt(DS.inputs.Ks_2MASS[0], 3)} ± ${fmt(DS.inputs.Ks_2MASS[1], 3)}`],
     ['Parallax (zero-point corrected)', `${fmt(DS.inputs.parallax_corrected_mas, 4)} ± ${fmt(DS.inputs.parallax_err_used_mas, 4)} mas`], ['Distance', `${q(dist, 1)} pc`],
     ['M<sub>G</sub>, M<sub>Ks</sub> (no extinction)', `${fmt(DS.abs_G_mag_noext, 2)}, ${fmt(DS.abs_Ks_mag_noext, 2)}`],
     ['Teff (Mamajek, BP−RP 2.10 ± 0.10)', `${q(S.Teff_K, 0)} K`], ['R★', `${q(S.R_star_Rsun, 3)} R☉`], ['M★', `${q(S.M_star_Msun, 3)} M☉`], ['ρ★', `${q(DS['planet_baseline_colour_2.10pm0.10'].rho_star_gcc, 2)} g cm⁻³`],
     ['Mann 2015 / 2019 from M<sub>Ks</sub>', `${fmt(DS.mann_MKs_check.R_Mann2015_Rsun.median, 3)} R☉, ${fmt(DS.mann_MKs_check.M_Mann2019_Msun.median, 3)} M☉`],
     ['TIC v8.2', `${DS.tic_v82.Teff} ± ${DS.tic_v82.Teff_e} K, ${fmt(+DS.tic_v82.R, 3)} R☉, ${fmt(+DS.tic_v82.M, 3)} M☉, ${fmt(+DS.tic_v82.dist_pc, 0)} pc`],
     ['Gaia Apsis (GSP-Phot)', `${fmt(+DS.gaia_dr3_apsis.teff_gspphot, 0)} K, ${fmt(+DS.gaia_dr3_apsis.radius_gspphot, 3)} R☉, E(BP−RP) ${DS.gaia_dr3_apsis.ebpminrp_gspphot}`],
     ['Extinction (SF11, scaled to 303 pc)', `E(B−V) ≈ ${fmt(DS.extinction.SF11_scaled_to_distance_h125pc, 2)}`],
     ['a / R★ (from ρ★ and P)', q(PB.a_over_Rstar, 1)], ['Transit probability R★/a', q(PB.transit_probability_Rstar_over_a, 4)], ['T14, central transit', `${q(PB.T14_central_h, 2)} h`],
     ['R<sub>p</sub> from k', `${q(PB.Rp_from_k_Rearth, 2)} R⊕`], ['R<sub>p</sub> from box depth', `${q(PB.Rp_from_boxdepth_Rearth, 2)} R⊕`],
     ['Gaia RUWE / non-single-star flag', `${fmt(+B.candidate_gaia_source.ruwe, 3)} / ${B.candidate_gaia_source.non_single_star}`], ['Gaia multi-peak fraction', B.candidate_gaia_source.ipd_frac_multi_peak],
    ].forEach(([a, b]) => { const d = h('div'); d.innerHTML = `<span>${a}</span><b>${b}</b>`; kv.appendChild(d); });
    body.appendChild(kv);
    const AR = C.C_archives, labels = {
      C_nea_cumulative_box: 'NASA Exoplanet Archive, cumulative KOI table (box)', C_nea_cumulative_kepid: 'cumulative KOI table (by KIC id)', C_nea_dr25tce_box: 'Q1–Q17 DR25 TCEs (box)', C_nea_dr25tce_kepid: 'DR25 TCEs (by KIC id)', C_nea_dr24tce_kepid: 'DR24 TCEs', C_nea_q1q16tce_kepid: 'Q1–Q16 TCEs', C_nea_q1q12tce_kepid: 'Q1–Q12 TCEs', C_nea_toi_box: 'TESS Objects of Interest (box)', C_nea_keplerstellar_kepid: 'Kepler stellar table', C_vizier_keplerEB_kirk2016: 'Villanova Kepler EB catalogue (Kirk 2016)', C_vizier_KIC: 'Kepler Input Catalog (VizieR)', C_vizier_TICv82: 'TIC v8.2 (VizieR)', C_vizier_planethunters_schmitt2014: 'Planet Hunters (Schmitt 2014)', C_vizier_planethunters_schwamb2013: 'Planet Hunters (Schwamb 2013)', C_vizier_kepler_fpp_morton2016: 'Kepler false-positive probabilities (Morton 2016)', C_simbad_cone30: 'SIMBAD, 30″ cone', C_nea_dr25stellar_kepid: 'DR25 stellar table', C_nea_dr25stellar_kepid_retry: 'DR25 stellar table (retry)', C_nea_fpwg_kepid: 'Kepler false-positive working group', exofop_TIC169822650: 'ExoFOP, TIC 169822650',
    };
    const meaning = { C_nea_keplerstellar_kepid: 'stellar rows only, no planet', C_vizier_KIC: 'catalogue entries only', C_vizier_TICv82: 'catalogue entries only' };
    const rows = Object.entries(AR).filter(([k]) => labels[k]).map(([k, v]) => {
      let res;
      if (k === 'exofop_TIC169822650') res = `${v.tois.length} TOIs, ${v.ctois.length} CTOIs, ${v.imaging.length} imaging, ${v.spectroscopy.length} spectra`;
      else if (v.error) res = 'query failed (' + v.error.split(' ')[0] + ')';
      else if (v.rows === null) res = k === 'C_simbad_cone30' ? '1 object: 2MASS J19512748+4127178, no classification' : 'no rows';
      else res = v.rows === 0 ? 'no match' : `${v.rows} rows${meaning[k] ? ', ' + meaning[k] : ''}`;
      return `<tr><td>${labels[k]}</td><td>${res}</td><td><a href="${v.url}" target="_blank" rel="noopener">query</a></td></tr>`;
    }).join('');
    const tbl = h('div', 'tbl x'); tbl.style.marginTop = '14px';
    tbl.innerHTML = `<table><caption>Archive checks (expert)</caption><thead><tr><th>Catalogue</th><th>Result</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
    body.appendChild(tbl);
    const note = h('div', 'note x'); note.style.marginTop = '14px';
    note.innerHTML = `<b>Expert notes.</b> The KBonus SAP depth (${pm(C.summary.candidate_sap_depth_ppm[0], C.summary.candidate_sap_depth_ppm[1])} ppm) is ${fmt(C.summary.candidate_sap_over_psf[0], 2)} of the PSF depth (${pm(C.summary.candidate_psf_depth_ppm[0], C.summary.candidate_psf_depth_ppm[1])} ppm): the aperture sum includes light from KIC 6146666 that the PSF fit removes, so the shallower SAP depth is dilution, not a contradiction. The Kepler Input Catalog lists KIC 6146680 (Kp 18.9) 4.0″ from the candidate with no Gaia or 2MASS counterpart within 3″; the nearest Gaia source is G 19.8 at 7.1″, so the KIC entry is most likely that star with poor astrometry, or spurious. The candidate's own KBonus centroid columns could not be used for a centroid test because they track the pointing of the whole stamp (they are identical to those of KIC 6146666 to 10⁻⁶ pixel).`;
    body.appendChild(note);
  }
}
"""
s = s[:start] + block + s[end:]
p.write_text(s, encoding="utf-8", newline="\n")

# 4. prep_data: keep only the needed parts of context
q = H / "prep_data.py"; t = q.read_text(encoding="utf-8")
old = """for name in ("localisation", "context"):
    p = H / name / "results.json"
    if p.exists():
        out[name] = json.load(open(p, encoding="utf-8"))"""
new = """for name in ("localisation", "context"):
    p = H / name / "results.json"
    if p.exists():
        out[name] = json.load(open(p, encoding="utf-8"))
if "context" in out:
    C = out["context"]
    C.pop("A4_centroid_test_INVALID", None); C.pop("A3_neighbour_own_lightcurves", None)
    C["A_leak_test"] = {k: C["A_leak_test"][k] for k in ("neighbour_KIC6146666", "neighbour_expectation", "candidate_KIC6146676")}
    C["A_leak_test"]["neighbour_KIC6146666"] = {k: C["A_leak_test"]["neighbour_KIC6146666"][k] for k in ("pdcsap", "sap", "n_cadences")}
    C["A_leak_test"]["candidate_KIC6146676"] = {k: v for k, v in C["A_leak_test"]["candidate_KIC6146676"].items() if k != "per_quarter_header"}
    C["B_gaia_neighbourhood"].pop("gaia_variability_and_nss", None)
    for k, v in C["C_archives"].items():
        if isinstance(v, dict): v.pop("file", None)
    C["D_stellar_planet"].pop("mamajek_rows_used", None)"""
assert old in t; q.write_text(t.replace(old, new), encoding="utf-8", newline="\n")
print("patched")
