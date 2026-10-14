from __future__ import annotations

import io

import numpy as np
from astropy.io import fits

from skysift.models import LightCurve


def parse_kbonus_fits(data: bytes) -> LightCurve:
    with fits.open(io.BytesIO(data), memmap=False) as h:
        d = h[1].data; p = h[0].header
        per_q = {}
        for hd in h[2:]:
            if hd.name.startswith("LIGHTCURVE_Q"):
                q = int(hd.header["QUARTER"])
                per_q[q] = {k.lower(): hd.header.get(k) for k in ("FLFRCSAP", "CROWDSAP", "NPIXSAP", "PSFFRAC")}
        meta = {"kic": p.get("KEPLERID"), "gaia_id": str(p.get("GAIAID", "")).replace("Gaia DR3 ", ""),
                "gmag": p.get("GMAG"), "tpf_org": p.get("TPFORG"), "ra": p.get("RA_OBJ"), "dec": p.get("DEC_OBJ"),
                "per_quarter": per_q, "time_system": "BKJD"}
        return LightCurve(time=np.asarray(d["TIME"], float), flux=np.asarray(d["FLUX"], float),
                          flux_err=np.asarray(d["FLUX_ERR"], float), quality=np.asarray(d["SAP_QUALITY"], int),
                          quarter=np.asarray(d["QUARTER"], int), flux_alt=np.asarray(d["SAP_FLUX"], float), meta=meta)
