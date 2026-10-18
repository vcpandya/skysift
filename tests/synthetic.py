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


def _with_dip(lc: LightCurve, factor: np.ndarray) -> LightCurve:
    """``lc`` with ``flux`` and ``flux_alt`` both multiplied by ``factor``: a signal on the target star shows in the
    SAP and the PSF photometry alike, which is what rung 3's aperture-dependent depth check assumes of a planet."""
    alt = None if lc.flux_alt is None else lc.flux_alt * factor
    return LightCurve(lc.time, lc.flux * factor, lc.flux_err, lc.quality, lc.quarter, alt, dict(lc.meta))


def inject_transit(lc: LightCurve, period, t0, duration_days, depth_ppm):
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    factor = np.where(np.abs(ph) < 0.5 * duration_days, 1 - depth_ppm * 1e-6, 1.0)
    return _with_dip(lc, factor)


def inject_v_eclipse(lc: LightCurve, period, t0, width_days, depth_ppm):
    """A triangular (V-shaped) eclipse: ``depth_ppm`` at mid-eclipse falling linearly to zero at +-width_days/2."""
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    shape = np.clip(1.0 - np.abs(ph) / (0.5 * width_days), 0.0, None)
    return _with_dip(lc, 1 - depth_ppm * 1e-6 * shape)


def inject_model(lc: LightCurve, period, t0, k, b, a_over_r):
    """Multiply the flux by the uniform-disc transit model (``skysift.lc.transit_model``) of a circular orbit."""
    from skysift.lc.transit_model import uniform_disc_flux
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    return _with_dip(lc, uniform_disc_flux(ph, period, k, b, a_over_r))
