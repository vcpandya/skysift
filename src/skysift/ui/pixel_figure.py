"""The pixel stage's picture: one panel per season, drawn from rung 5's stored metrics alone (no pixel data is read), in
archive SEASON order. Each panel places this star (a ring), the neighbour whose pixels hold it (a square) and the
centroid of the light lost in transit (a cross, with circles at 1 and 3 times its error) on the season's own pixel
grid, row 0 at the bottom as the dossier draws it, and names the nearer star with the signed separation in sigma. A
season that did not vote is greyed and says why. Every scale is shared by the panels, so the seasons compare at a
glance. Metrics in, SVG text out.

The signal page draws the figure inline (``theme=None``): it carries no colour of its own, and app.css styles its
``pf-`` classes with the theme tokens, so the dashboard's theme toggle applies. The standalone download
(``/api/signals/{id}/pixels.svg?theme=light|dark``) carries the exact token values of that theme (``PALETTES``, which
``tools/contrast_check.py`` holds against app.css) and its own ground."""
from __future__ import annotations

import math
from html import escape

from skysift.ui.explain import pixel_seasons, season_name, season_votes

PANEL_W, PLOT, GAP, PAD = 230, 200, 12, 8
PLOT_X = (PANEL_W - PLOT) // 2
TEXT_Y = (PLOT + 22, PLOT + 38, PLOT + 54)
PANEL_H = PLOT + 64
MARGIN_PIX = 0.7            # pixels of room around the outermost mark
MIN_HALF_SPAN = 1.5         # the smallest half-width of a panel, in pixels
SIGMA_RINGS = (1.0, 3.0)    # error circles; 3 is the pass threshold (lc.pixels.SEPARATION_SIGMA)
# the app.css tokens the figure uses, and their values in each theme block (the standalone file has no stylesheet)
TOKENS = ("solar", "ion", "fg", "fg-2", "muted", "bg")
PALETTES = {"light": {"solar": "#a25d00", "ion": "#0c7890", "fg": "#0b1020", "fg-2": "#4a5368", "muted": "#7b8499",
                      "bg": "#f5f7fb"},
            "dark": {"solar": "#ffb454", "ion": "#4fd1e8", "fg": "#e8ecf4", "fg-2": "#a7b0c3", "muted": "#6b7489",
                     "bg": "#070b14"}}
THEMES = tuple(PALETTES)
# the figure's rules with <token> placeholders: app.css carries them with var(--token), the standalone file with hex
RULES = (".pf-text{font:11px 'IBM Plex Sans',system-ui,sans-serif;fill:<fg-2>;text-anchor:middle}",
         ".pf-head{font-weight:600;fill:<fg>}",
         ".pf-frame{fill:none;stroke:<muted>}",
         ".pf-grid{stroke:<muted>;stroke-opacity:.35}",
         ".pf-axis{stroke:<muted>;stroke-dasharray:2 4}",
         ".pf-target{fill:none;stroke:<solar>;stroke-width:2.5}",
         ".pf-neighbour{fill:none;stroke:<muted>;stroke-width:2}",
         ".pf-centroid{stroke:<ion>;stroke-width:2;stroke-linecap:round}",
         ".pf-err1{fill:<ion>;fill-opacity:.15;stroke:<ion>}",
         ".pf-err3{fill:none;stroke:<ion>;stroke-dasharray:3 3}",
         ".non-voting .pf-marks{opacity:.45}")


def rules(values: dict[str, str]) -> list[str]:
    """RULES with each <token> replaced by ``values[token]``."""
    out = []
    for rule in RULES:
        for token in TOKENS:
            rule = rule.replace(f"<{token}>", values[token])
        out.append(rule)
    return out


def css_rules() -> list[str]:
    """The rules as app.css carries them, on the theme tokens."""
    return rules({t: f"var(--{t})" for t in TOKENS})


def standalone_style(theme: str) -> str:
    return "<style>" + "".join(rules(PALETTES[theme])) + "</style>"


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


def figure_label(metrics: dict) -> str:
    """The figure's text alternative from the per-season votes: "Pixel check: 3 of 4 archive seasons nearer the
    target; archive season 3 not voting (box incomplete: target)"."""
    seasons = pixel_seasons(metrics)
    if not seasons:
        return "Pixel check: no season to draw"
    parts = []
    for star in ("target", "neighbour"):
        n = sum(1 for s in seasons if season_votes(s) and s.get("nearer") == star)
        if n:
            parts.append(f"{n} of {len(seasons)} archive seasons nearer the {star}")
    parts += [f"{season_name(s)} not voting ({s.get('non_voting_reason') or 'no reason stored'})"
              for s in seasons if not season_votes(s)]
    return "Pixel check: " + ("; ".join(parts) or "no season voted")


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
    name = season_name(s)
    head = name[:1].upper() + name[1:] + (f" · Q{', Q'.join(str(q) for q in quarters)}" if quarters else "")
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


def _panel(i: int, s: dict, m: dict, half: float, ids: str) -> str:
    voting = season_votes(s)
    x0 = PAD + i * (PANEL_W + GAP)
    c = _centre(m) or (0.0, 0.0)
    k = (PLOT / 2) / half

    def X(col: float) -> float:
        return round(PLOT_X + PLOT / 2 + (col - c[0]) * k, 1)

    def Y(row: float) -> float:
        return round(PAD + PLOT / 2 - (row - c[1]) * k, 1)

    clip = f"{ids}-clip-{i}"
    label = s.get("season") if _num(s.get("season")) is not None else "unlabelled"
    head = (f'<g class="season{"" if voting else " non-voting"}" data-season="{escape(str(label))}" '
            f'transform="translate({x0} 0)">')
    parts = [head,
             f'<clipPath id="{clip}"><rect x="{PLOT_X}" y="{PAD}" width="{PLOT}" height="{PLOT}" rx="6"/></clipPath>',
             f'<rect class="pf-frame" x="{PLOT_X}" y="{PAD}" width="{PLOT}" height="{PLOT}" rx="6"/>',
             f'<g class="pf-marks" clip-path="url(#{clip})">']
    for n in range(math.floor(c[0] - half - 0.5), math.ceil(c[0] + half - 0.5) + 1):
        edge = n + 0.5                                    # pixel n spans n - 0.5 to n + 0.5
        parts.append(f'<line class="pf-grid" x1="{X(edge)}" x2="{X(edge)}" y1="{PAD}" y2="{PAD + PLOT}"/>')
    for n in range(math.floor(c[1] - half - 0.5), math.ceil(c[1] + half - 0.5) + 1):
        edge = n + 0.5
        parts.append(f'<line class="pf-grid" x1="{PLOT_X}" x2="{PLOT_X + PLOT}" y1="{Y(edge)}" y2="{Y(edge)}"/>')
    t, nb, ct, sigma = m["target"], m["neighbour"], m["centroid"], m["sigma"]
    if t and nb:
        parts.append(f'<line class="pf-axis" x1="{X(t[0])}" y1="{Y(t[1])}" x2="{X(nb[0])}" y2="{Y(nb[1])}"/>')
    if nb:
        parts.append(f'<rect class="pf-neighbour" x="{X(nb[0]) - 7}" y="{Y(nb[1]) - 7}" width="14" height="14"/>')
    if t:
        parts.append(f'<circle class="pf-target" cx="{X(t[0])}" cy="{Y(t[1])}" r="9"/>')
    if ct:
        cx, cy = X(ct[0]), Y(ct[1])
        if sigma:
            for ring, cls in zip(SIGMA_RINGS, ("pf-err1", "pf-err3"), strict=True):
                parts.append(f'<circle class="{cls}" cx="{cx}" cy="{cy}" r="{_f(max(ring * sigma * k, 1.5))}"/>')
        parts.append(f'<path class="pf-centroid" d="M{_f(cx - 5)} {_f(cy - 5)} L{_f(cx + 5)} {_f(cy + 5)} '
                     f'M{_f(cx - 5)} {_f(cy + 5)} L{_f(cx + 5)} {_f(cy - 5)}"/>')
    parts.append("</g>")
    mid = PANEL_W // 2
    for y, text, cls in zip(TEXT_Y, _words(s), ("pf-text pf-head", "pf-text", "pf-text"), strict=True):
        if text:
            parts.append(f'<text class="{cls}" x="{mid}" y="{y}">{escape(text)}</text>')
    parts.append("</g>")
    return "".join(parts)


def render_svg(metrics: dict, signal_id: int, reason: str = "", theme: str | None = None) -> str:
    """The season panels of rung 5's ``metrics``, or a short note when the row holds no season to draw. With
    ``theme`` None the SVG is for the page (no colour of its own, app.css styles it); with "light" or "dark" it is a
    standalone file in that theme's token values on that theme's ground."""
    if theme is not None and theme not in PALETTES:
        raise ValueError(f"unknown theme {theme!r}; one of {', '.join(THEMES)}")
    seasons = pixel_seasons(metrics)
    label = escape(figure_label(metrics))
    ids = f"pf{signal_id}"
    style = standalone_style(theme) if theme else ""
    if not seasons:
        w, h = 420, 70
        note = escape(reason or "the pixel check stored no season")
        body = (f'<text class="pf-text pf-head" x="{w // 2}" y="30">No season to draw</text>'
                f'<text class="pf-text" x="{w // 2}" y="50">{note}</text>')
    else:
        marks = [_season_marks(s) for s in seasons]
        half = _half_span(marks)
        w = 2 * PAD + len(seasons) * PANEL_W + (len(seasons) - 1) * GAP
        h = PAD + PANEL_H
        body = "".join(_panel(i, s, m, half, ids) for i, (s, m) in enumerate(zip(seasons, marks, strict=True)))
    ground = f'<rect width="{w}" height="{h}" fill="{PALETTES[theme]["bg"]}"/>' if theme else ""
    desc = escape("Ring: this star. Square: the neighbour. Cross: the centroid of the lost light, with circles at 1 "
                  "and 3 times its error. A greyed panel did not vote.")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" class="pf" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{label}"><desc>{desc}</desc>{style}{ground}{body}</svg>')
