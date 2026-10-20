"""The approximate astrophysical false-positive probability of spec 4.2: three scenarios in the spirit of Morton
(2012), a planet on the target (PL), a background or foreground eclipsing binary within the confusion radius (BEB)
and an eclipsing binary on the target itself (EB). Each scenario weighs ``prior x geometric x data``;
``FPP = (w_BEB + w_EB) / (w_PL + w_BEB + w_EB)``. Every factor and its inputs is kept in ``ScenarioWeight.factors``
so the dossier can print the whole table on the Expert level.

Pure arithmetic over ``priors``; no I/O. The numbers are provisional until the sign-off gate of spec 4.2
(``SIGNED_OFF``)."""
from __future__ import annotations

import math
from dataclasses import dataclass

from skysift import jsonutil
from skysift.lc.ops import circular_duration_days
from skysift.priors import (
    CONFUSION_FLOOR_ARCSEC,
    ECCENTRICITY_ALLOWANCE,
    F_BIN,
    F_EB,
    PIXEL_ARCSEC,
    PSF_BLEND_ARCSEC,
    binary_period_fraction,
    planet_prior,
    spectral_class,
)

SIGNED_OFF = False          # flipped after the review of spec 4.2; the tile says "provisional" while False

FALLBACK_RADIUS = 0.5       # Rsun, when the star has no radius
FALLBACK_MASS = 0.5         # Msun, when the star has no mass
RSUN_AU = 0.00465047
RSUN_REARTH = 6.957e8 / 6.371e6      # the nominal solar and Earth radii of rung 2
BEB_PAIR_MASS = 2.0         # Msun: an equal-mass pair of Sun-like stars
BEB_PAIR_RADII = 2 * 1.0    # Rsun: R1 + R2 of that pair
EB_R2_OVER_R1 = 0.5
EB_M2_OVER_M1 = 0.5          # the companion's mass ratio, held equal to its radius ratio
EB_SECONDARY_RATIO = 0.1    # an equal-period EB of this depth predicts a secondary of at least depth x 0.1
EB_ODD_EVEN_RATIO = 0.25    # the predicted odd/even alternation for R2 = 0.5 R1, as a fraction of the depth
V_SHAPE_B = 0.9
V_SHAPE_FACTOR = 0.5
FIT_DURATION_ERROR = 0.1    # the fit duration's fractional error in the D_shape sigma


@dataclass(frozen=True)
class Neighbour:
    source_id: str
    sep_arcsec: float
    gmag: float
    bp_rp: float | None
    # from the pixel stage, when present: distance from the season-averaged difference centroid
    centroid_sep_arcsec: float | None = None


@dataclass(frozen=True)
class FppInputs:
    period: float                       # days
    depth: float                        # fraction (ppm / 1e6)
    gmag: float                         # target G
    teff: float | None
    radius: float | None                # Rsun; None -> 0.5
    mass: float | None                  # Msun; None -> 0.5
    rp_rearth: float | None             # None -> from sqrt(depth) * radius
    fit_k: float | None                 # rung 2 fit; None when fit_ok is False
    fit_b: float | None
    fit_a_over_r: float | None
    secondary_ppm: float | None         # rung 2 secondary and control scatter
    secondary_err_ppm: float | None
    odd_ppm: float | None
    even_ppm: float | None
    odd_even_err_ppm: float | None
    neighbours: tuple[Neighbour, ...]
    centroid_sigma_arcsec: float | None  # season-averaged centroid error; None without a pixel stage


@dataclass(frozen=True)
class ScenarioWeight:
    name: str                           # "PL" | "BEB" | "EB"
    prior: float
    geometric: float
    data: float
    factors: dict[str, float | str | None]   # every named factor and the inputs it was computed from

    @property
    def weight(self) -> float:
        return self.prior * self.geometric * self.data


@dataclass(frozen=True)
class Fpp:
    value: float                        # (w_BEB + w_EB) / (w_PL + w_BEB + w_EB)
    scenarios: tuple[ScenarioWeight, ScenarioWeight, ScenarioWeight]
    pixels: bool                        # False -> label "False positive (no pixel data yet)"
    confusion_radius_arcsec: float
    neighbours: tuple[dict, ...]        # per neighbour: source_id, sep_arcsec, gmag, bright_enough, exclusion X_i

    def words(self) -> str:
        """One significant figure as "about N percent", floored at "under 1 percent", capped at "over 90 percent"."""
        v = self.value
        if v < 0.01: return "under 1 percent"
        if v > 0.9: return "over 90 percent"
        pct = v * 100
        scale = 10 ** math.floor(math.log10(pct))
        n = math.floor(pct / scale + 0.5) * scale          # half up, so 0.025 reads "about 3 percent"
        return f"about {n} percent"

    def label(self) -> str:
        return "False positive" if self.pixels else "False positive (no pixel data yet)"

    def as_dict(self) -> dict:
        """The factor table of spec 4.2, ready for strict JSON: tuples become lists and any non-finite float (an
        infinite k_i or a nan input) becomes None through ``jsonutil.sanitize``, so ``json.dumps`` with
        ``allow_nan=False`` never fails on it."""
        return jsonutil.sanitize({
            "value": self.value, "words": self.words(), "label": self.label(), "signed_off": SIGNED_OFF,
            "pixels": self.pixels, "confusion_radius_arcsec": self.confusion_radius_arcsec,
            "neighbours": [dict(n) for n in self.neighbours],
            "scenarios": [{"name": s.name, "prior": s.prior, "geometric": s.geometric, "data": s.data,
                           "weight": s.weight, "factors": dict(s.factors)} for s in self.scenarios],
        })


def _a_au(period: float, mass: float) -> float:
    """Kepler's third law: the semi-major axis in AU for a period in days and a total mass in Msun."""
    return (mass * (period / 365.25) ** 2) ** (1 / 3)


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _tail(predicted: float, measured: float | None, err: float | None) -> float:
    """``1 - Phi((predicted - measured) / err)``: the chance that a signal of the predicted size hides under the
    measurement; 1 when the measurement or its error is missing or not finite (rung 2 can report NaN)."""
    if measured is None or err is None or not math.isfinite(measured) or not math.isfinite(err) or not err > 0:
        return 1.0
    return 1.0 - _phi((predicted - measured) / err)


def _planet(inputs: FppInputs, cls: str, radius: float, mass: float) -> ScenarioWeight:
    period = inputs.period
    rp = inputs.rp_rearth if inputs.rp_rearth is not None else math.sqrt(inputs.depth) * radius * RSUN_REARTH
    f_pl, note = planet_prior(cls, rp, period)
    a_au = _a_au(period, mass)
    r_over_a = radius * RSUN_AU / a_au
    t14_circ = float(circular_duration_days(period, mass, radius))
    k, b, a_r = inputs.fit_k, inputs.fit_b, inputs.fit_a_over_r
    factors: dict[str, float | str | None] = {
        "class": cls, "rp_rearth": rp, "f_pl": f_pl, "a_au": a_au, "R_star_over_a": r_over_a, "T14_circ": t14_circ}
    if k is None or b is None or a_r is None:
        d_shape = 1.0
        factors.update({"T14_fit": None, "sigma": None, "z": None})
    else:
        chord = math.sqrt(max(0.0, (1 + k) ** 2 - b ** 2))
        t14_fit = period / math.pi * math.asin(min(1.0, chord / a_r))
        sigma = math.hypot(FIT_DURATION_ERROR * t14_fit, ECCENTRICITY_ALLOWANCE * t14_circ)
        z = (t14_fit - t14_circ) / sigma
        d_shape = math.exp(-z ** 2 / 2)
        factors.update({"T14_fit": t14_fit, "sigma": sigma, "z": z})
    factors["D_shape"] = d_shape
    if note: factors["note"] = note
    return ScenarioWeight("PL", f_pl, r_over_a, d_shape, factors)


def _exclusion(s: float, r_c: float, sigma_c: float | None) -> float:
    """``X_i``: with pixels ``exp(-k^2 / 2)`` for ``k = max(0, (s - r_c) / sigma_c)``; without, 1 inside ``r_c`` and
    a Gaussian fall-off of width ``PSF_BLEND_ARCSEC`` outside it."""
    if s <= r_c: return 1.0
    if sigma_c is None: return math.exp(-(s - r_c) ** 2 / (2 * PSF_BLEND_ARCSEC ** 2))
    if not sigma_c > 0: return 0.0            # a zero centroid error excludes everything outside r_c
    k = (s - r_c) / sigma_c
    return math.exp(-k ** 2 / 2)


def _background(inputs: FppInputs) -> tuple[ScenarioWeight, float, tuple[dict, ...]]:
    sigma_c = inputs.centroid_sigma_arcsec
    r_c = max(3 * sigma_c, CONFUSION_FLOOR_ARCSEC) if sigma_c is not None else PIXEL_ARCSEC
    rows, bright_x = [], []
    for n in inputs.neighbours:
        ratio = 10 ** (-0.4 * (n.gmag - inputs.gmag))
        frac = ratio / (1 + ratio)
        bright = frac >= 2 * inputs.depth
        # without a pixel stage every neighbour is measured from the target, whatever centroid it carries
        s = n.centroid_sep_arcsec if sigma_c is not None and n.centroid_sep_arcsec is not None else n.sep_arcsec
        x = _exclusion(s, r_c, sigma_c)
        rows.append({"source_id": n.source_id, "sep_arcsec": n.sep_arcsec, "gmag": n.gmag,
                     "centroid_sep_arcsec": n.centroid_sep_arcsec, "s_arcsec": s, "flux_fraction": frac,
                     "bright_enough": bright, "exclusion": x})
        if bright: bright_x.append(x)
    n_bright = len(bright_x)
    a_2p = _a_au(2 * inputs.period, BEB_PAIR_MASS)
    geometric = BEB_PAIR_RADII * RSUN_AU / a_2p
    x_mean = sum(bright_x) / n_bright if n_bright else 0.0     # no bright neighbour: the BEB weight is 0
    factors: dict[str, float | str | None] = {
        "f_EB": F_EB, "n_bright": n_bright, "a_2P_au": a_2p, "X_mean": x_mean, "r_c_arcsec": r_c,
        "centroid_sigma_arcsec": sigma_c}
    return ScenarioWeight("BEB", F_EB * n_bright, geometric, x_mean, factors), r_c, tuple(rows)


def _on_target_binary(inputs: FppInputs, cls: str, radius: float, mass: float) -> ScenarioWeight:
    f_bin = F_BIN[cls]
    g_p = binary_period_fraction(inputs.period)
    m_pair = mass * (1 + EB_M2_OVER_M1)
    a_2p = _a_au(2 * inputs.period, m_pair)
    geometric = radius * (1 + EB_R2_OVER_R1) * RSUN_AU / a_2p
    depth_ppm = inputs.depth * 1e6
    s_sec = _tail(EB_SECONDARY_RATIO * depth_ppm, inputs.secondary_ppm, inputs.secondary_err_ppm)
    odd_even = abs(inputs.odd_ppm - inputs.even_ppm) if inputs.odd_ppm is not None and inputs.even_ppm is not None \
        else None
    s_oe = _tail(EB_ODD_EVEN_RATIO * depth_ppm, odd_even, inputs.odd_even_err_ppm)
    s_v = 1.0 if (inputs.fit_b is None or inputs.fit_b < V_SHAPE_B) else V_SHAPE_FACTOR
    factors: dict[str, float | str | None] = {
        "class": cls, "f_bin": f_bin, "g_P": g_p, "a_2P_au": a_2p, "pair_mass_msun": m_pair,
        "secondary_predicted_ppm": EB_SECONDARY_RATIO * depth_ppm, "secondary_ppm": inputs.secondary_ppm,
        "secondary_err_ppm": inputs.secondary_err_ppm, "S_sec": s_sec,
        "odd_even_predicted_ppm": EB_ODD_EVEN_RATIO * depth_ppm, "odd_even_ppm": odd_even,
        "odd_even_err_ppm": inputs.odd_even_err_ppm, "S_oe": s_oe, "fit_b": inputs.fit_b, "S_v": s_v}
    return ScenarioWeight("EB", f_bin * g_p, geometric, s_sec * s_oe * s_v, factors)


def compute(inputs: FppInputs) -> Fpp:
    """The three scenario weights and the FPP of spec 4.2 for one signal. Raises ``ValueError`` on a non-positive
    period, depth or fitted a/R*, and when the weights come out non-finite (a NaN stellar or fit input)."""
    if not inputs.period > 0: raise ValueError(f"period must be positive, got {inputs.period}")
    if not inputs.depth > 0: raise ValueError(f"depth must be positive, got {inputs.depth}")
    if inputs.fit_a_over_r is not None and not inputs.fit_a_over_r > 0:
        raise ValueError(f"fit_a_over_r must be positive, got {inputs.fit_a_over_r}")
    radius = inputs.radius or FALLBACK_RADIUS
    mass = inputs.mass or FALLBACK_MASS
    cls = spectral_class(inputs.teff)
    pl = _planet(inputs, cls, radius, mass)
    beb, r_c, rows = _background(inputs)
    eb = _on_target_binary(inputs, cls, radius, mass)
    total = pl.weight + beb.weight + eb.weight
    if not math.isfinite(total):
        raise ValueError("the scenario weights are not finite; check the stellar and fit inputs")
    value = (beb.weight + eb.weight) / total if total > 0 else 1.0     # no admissible scenario at all: not a planet
    return Fpp(value=value, scenarios=(pl, beb, eb), pixels=inputs.centroid_sigma_arcsec is not None,
               confusion_radius_arcsec=r_c, neighbours=rows)
