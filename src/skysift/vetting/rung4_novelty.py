from __future__ import annotations

import csv
import io

from skysift.models import Signal, TestResult, TestStatus
from skysift.net import ServiceUnavailable, looks_like_error_document
from skysift.vetting.base import DataBundle

EXO_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
VIZIER = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"


def _csv_rows(data: bytes) -> list[dict]:
    text = data.decode(errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _tsv_rows(data: bytes) -> list[dict]:
    """VizieR asu-tsv: comment lines, then header, units and dashes lines, then rows."""
    lines = [ln for ln in data.decode(errors="replace").splitlines() if ln.strip() and not ln.startswith("#")]
    if len(lines) < 3: return []
    header = lines[0].split("\t")
    rows = (ln.split("\t") for ln in lines[3:])
    return [dict(zip(header, r)) for r in rows if len(r) == len(header)]


def _period_match(p_signal: float, p_cat: float, tol=0.01) -> bool:
    return any(abs(p_signal - k * p_cat) / (k * p_cat) < tol for k in (0.5, 1.0, 2.0)) if p_cat and p_cat > 0 else False


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
        checks = []
        if t.kic:
            checks.append(("exoarchive", "cumulative", f"select kepid,kepoi_name,koi_period from cumulative where kepid={t.kic}", "koi_period", "kepoi_name"))
            checks.append(("exoarchive", "q1_q17_dr25_tce", f"select kepid,tce_plnt_num,tce_period from q1_q17_dr25_tce where kepid={t.kic}", "tce_period", "kepid"))
        else:
            checks.append(("exoarchive", "cumulative", f"select kepid,kepoi_name,koi_period from cumulative where {cone}", "koi_period", "kepoi_name"))
            checks.append(("exoarchive", "q1_q17_dr25_tce", f"select kepid,tce_plnt_num,tce_period from q1_q17_dr25_tce where {cone}", "tce_period", "kepid"))
        checks.append(("exoarchive", "toi", f"select tid,toi,pl_orbper from toi where {cone}", "pl_orbper", "toi"))
        found, metrics = [], {}
        svc = "exoarchive"
        try:
            for service, table, query, pcol, idcol in checks:
                rows = _csv_rows(self._get(b, service, EXO_TAP, {"query": query, "format": "csv"}, (service, table, t.target_id)))
                metrics[table] = len(rows)
                for r in rows:
                    try: p = float(r.get(pcol) or 0)
                    except ValueError: p = 0.0
                    if _period_match(s.period, p):
                        return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:{table}:{r.get(idcol)}")
                    found.append((table, r.get(idcol), p))
            svc = "vizier"
            eb = _tsv_rows(self._get(b, "vizier", VIZIER, {"-source": "J/AJ/151/68", "-c": f"{t.ra} {t.dec:+}", "-c.rs": self.radius_deg * 3600, "-out.max": 50},
                                     ("vizier", "J/AJ/151/68", t.target_id)))
            metrics["kepler_eb"] = len(eb)
            for r in eb:
                try: p = float(r.get("Per") or r.get("Period") or 0)
                except ValueError: p = 0.0
                if _period_match(s.period, p):
                    return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:kepler_eb:{r.get('KIC')}")
            if t.gaia_id:
                svc = "gaia"
                for table in ("vari_summary", "nss_two_body_orbit"):
                    rows = _csv_rows(self._get(b, "gaia", GAIA_TAP, {"request": "doQuery", "lang": "ADQL", "format": "csv",
                                                                     "query": f"select * from gaiadr3.{table} where source_id={t.gaia_id}"},
                                               ("gaia", table, t.gaia_id)))
                    metrics[f"gaia_{table}"] = len(rows)
                    for r in rows:
                        try: p = float(r.get("period") or 0)
                        except ValueError: p = 0.0
                        if _period_match(s.period, p):
                            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:gaia_{table}:{t.gaia_id}")
        except ServiceUnavailable as e:
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, metrics=metrics, reason=f"outage:{svc}: {e}")
        metrics["other_period_objects"] = found
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=metrics)
