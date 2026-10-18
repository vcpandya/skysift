from __future__ import annotations

import csv
import io
import math

from skysift.models import Signal, TestResult, TestStatus
from skysift.net import ServiceUnavailable, looks_like_error_document
from skysift.vetting.base import DataBundle

EXO_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
VIZIER = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"


class _SchemaError(Exception):
    pass


def _to_float(value) -> float:
    """Parse a catalogue number; 0.0 for None, empty, non-numeric, non-finite or negative values."""
    try: v = float(value)
    except (TypeError, ValueError): return 0.0
    return v if math.isfinite(v) and v >= 0 else 0.0


def _csv_rows(data: bytes) -> tuple[list[str] | None, list[dict]]:
    """Return (header, rows); header is None when the body has no header line."""
    text = data.decode(errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines: return None, []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    return list(reader.fieldnames or []), list(reader)


def _tsv_rows(data: bytes) -> tuple[list[str] | None, list[dict]]:
    """VizieR asu-tsv: comment lines, header, units, '---' separator, then rows.

    Returns (header, rows); header is None when no separator line is found."""
    text = data.decode(errors="replace").splitlines()
    lines = [ln for ln in text if ln.strip() and not ln.startswith("#")]
    sep = next((i for i, ln in enumerate(lines) if ln.split("\t")[0].strip().startswith("---")), None)
    if sep is None or sep < 2:
        # VizieR answers a cone search with no match by sending the table metadata (#Table, #Column lines) and no
        # data section at all (verified October 2026): that is a legitimate zero, and the column names come from the
        # metadata. A body without any #Column line is empty or foreign and stays an undetermined schema error.
        columns = [ln.split("\t")[1].strip() for ln in text if ln.startswith("#Column\t") and ln.count("\t") >= 1]
        return (columns, []) if columns else (None, [])
    header = lines[sep - 2].split("\t")
    rows = (ln.split("\t") for ln in lines[sep + 1:])
    return header, [dict(zip(header, r)) for r in rows if len(r) == len(header)]


OWN_PERIOD_TOL = 0.01          # rows of the target itself: its own kepid, the Gaia tables queried by source_id, or a
                               # row within SAME_STAR_ARCSEC of the target
OWN_RATIO_TOL = 0.001          # own rows at every ratio other than 1 (review of 18 October 2026): fifteen 1-percent
                               # windows over 0.5 to 200 d let a random genuine signal match a given own period about 5
                               # percent of the time (20 to 40 percent on multi-planet hosts), while a real alias matches
                               # to about 1e-4 (K06356.01: 3.44144 d against 24.09002 / 7 = 3.44143 d)
NEIGHBOUR_PERIOD_TOL = 0.001   # rows matched by position only
SAME_STAR_ARCSEC = 2.0         # a catalogue row this close is the target star itself (K06356.01 sits 0.66 arcsec from
                               # gaia-dr3-2052196018973519744, which has no KIC)
NEIGHBOUR_RATIOS = (0.5, 1.0, 2.0)
OWN_RATIOS = tuple(sorted({1.0 / k for k in range(2, 9)} | {1.0} | {float(k) for k in range(2, 9)}))   # 1/8 .. 8


def _period_match(p_signal: float, p_cat: float, tol=OWN_PERIOD_TOL, ratios=NEIGHBOUR_RATIOS,
                  ratio_tol=OWN_RATIO_TOL) -> float | None:
    """The ratio ``k`` of ``ratios`` with ``p_signal`` within tolerance (relative) of ``k`` times the catalogue period,
    or None (the smallest relative mismatch wins when several fit). The tolerance is ``tol`` at ``k`` = 1 and the
    smaller of ``tol`` and ``ratio_tol`` at every other ratio.

    The tolerance and the ratios depend on whose row it is. A row of the target itself keeps 1 percent at ratio 1,
    which absorbs the period error of a short or noisy detection of the same object, and 0.1 percent (OWN_RATIO_TOL) at
    every other integer ratio both ways up to 8 (OWN_RATIOS), where a real alias matches to about 1e-4: a known
    object on the star explains a signal at any of its sub-harmonics or harmonics, and the detector can rank one of
    those first (K06356.01 at 24.09 d came out of the live run at 24.09/7 d). A neighbour inside the 120 arcsec cone
    (36 times the area of the old 20 arcsec one) keeps 0.5, 1 and 2 at 0.1 percent: a random catalogue period lands in
    one of three 2-percent windows about 0.9 percent of the time, so the old tolerance would reject about one genuine
    new signal in a hundred as known, while the Kepler and BLS periods of a real blend agree to about 1e-4."""
    if not (p_cat and p_cat > 0):
        return None
    fits = [(err, k) for k in ratios
            if (err := abs(p_signal - k * p_cat) / (k * p_cat)) < (tol if k == 1.0 else min(tol, ratio_tol))]
    return float(min(fits)[1]) if fits else None


BJD_MINUS_BKJD = 2454833.0     # Kepler BJD offset: BKJD = BJD - 2454833.0 (the TOI table gives pl_tranmid in BJD)


def _epoch(value, offset: float = 0.0) -> float | None:
    """A catalogue epoch in BKJD (``value`` minus ``offset``), or None for a missing, empty or non-finite value."""
    try: v = float(value) - offset
    except (TypeError, ValueError): return None
    return v if math.isfinite(v) else None


def _epoch_check(t0_sig: float, p_sig: float, duration_d: float, t0_cat: float | None,
                 p_cat: float) -> tuple[float | None, bool | None]:
    """(epoch_offset_h, epoch_agrees) of a period-matched catalogue row; both None without a catalogue epoch.

    The signal's t0 is compared with the catalogue ephemeris modulo the shorter of the two periods: for a ratio
    ``p_sig / p_cat`` of 1 or more the signal's transits are a subset of the catalogue's, below 1 the catalogue's are a
    subset of the signal's, so a genuine match (the same object or its alias) has t0 on the shorter-period grid. A
    second real planet in an exact n:1 resonance with the known one matches on period alone but not on epoch. The
    tolerance is twice the signal's duration with a 1 h floor (30 min cadence)."""
    m = min(p_sig, p_cat)
    if t0_cat is None or not (math.isfinite(t0_sig) and math.isfinite(m) and m > 0):
        return None, None
    offset = ((t0_sig - t0_cat + 0.5 * m) % m) - 0.5 * m
    offset_h = abs(offset) * 24.0
    return offset_h, offset_h <= max(2.0 * duration_d * 24.0, 1.0)


def _same_kic(value, kic: int | None) -> bool:
    """True when a catalogue KIC or kepid field names ``kic`` (VizieR may pad it with spaces or zeros)."""
    if not kic: return False
    try: return int(str(value).strip()) == int(kic)
    except (TypeError, ValueError): return False


def _box(ra: float, dec: float, radius_deg: float) -> str:
    """ADQL ``where`` fragment selecting a box around (ra, dec) that contains the search circle. The Exoplanet
    Archive sits behind a Cloudflare filter that rejects every GET whose query holds ``CONTAINS(POINT(``
    (HTTP 403, October 2026), so the cone is expressed as plain range predicates and the exact circle is applied
    to the returned rows by ``_within``. RA half-width grows with 1/cos(dec); near the poles the whole RA range
    is taken, and a box crossing RA 0/360 becomes two ranges."""
    dec_lo, dec_hi = max(-90.0, dec - radius_deg), min(90.0, dec + radius_deg)
    cosd = math.cos(math.radians(dec))
    if cosd < 1e-6 or radius_deg / cosd >= 180.0:
        return f"dec between {dec_lo} and {dec_hi}"
    dra = radius_deg / cosd
    lo, hi = ra - dra, ra + dra
    if lo < 0.0:
        ra_part = f"(ra between 0 and {hi} or ra between {lo + 360.0} and 360)"
    elif hi > 360.0:
        ra_part = f"(ra between {lo} and 360 or ra between 0 and {hi - 360.0})"
    else:
        ra_part = f"ra between {lo} and {hi}"
    return f"{ra_part} and dec between {dec_lo} and {dec_hi}"


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation (haversine), degrees."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    a = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


def _sep_arcsec(row: dict, ra: float, dec: float) -> float | None:
    """Separation of the row from (ra, dec) in arcsec, or None when the row has no parseable finite ra/dec."""
    try:
        r_ra, r_dec = float(row.get("ra")), float(row.get("dec"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(r_ra) and math.isfinite(r_dec)):
        return None
    return _separation_deg(ra, dec, r_ra, r_dec) * 3600.0


def _within(row: dict, ra: float, dec: float, radius_deg: float) -> bool:
    """True when the row lies inside the search circle; rows without parseable ra/dec are kept (box-limited)."""
    sep = _sep_arcsec(row, ra, dec)
    return sep is None or sep <= radius_deg * 3600.0


def _frequency_to_period(row: dict) -> float:
    f = _to_float(row.get("frequency"))
    return 1.0 / f if f > 0 else 0.0


class Rung4Novelty:
    name = "rung4_novelty"
    cost = 1.0

    def __init__(self, radius_arcsec: float = 120.0):
        # Kepler blends share pixels out to about two arcminutes, so a period-matched neighbour counts as known
        self.radius_arcsec = radius_arcsec
        self.radius_deg = radius_arcsec / 3600.0

    def _get(self, b: DataBundle, service, url, params, key):
        if key in b.cache:
            return b.cache[key]
        data = b.fetch(url, params=params, service=service, store=b.store)
        err = looks_like_error_document(data)
        if err:  # HTTP 200 carrying an error document is an outage, never an empty result
            raise ServiceUnavailable(f"{key[1]}: {err}")
        b.cache[key] = data
        return data

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        t = b.target
        cone = _box(t.ra, t.dec, self.radius_deg)                 # exact circle applied by _within on the rows
        where_cum = f"kepid={t.kic} or {cone}" if t.kic else cone   # the target's own KOI/TCE rows, plus any neighbour
        koi_id = lambda r: r.get("kepoi_name")
        tce_id = lambda r: f"{r.get('kepid')}.{r.get('tce_plnt_num')}"
        toi_id = lambda r: r.get("toi")
        koi_t0 = lambda r: _epoch(r.get("koi_time0bk"))                    # BKJD
        tce_t0 = lambda r: _epoch(r.get("tce_time0bk"))                    # BKJD
        toi_t0 = lambda r: _epoch(r.get("pl_tranmid"), BJD_MINUS_BKJD)     # BJD
        checks = [
            ("cumulative", f"select kepid,kepoi_name,koi_period,koi_time0bk,ra,dec from cumulative where {where_cum}",
             "koi_period", koi_id, koi_t0),
            ("q1_q17_dr25_tce", f"select kepid,tce_plnt_num,tce_period,tce_time0bk,ra,dec from q1_q17_dr25_tce where {where_cum}",
             "tce_period", tce_id, tce_t0),
            ("toi", f"select tid,toi,pl_orbper,pl_tranmid,ra,dec from toi where {cone}", "pl_orbper", toi_id, toi_t0),
        ]
        found, metrics = [], {}

        def scan(label, parsed, pcol, ident, period=None, epoch=None):
            """Record counts and other-period rows (catalogue, id, period, separation in arcsec or None); return a
            known-reason identifier on a period match (the matching row goes to ``period_matched`` with its
            separation from the target, the period ratio, and ``epoch_offset_h`` and ``epoch_agrees`` from
            ``_epoch_check``, None when the table has no epoch; information only, the verdict does not change).
            Rows of the target itself (its own kepid or KIC; every row of a Gaia table, which is queried by
            source_id; any row within SAME_STAR_ARCSEC of the target) are matched first, at 1 percent for ratio 1 and 0.1 percent at every other integer ratio up to 8 both ways;
            neighbours at 0.1 percent and the ratios 0.5, 1 and 2."""
            header, rows = parsed
            if header is None: raise _SchemaError(f"schema:{label}:empty response")
            if rows and pcol not in header: raise _SchemaError(f"schema:{label}:missing {pcol}")
            by_id = label.startswith("gaia_")

            def own(r):
                if by_id or _same_kic(r.get("kepid"), t.kic) or _same_kic(r.get("KIC"), t.kic):
                    return True
                sep = _sep_arcsec(r, t.ra, t.dec)
                return sep is not None and sep <= SAME_STAR_ARCSEC
            if "ra" in header and "dec" in header:                 # box query: keep only rows inside the circle
                rows = [r for r in rows if own(r) or _within(r, t.ra, t.dec, self.radius_deg)]
            metrics[label] = len(rows)
            for r in sorted(rows, key=lambda r: not own(r)):       # stable: the target's own rows first
                p = period(r) if period else _to_float(r.get(pcol))
                if p <= 0: continue
                sep = _sep_arcsec(r, t.ra, t.dec)
                ratio = (_period_match(s.period, p, OWN_PERIOD_TOL, OWN_RATIOS, OWN_RATIO_TOL) if own(r)
                         else _period_match(s.period, p, NEIGHBOUR_PERIOD_TOL, NEIGHBOUR_RATIOS))
                if ratio is not None:
                    offset_h, agrees = _epoch_check(s.t0, s.period, s.duration, epoch(r) if epoch else None, p)
                    metrics["period_matched"] = [{"catalog": label, "id": ident(r), "period": p, "sep_arcsec": sep,
                                                  "ratio": ratio, "epoch_offset_h": offset_h, "epoch_agrees": agrees}]
                    return ident(r)
                found.append((label, ident(r), p, sep))
            return None

        svc = "exoarchive"
        try:
            for table, query, pcol, ident, epoch in checks:
                parsed = _csv_rows(self._get(b, "exoarchive", EXO_TAP, {"query": query, "format": "csv"}, ("exoarchive", table, t.target_id)))
                hit = scan(table, parsed, pcol, ident, epoch=epoch)
                if hit is not None:
                    return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:{table}:{hit}")
            svc = "vizier"
            # the table-level source: the catalogue also holds table9 (non-repeating events, no Per column), and a
            # catalogue-level query returns every table that matches
            parsed = _tsv_rows(self._get(b, "vizier", VIZIER, {"-source": "J/AJ/151/68/catalog", "-c": f"{t.ra} {t.dec:+}", "-c.rs": self.radius_arcsec, "-out.max": 50},
                                         ("vizier", "J/AJ/151/68", t.target_id)))
            hit = scan("kepler_eb", parsed, "Per", lambda r: r.get("KIC"))
            if hit is not None:
                return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:kepler_eb:{hit}")
            if t.gaia_id and not str(t.gaia_id).isdigit():
                # source_id is interpolated into ADQL: anything but digits is never sent
                metrics["gaia_skipped"] = "non-numeric source_id"
            elif t.gaia_id:
                svc = "gaia"
                for table, pcol, period in (("nss_two_body_orbit", "period", None), ("vari_eclipsing_binary", "frequency", _frequency_to_period)):
                    parsed = _csv_rows(self._get(b, "gaia", GAIA_TAP, {"request": "doQuery", "lang": "ADQL", "format": "csv",
                                                                       "query": f"select * from gaiadr3.{table} where source_id={t.gaia_id}"},
                                                 ("gaia", table, t.gaia_id)))
                    hit = scan(f"gaia_{table}", parsed, pcol, lambda r: r.get("source_id") or t.gaia_id, period)
                    if hit is not None:
                        return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:gaia_{table}:{t.gaia_id}")
        except _SchemaError as e:
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, metrics=metrics, reason=str(e))
        except ServiceUnavailable as e:
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, metrics=metrics, reason=f"outage:{svc}: {e}")
        metrics["other_period_objects"] = found
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=metrics)
