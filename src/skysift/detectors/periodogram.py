"""Per-target periodogram summary: the per-band normalised SDE envelope on a fixed log-period grid plus the exact top
peaks. About 8 kB per star against about 2 MB for the raw spectrum, and the envelope keeps what the eye and the
false-alarm calibration need: peak heights, their isolation, aliases and the noise floor."""
from __future__ import annotations

import io

import numpy as np
from pydantic import BaseModel, Field

N_BINS = 2048


class PeriodogramSummary(BaseModel):
    min_period: float
    max_period: float
    n_bins: int = N_BINS
    envelope: list[float | None]            # max SDE per log-period bin; None where no trial period fell
    peaks: list[dict] = Field(default_factory=list)   # {"period", "sde", "duration", "rank"}
    band_edges: list[float] = Field(default_factory=list)
    n_periods: int = 0

    def bin_periods(self) -> np.ndarray:
        edges = np.geomspace(self.min_period, self.max_period, self.n_bins + 1)
        return np.sqrt(edges[:-1] * edges[1:])

    @classmethod
    def from_spectrum(cls, periods: np.ndarray, sde: np.ndarray, min_period: float, max_period: float,
                      band_edges, peaks: list[dict], n_bins: int = N_BINS) -> PeriodogramSummary:
        periods = np.asarray(periods, float); sde = np.asarray(sde, float)
        env = np.full(n_bins, np.nan)
        if periods.size:
            frac = np.log(periods / min_period) / np.log(max_period / min_period)
            idx = np.clip(np.floor(frac * n_bins).astype(int), 0, n_bins - 1)
            np.fmax.at(env, idx, sde)              # fmax ignores the NaN fill, so each bin ends at its maximum SDE
        return cls(min_period=float(min_period), max_period=float(max_period), n_bins=n_bins,
                   envelope=[None if not np.isfinite(v) else float(v) for v in env],
                   peaks=[{k: (float(v) if isinstance(v, (int, float, np.generic)) and k != "rank" else v) for k, v in p.items()}
                          for p in peaks],
                   band_edges=[float(b) for b in band_edges], n_periods=int(periods.size))

    def to_npz(self) -> bytes:
        buf = io.BytesIO()
        env = np.array([np.nan if v is None else v for v in self.envelope], dtype=np.float32)
        peaks = np.array([(p["period"], p["sde"], p["duration"], p["rank"]) for p in self.peaks], dtype=np.float64).reshape(-1, 4)
        np.savez_compressed(buf, envelope=env, peaks=peaks, band_edges=np.asarray(self.band_edges, np.float64),
                            meta=np.array([self.min_period, self.max_period, self.n_bins, self.n_periods], np.float64))
        return buf.getvalue()

    @classmethod
    def from_npz(cls, data: bytes) -> PeriodogramSummary:
        with np.load(io.BytesIO(data)) as z:
            env, peaks, edges, meta = z["envelope"], z["peaks"], z["band_edges"], z["meta"]
        return cls(min_period=float(meta[0]), max_period=float(meta[1]), n_bins=int(meta[2]), n_periods=int(meta[3]),
                   envelope=[None if not np.isfinite(v) else float(v) for v in env],
                   peaks=[{"period": float(p), "sde": float(s), "duration": float(d), "rank": int(r)} for p, s, d, r in peaks],
                   band_edges=[float(b) for b in edges])
