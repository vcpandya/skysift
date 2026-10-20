"""The transit sparkline of a candidate card: the folded light curve within two durations of mid-transit, binned, with
the transit model on top, drawn as a 240 x 72 SVG. Fold points in, SVG text out; loading the curve and caching the
picture belong to the route (``/api/signals/{id}/sparkline.svg``).

The image is served as an ``<img>``, which cannot read the page's custom properties, so the colours are fixed hex
values chosen to read on both themes at this size: the binned points in ion, the model in solar (spec section 3,
"Marks")."""
from __future__ import annotations

import math

import numpy as np

from skysift.lc.transit_model import uniform_disc_flux
from skysift.models import LightCurve
from skysift.ui.plots import _bin, _fold_hours, whitened_fnorm

WIDTH, HEIGHT, PAD = 240, 72, 6
WINDOW_DURATIONS = 2.0      # the fold spans this many transit durations either side of mid-transit
N_BINS = 48                 # bins across the whole window
MODEL_POINTS = 121
ION, SOLAR, MUTED = "#4fd1e8", "#ffb454", "#8a93a8"
DOT_RADIUS, MODEL_WIDTH = 2.2, 1.5
_SVG_OPEN = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" '
             f'height="{HEIGHT}">')


def _number(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _shape_metrics(tests: list[dict]) -> dict:
    """Rung 2's latest metrics (tests come ordered by id), or nothing."""
    rows = [t for t in tests if t.get("rung") == "rung2_shape"]
    return (rows[-1].get("metrics") or {}) if rows else {}


def _fit_model(hours: np.ndarray, period: float, duration: float, m: dict) -> np.ndarray | None:
    """The uniform-disc transit of rung 2's fit in ppm at ``hours`` from mid-transit, or None without a usable fit. A
    fit stored without its orbit size takes the one that gives the signal's duration."""
    k, b = _number(m.get("fit_k")), _number(m.get("fit_b"))
    if k is None or b is None or k <= 0 or b < 0:
        return None
    a = _number(m.get("fit_a_over_r"))
    if a is None:
        inner = (1.0 + k) ** 2 - b * b
        s = math.sin(math.pi * duration / period)
        if inner <= 0 or s <= 0:
            return None
        a = math.sqrt(inner) / s
    if a <= 1.0:
        return None
    return (uniform_disc_flux(hours / 24.0, period, k, b, a) - 1.0) * 1e6


def fold_points(lc: LightCurve, signal: dict, tests: list[dict]) -> dict:
    """The binned fold of the whitened flux within ``WINDOW_DURATIONS`` durations of mid-transit, in ppm about the
    out-of-transit median, and the model line: rung 2's transit fit when it has one, else the detector's box."""
    period, t0, dur = float(signal["period"]), float(signal["t0"]), float(signal["duration"])
    depth = float(signal["depth_ppm"])
    f = whitened_fnorm(lc, signal)
    good = np.isfinite(f) & np.isfinite(lc.time)
    window = WINDOW_DURATIONS * dur * 24.0
    ph = _fold_hours(lc.time[good], period, t0)
    near = np.abs(ph) <= window
    flux = f[good][near]
    ph = ph[near]
    outside = np.abs(ph) > 0.75 * dur * 24.0
    base = float(np.median(flux[outside])) if outside.any() else float(np.median(flux)) if flux.size else 1.0
    centres, means = _bin(ph, (flux / base - 1.0) * 1e6, window, 2.0 * window / N_BINS)
    keep = np.isfinite(means) & (np.abs(centres) <= window)
    hours = np.linspace(-window, window, MODEL_POINTS)
    model = _fit_model(hours, period, dur, _shape_metrics(tests))
    if model is not None:
        line = list(zip(hours.tolist(), model.tolist()))
    else:
        h = 12.0 * dur
        line = [(-window, 0.0), (-h, 0.0), (-h, -depth), (h, -depth), (h, 0.0), (window, 0.0)]
    return {"hours": centres[keep].tolist(), "ppm": means[keep].tolist(), "model": line, "window_h": window,
            "model_kind": "fit" if model is not None else "box"}


def render_svg(points: dict, depth_ppm: float) -> str:
    """The sparkline: binned points as ion dots, the model as a solar line, on a transparent background. The vertical
    scale always holds the baseline and the full depth, so a shallow dip is never stretched to fill the picture."""
    w = float(points["window_h"]) or 1.0
    model = points["model"]
    values = [*points["ppm"], *(p for _h, p in model), 0.0, -abs(depth_ppm)]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    lo, hi = lo - 0.08 * span, hi + 0.08 * span

    def x(h: float) -> float:
        return round(PAD + (h + w) / (2 * w) * (WIDTH - 2 * PAD), 1)

    def y(p: float) -> float:
        return round(PAD + (hi - p) / (hi - lo) * (HEIGHT - 2 * PAD), 1)

    dots = "".join(f'<circle cx="{x(h)}" cy="{y(p)}" r="{DOT_RADIUS}"/>' for h, p in zip(points["hours"],
                                                                                         points["ppm"]))
    path = "M" + " L".join(f"{x(h)} {y(p)}" for h, p in model)
    return (f'{_SVG_OPEN}<g fill="{ION}">{dots}</g>'
            f'<path d="{path}" fill="none" stroke="{SOLAR}" stroke-width="{MODEL_WIDTH}" stroke-linejoin="round"/>'
            "</svg>")


def placeholder_svg() -> str:
    """What a card shows when the raw light curve is not on disk: a dashed baseline and the words."""
    return (f'{_SVG_OPEN}<line x1="{PAD}" y1="50" x2="{WIDTH - PAD}" y2="50" stroke="{MUTED}" stroke-width="1" '
            f'stroke-dasharray="3 4"/><text x="{WIDTH // 2}" y="36" text-anchor="middle" fill="{MUTED}" '
            'font-family="IBM Plex Sans, system-ui, sans-serif" font-size="12">light curve not cached</text></svg>')
