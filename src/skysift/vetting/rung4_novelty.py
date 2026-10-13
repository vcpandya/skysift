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
    lines = [ln for ln in data.decode(errors="replace").splitlines() if ln.strip() and not ln.startswith("#")]
    sep = next((i for i, ln in enumerate(lines) if ln.split("\t")[0].strip().startswith("---")), None)
    if sep is None or sep < 2: return None, []
    header = lines[sep - 2].split("\t")
    rows = (ln.split("\t") for ln in lines[sep + 1:])
    return header, [dict(zip(header, r)) for r in rows if len(r) == len(header)]


def _period_match(p_signal: float, p_cat: float, tol=0.01) -> bool:
    return any(abs(p_signal - k * p_cat) / (k * p_cat) < tol for k in (0.5, 1.0, 2.0)) if p_cat and p_cat > 0 else False


def _frequency_to_period(row: dict) -> float:
    f = _to_float(row.get("frequency"))
    return 1.0 / f if f > 0 else 0.0


class Rung4Novelty:
    name = "rung4_novelty"
    cost = 1.0

    def __init__(self, radius_arcsec: float = 20.0):
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
        cone = f"CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{t.ra},{t.dec},{self.radius_deg}))=1"
        where_cum = f"kepid={t.kic}" if t.kic else cone
        koi_id = lambda r: r.get("kepoi_name")
        tce_id = lambda r: f"{r.get('kepid')}.{r.get('tce_plnt_num')}"
        toi_id = lambda r: r.get("toi")
        checks = [
            ("cumulative", f"select kepid,kepoi_name,koi_period from cumulative where {where_cum}", "koi_period", koi_id),
            ("q1_q17_dr25_tce", f"select kepid,tce_plnt_num,tce_period from q1_q17_dr25_tce where {where_cum}", "tce_period", tce_id),
            ("toi", f"select tid,toi,pl_orbper from toi where {cone}", "pl_orbper", toi_id),
        ]
        found, metrics = [], {}

        def scan(label, parsed, pcol, ident, period=None):
            """Record counts and other-period rows; return a known-reason identifier on a period match."""
            header, rows = parsed
            if header is None: raise _SchemaError(f"schema:{label}:empty response")
            if rows and pcol not in header: raise _SchemaError(f"schema:{label}:missing {pcol}")
            metrics[label] = len(rows)
            for r in rows:
                p = period(r) if period else _to_float(r.get(pcol))
                if p <= 0: continue
                if _period_match(s.period, p): return ident(r)
                found.append((label, ident(r), p))
            return None

        svc = "exoarchive"
        try:
            for table, query, pcol, ident in checks:
                parsed = _csv_rows(self._get(b, "exoarchive", EXO_TAP, {"query": query, "format": "csv"}, ("exoarchive", table, t.target_id)))
                hit = scan(table, parsed, pcol, ident)
                if hit is not None:
                    return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:{table}:{hit}")
            svc = "vizier"
            parsed = _tsv_rows(self._get(b, "vizier", VIZIER, {"-source": "J/AJ/151/68", "-c": f"{t.ra} {t.dec:+}", "-c.rs": self.radius_deg * 3600, "-out.max": 50},
                                         ("vizier", "J/AJ/151/68", t.target_id)))
            hit = scan("kepler_eb", parsed, "Per", lambda r: r.get("KIC"))
            if hit is not None:
                return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:kepler_eb:{hit}")
            if t.gaia_id:
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
