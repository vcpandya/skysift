/* SkySift dossier, minimal (dossiers plan Task 4; Task 5 replaces it). Draws the eight sections from the JSON in
   #dossier-data: the summary, the four signal tiles, the two probabilities with what they do not cover, the checks,
   the caveats and the reproduction commands, with a Normal / Expert toggle that sets data-level (and data-mode, so
   the dashboard's .only-expert rule applies) on the root element. Text only through textContent. */
(function () {
  "use strict";
  var holder = document.getElementById("dossier-data");
  var app = document.getElementById("dossier");
  if (!holder || !app) return;
  var D = JSON.parse(holder.textContent);
  var root = document.documentElement;

  function h(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }
  function num(x, digits) {
    if (x === null || x === undefined || !isFinite(x)) return "unknown";
    return Number(x).toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  }
  function val(v, digits, unit) {
    if (!v || v.median === undefined) return "unknown";
    return num(v.median, digits) + " (+" + num(v.plus, digits) + " / -" + num(v.minus, digits) + ")" + (unit || "");
  }

  var toggle = h("div", "dossier-toggle");
  toggle.setAttribute("role", "group");
  toggle.setAttribute("aria-label", "Level of detail");
  var buttons = {};
  [["normal", "Normal"], ["expert", "Expert"]].forEach(function (pair) {
    var b = h("button", null, pair[1]);
    b.type = "button";
    b.addEventListener("click", function () { setLevel(pair[0]); });
    buttons[pair[0]] = b;
    toggle.appendChild(b);
  });
  function setLevel(level) {
    root.setAttribute("data-level", level);
    root.setAttribute("data-mode", level === "expert" ? "expert" : "simple");
    Object.keys(buttons).forEach(function (k) { buttons[k].setAttribute("aria-pressed", String(k === level)); });
  }
  setLevel(root.getAttribute("data-level") === "expert" ? "expert" : "normal");

  var head = h("header", "dossier-head");
  var titles = h("div");
  titles.appendChild(h("h1", null, (D.target && D.target.name) || "Dossier"));
  titles.appendChild(h("p", "dossier-status", (D.status && (D.status.headline || D.status.label)) || ""));
  head.appendChild(titles);
  head.appendChild(toggle);
  app.appendChild(head);

  var sections = (D.sections || []).map(function (s) {
    var sec = h("section", "dossier-section");
    sec.id = "section-" + s.number;
    var h2 = h("h2");
    h2.appendChild(h("span", "num", s.number));
    h2.appendChild(document.createTextNode(s.title));
    sec.appendChild(h2);
    app.appendChild(sec);
    return sec;
  });
  function section(i) { return sections[i] || app; }

  /* 01 The signal */
  var eph = D.ephemeris || {};
  section(0).appendChild(h("p", null, D.summary));
  var tiles = h("div", "dossier-tiles");
  [["Period", num(eph.period_days, 4) + " days"], ["Depth", num(eph.depth_ppm, 0) + " ppm"],
   ["Duration", num(eph.duration_h, 2) + " hours"], ["SDE", num(eph.sde, 1)]].forEach(function (t) {
    var tile = h("div", "dossier-tile");
    tile.appendChild(h("span", "label", t[0]));
    tile.appendChild(h("span", "value", t[1]));
    tiles.appendChild(tile);
  });
  section(0).appendChild(tiles);

  /* 02 Does it repeat */
  var events = (D.plots && D.plots.events) || [];
  section(1).appendChild(h("p", null, events.length + " transits with data were measured one by one."));

  /* 03 The checks */
  var why = h("ul", "dossier-list");
  (D.why || []).forEach(function (w) {
    var li = h("li");
    li.appendChild(h("b", null, w.name + ": " + w.status));
    li.appendChild(h("span", null, w.sentence));
    if (w.numbers) li.appendChild(h("span", "only-expert", " (" + w.numbers + ")"));
    why.appendChild(li);
  });
  section(2).appendChild(why);
  var fap = D.fap || {}, fpp = D.fpp || {};
  [["False alarm", fap.words, fap.sentence_not_covered,
    fap.n !== undefined ? fap.k + " of " + num(fap.n, 0) + " null searches reached SDE " + num(fap.sde, 1) : ""],
   [fpp.label || "False positive", fpp.words + (fpp.signed_off ? "" : " (provisional)"), fpp.sentence_not_covered,
    "FPP " + num(fpp.value, 4) + ", confusion radius " + num(fpp.confusion_radius_arcsec, 2) + " arcsec"]
  ].forEach(function (p) {
    var box = h("div", "dossier-prob");
    var line = h("p");
    line.appendChild(h("b", null, p[0] + " "));
    line.appendChild(document.createTextNode(p[1] || "unknown"));
    box.appendChild(line);
    box.appendChild(h("p", "not-covered", p[2]));
    if (p[3]) box.appendChild(h("p", "only-expert", p[3]));
    section(2).appendChild(box);
  });

  /* 04 Which star is dimming */
  var px = D.pixels;
  section(3).appendChild(h("p", null, px ? "Difference images in " + px.length + " seasons: the dip is nearer the " +
    "target in " + px.filter(function (s) { return s.nearer === "target"; }).length + " of them."
    : "The pixel check has not produced difference images for this signal."));

  /* 05 The star and the planet */
  var star = D.star || {}, planet = D.planet || {};
  var kv = h("ul", "dossier-list");
  [["Spectral type", star.spectral_type || "unknown"], ["Temperature", val(star.teff, 0, " K")],
   ["Radius", val(star.radius, 3, " Rsun")], ["Mass", val(star.mass, 3, " Msun")],
   ["Distance", val(star.distance_pc, 0, " pc")], ["Planet radius", val(planet.rp_rearth, 2, " Earth radii")],
   ["Orbit", val(planet.a_au, 3, " AU")], ["Equilibrium temperature", val(planet.teq_k, 0, " K")]
  ].forEach(function (r) {
    var li = h("li");
    li.appendChild(h("b", null, r[0]));
    li.appendChild(h("span", null, r[1]));
    kv.appendChild(li);
  });
  section(4).appendChild(kv);

  /* 06 How it might look */
  var look = D.look;
  section(5).appendChild(h("p", null, look ? "A " + look.star_colour + " star; from the planet it would look " +
    num(look.angular_size_sun, 1) + " times as wide as the Sun from Earth."
    : "The star could not be characterized, so the panel is not drawn."));

  /* 07 What is not yet known */
  var cav = h("ul", "dossier-list");
  (D.caveats || []).forEach(function (c) {
    var li = h("li");
    li.appendChild(h("b", null, c.title));
    li.appendChild(h("span", null, c.text));
    cav.appendChild(li);
  });
  section(6).appendChild(cav);

  /* 08 How to reproduce */
  var prov = D.provenance || {};
  section(7).appendChild(h("pre", null, (prov.commands || []).join("\n")));
  section(7).appendChild(h("p", null, "Search version " + prov.search_version + ", ladder version " +
    prov.ladder_version + ", commit " + prov.git_commit + ", " + (prov.queries || []).length + " logged queries."));
})();
