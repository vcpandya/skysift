"""The pixel stage's picture on the signal page: one panel per season, drawn from rung 5's stored metrics alone (no pixel
data is read), in archive SEASON order. Each panel places this star (a ring), the neighbour whose pixels hold it (a
square) and the centroid of the light lost in transit (a cross, with circles at 1 and 3 times its error) on the season's
own pixel grid, row 0 at the bottom as the dossier draws it, and names the nearer star with the signed separation in
sigma. A season that did not vote is greyed and says why. Metrics in, SVG text out; the route
(``/api/signals/{id}/pixels.svg``) reads the row.

The picture is served as an ``<img>``, which cannot read the page's custom properties, so the colours are fixed values
that read on both themes (the text follows the system scheme through the picture's own media query); every scale is
shared by the panels, so the seasons can be compared at a glance."""
from __future__ import annotations

import math
from html import escape

from skysift.ui.explain import pixel_seasons, season_votes

PANEL_W, PLOT, GAP, PAD = 230, 200, 12, 8
PLOT_X = (PANEL_W - PLOT) // 2
TEXT_Y = (PLOT + 22, PLOT + 38, PLOT + 54)
PANEL_H = PLOT + 64
MARGIN_PIX = 0.7            # pixels of room around the outermost mark
MIN_HALF_SPAN = 1.5         # the smallest half-width of a panel, in pixels
SIGMA_RINGS = (1.0, 3.0)    # error circles; 3 is the pass threshold (lc.pixels.SEPARATION_SIGMA)
STYLE = ("<style>"
         ".t{font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:11px;fill:#4a5368;text-anchor:middle}"
         ".h{font-weight:600}"
         ".bg{fill:#8a93a8;fill-opacity:.06;stroke:#8a93a8;stroke-opacity:.45}"
         ".grid{stroke:#8a93a8;stroke-opacity:.28}"
         ".axis{stroke:#8a93a8;stroke-dasharray:2 4}"
         ".target{fill:none;stroke:#e09a2d;stroke-width:2.5}"
         ".neighbour{fill:none;stroke:#7b8499;stroke-width:2}"
         ".centroid{stroke:#1aa6c4;stroke-width:2;stroke-linecap:round}"
         ".err1{fill:#1aa6c4;fill-opacity:.15;stroke:#1aa6c4}"
         ".err3{fill:none;stroke:#1aa6c4;stroke-dasharray:3 3}"
         ".non-voting .marks{opacity:.4}.non-voting .t{fill:#7b8499}"
         "@media (prefers-color-scheme:dark){.t{fill:#c3cad8}.non-voting .t{fill:#8a93a8}}"
         "</style>")


def _num(x) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x) else None


def _point(v) -> tuple[float, float] | None:
    """A stored (col, row) pair, or None when either is missing."""
    if not isinstance(v, list | tuple) or len(v) != 2:
        return None
    col, row = _num(v[0]), _num(v[1])
    return None if col is None or row is None else (col, row)


def _f(x: float) -> str:
    return f"{x:.1f}"


def _season_marks(s: dict) -> dict:
    return {"target": _point(s.get("pos_target")), "neighbour": _point(s.get("pos_neighbour")),
            "centroid": _point(s.get("centroid_colrow")), "sigma": _num(s.get("centroid_sigma_pix"))}


def _centre(m: dict) -> tuple[float, float] | None:
    """The panel's centre: halfway between the two stars, else whichever marks there are."""
    pts = [p for p in (m["target"], m["neighbour"]) if p] or ([m["centroid"]] if m["centroid"] else [])
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _half_span(marks: list[dict]) -> float:
    """The half-width in pixels every panel shows: room for each panel's farthest mark and 3-sigma circle."""
    span = MIN_HALF_SPAN
    for m in marks:
        c = _centre(m)
        if c is None:
            continue
        for key in ("target", "neighbour", "centroid"):
            p = m[key]
            if p is None:
                continue
            extra = SIGMA_RINGS[-1] * m["sigma"] if key == "centroid" and m["sigma"] else 0.0
            span = max(span, max(abs(p[0] - c[0]), abs(p[1] - c[1])) + extra + MARGIN_PIX)
    return span


def _words(s: dict) -> tuple[str, str, str]:
    """The panel's three lines: the season and its quarters, the vote, and the two distances."""
    quarters = [int(q) for q in s.get("quarters") or [] if _num(q) is not None]
    head = f"Archive season {s.get('season')}" + (f" · Q{', Q'.join(str(q) for q in quarters)}" if quarters else "")
    sep = _num(s.get("separation_sigma"))
    if not season_votes(s):
        vote = f"not voting: {s.get('non_voting_reason') or 'no reason stored'}"
    elif s.get("nearer") in ("target", "neighbour"):
        vote = f"nearer {s['nearer']}" + (f", {sep:+.1f} sigma" if sep is not None else "")
    else:
        vote = "voted, no nearer star stored"
    d_t, d_n = _num(s.get("dist_target_pix")), _num(s.get("dist_neighbour_pix"))
    dist = " · ".join(x for x in (d_t is not None and f"this star {d_t:.2f} px",
                                  d_n is not None and f"neighbour {d_n:.2f} px") if x)
    return head, vote, dist


def _panel(i: int, s: dict, m: dict, half: float) -> str:
    voting = season_votes(s)
    x0 = PAD + i * (PANEL_W + GAP)
    c = _centre(m) or (0.0, 0.0)
    k = (PLOT / 2) / half

    def X(col: float) -> float:
        return round(PLOT_X + PLOT / 2 + (col - c[0]) * k, 1)

    def Y(row: float) -> float:
        return round(PAD + PLOT / 2 - (row - c[1]) * k, 1)

    clip = f"clip-{i}"
    head = (f'<g class="season{"" if voting else " non-voting"}" data-season="{escape(str(s.get("season")))}" '
            f'transform="translate({x0} 0)">')
    parts = [head,
             f'<clipPath id="{clip}"><rect x="{PLOT_X}" y="{PAD}" width="{PLOT}" height="{PLOT}" rx="6"/></clipPath>',
             f'<rect class="bg" x="{PLOT_X}" y="{PAD}" width="{PLOT}" height="{PLOT}" rx="6"/>',
             f'<g class="marks" clip-path="url(#{clip})">']
    for n in range(math.floor(c[0] - half - 0.5), math.ceil(c[0] + half - 0.5) + 1):
        edge = n + 0.5                                    # pixel n spans n - 0.5 to n + 0.5
        parts.append(f'<line class="grid" x1="{X(edge)}" x2="{X(edge)}" y1="{PAD}" y2="{PAD + PLOT}"/>')
    for n in range(math.floor(c[1] - half - 0.5), math.ceil(c[1] + half - 0.5) + 1):
        edge = n + 0.5
        parts.append(f'<line class="grid" x1="{PLOT_X}" x2="{PLOT_X + PLOT}" y1="{Y(edge)}" y2="{Y(edge)}"/>')
    t, nb, ct, sigma = m["target"], m["neighbour"], m["centroid"], m["sigma"]
    if t and nb:
        parts.append(f'<line class="axis" x1="{X(t[0])}" y1="{Y(t[1])}" x2="{X(nb[0])}" y2="{Y(nb[1])}"/>')
    if nb:
        parts.append(f'<rect class="neighbour" x="{X(nb[0]) - 7}" y="{Y(nb[1]) - 7}" width="14" height="14"/>')
    if t:
        parts.append(f'<circle class="target" cx="{X(t[0])}" cy="{Y(t[1])}" r="9"/>')
    if ct:
        cx, cy = X(ct[0]), Y(ct[1])
        if sigma:
            for ring, cls in zip(SIGMA_RINGS, ("err1", "err3"), strict=True):
                parts.append(f'<circle class="{cls}" cx="{cx}" cy="{cy}" r="{_f(max(ring * sigma * k, 1.5))}"/>')
        parts.append(f'<path class="centroid" d="M{_f(cx - 5)} {_f(cy - 5)} L{_f(cx + 5)} {_f(cy + 5)} '
                     f'M{_f(cx - 5)} {_f(cy + 5)} L{_f(cx + 5)} {_f(cy - 5)}"/>')
    parts.append("</g>")
    mid = PANEL_W // 2
    for y, text, cls in zip(TEXT_Y, _words(s), ("t h", "t", "t"), strict=True):
        if text:
            parts.append(f'<text class="{cls}" x="{mid}" y="{y}">{escape(text)}</text>')
    parts.append("</g>")
    return "".join(parts)


def render_svg(metrics: dict, signal_id: int, reason: str = "") -> str:
    """The season panels of rung 5's ``metrics``, or a short note when the row holds no season to draw."""
    seasons = pixel_seasons(metrics)
    label = escape(f"Where the lost light sits in each season for signal {signal_id}")
    if not seasons:
        w, h = 420, 70
        note = escape(reason or "the pixel check stored no season")
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
                f'aria-label="{label}">{STYLE}<text class="t h" x="{w // 2}" y="30">No season to draw</text>'
                f'<text class="t" x="{w // 2}" y="50">{note}</text></svg>')
    marks = [_season_marks(s) for s in seasons]
    half = _half_span(marks)
    w = 2 * PAD + len(seasons) * PANEL_W + (len(seasons) - 1) * GAP
    h = PAD + PANEL_H
    panels = "".join(_panel(i, s, m, half) for i, (s, m) in enumerate(zip(seasons, marks, strict=True)))
    desc = escape("Ring: this star. Square: the neighbour. Cross: the centroid of the lost light, with circles at 1 "
                  "and 3 times its error. A greyed panel did not vote.")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{label}"><desc>{desc}</desc>{STYLE}{panels}</svg>')
