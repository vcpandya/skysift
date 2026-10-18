import numpy as np

from skysift.models import LightCurve


def make_lightcurve(n_days=1400, cadence_min=29.4, noise_ppm=1500, seed=0, quarters=True, gaps=True):
    rng = np.random.default_rng(seed)
    t = np.arange(131.5, 131.5 + n_days, cadence_min / 1440.0)
    q = ((t - 131.5) // 93.0).astype(int) + 1 if quarters else np.ones_like(t, dtype=int)
    f = 1.0 + rng.normal(0, noise_ppm * 1e-6, t.size)
    if quarters:
        f *= 1.0 + 0.01 * (q % 3)          # per-quarter offsets
    if gaps:
        keep = ((t - 131.5) % 93.0) > 1.5
        t, f, q = t[keep], f[keep], q[keep]
    e = np.full(t.size, noise_ppm * 1e-6)
    return LightCurve(time=t, flux=f, flux_err=e, quality=np.zeros(t.size, int), quarter=q, flux_alt=f.copy())


def inject_transit(lc: LightCurve, period, t0, duration_days, depth_ppm):
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    f = lc.flux.copy(); f[np.abs(ph) < 0.5 * duration_days] *= 1 - depth_ppm * 1e-6
    return LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt, dict(lc.meta))


def inject_v_eclipse(lc: LightCurve, period, t0, width_days, depth_ppm):
    """A triangular (V-shaped) eclipse: ``depth_ppm`` at mid-eclipse falling linearly to zero at +-width_days/2."""
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    shape = np.clip(1.0 - np.abs(ph) / (0.5 * width_days), 0.0, None)
    f = lc.flux * (1 - depth_ppm * 1e-6 * shape)
    return LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt, dict(lc.meta))
