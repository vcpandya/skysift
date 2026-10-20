"""B + C: archive queries. KIC 5689121. Every response is saved verbatim in archive-proof/, with the exact URL in manifest.json."""
import json, pathlib, hashlib, sys
import requests
from skysift.net import fetch, ServiceUnavailable
HERE = pathlib.Path(__file__).parent; PROOF = HERE / "archive-proof"
RA, DEC = 285.90782692874626, 40.94198844296448
CAND_GAIA, NB_GAIA = 2103792461826460288, 2103792427466721792
R_DEG = 30 / 3600
GAIA = "https://gea.esac.esa.int/tap-server/tap/sync"
NEA = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
VIZ = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
man_path = PROOF / "manifest.json"
manifest = json.loads(man_path.read_text()) if man_path.exists() else {}

def q(name, url, params, service, ext):
    full = requests.Request("GET", url, params=params).prepare().url
    try:
        data = fetch(url, params=params, service=service, timeout=300); err = None
    except ServiceUnavailable as e:
        data = b""; err = str(e)
    out = PROOF / f"{name}.{ext}"
    out.write_bytes(data)
    manifest[name] = {"url": full, "file": out.name, "bytes": len(data), "error": err,
                      "sha256": hashlib.sha256(data).hexdigest()}
    print(name, len(data), err or "")

def gaia(name, adql): q(name, GAIA, {"request": "doQuery", "lang": "ADQL", "format": "csv", "query": adql}, "gaia-tap", "csv")
def nea(name, adql): q(name, NEA, {"query": adql, "format": "csv"}, "nasa-exoplanet-archive", "csv")
def viz(name, source, extra=None, radius=30):
    p = {"-source": source, "-c": f"{RA} {DEC:+}", "-c.rs": str(radius), "-out.all": "", "-out.add": "_r", "-sort": "_r"}
    p.update(extra or {}); q(name, VIZ, p, "vizier", "tsv")

which = sys.argv[1:] or ["gaia", "nea", "viz", "simbad"]
cone = f"1=CONTAINS(POINT(ra,dec),CIRCLE({RA},{DEC},{R_DEG:.8f}))"
sep = f"DISTANCE(POINT(ra,dec),POINT({RA},{DEC}))*3600 AS sep_arcsec"
if "gaia" in which:
    gaia("B_gaia_cone30", f"SELECT source_id,ra,dec,{sep},phot_g_mean_mag,bp_rp,parallax,parallax_error,parallax_over_error,"
         f"pmra,pmdec,ruwe,phot_variable_flag,non_single_star FROM gaiadr3.gaia_source WHERE {cone} ORDER BY sep_arcsec")
    gaia("B_gaia_source_candidate", f"SELECT * FROM gaiadr3.gaia_source WHERE source_id={CAND_GAIA}")
    gaia("B_gaia_source_neighbour", f"SELECT source_id,ra,dec,phot_g_mean_mag,bp_rp,parallax,parallax_error,ruwe,radial_velocity,"
         f"teff_gspphot,phot_variable_flag,non_single_star FROM gaiadr3.gaia_source WHERE source_id={NB_GAIA}")
    gaia("B_gaia_astrophys_candidate", f"SELECT * FROM gaiadr3.astrophysical_parameters WHERE source_id={CAND_GAIA}")
    for t in ("vari_summary", "vari_classifier_result", "nss_two_body_orbit", "vari_eclipsing_binary"):
        gaia(f"B_gaia_{t}_candidate", f"SELECT * FROM gaiadr3.{t} WHERE source_id={CAND_GAIA}")
    # same variability / NSS tables for every source inside the 30 arcsec cone
    sub = f"SELECT source_id FROM gaiadr3.gaia_source WHERE {cone}"
    for t in ("vari_summary", "vari_classifier_result", "nss_two_body_orbit", "vari_eclipsing_binary"):
        gaia(f"B_gaia_{t}_cone30", f"SELECT t.* FROM gaiadr3.{t} AS t WHERE t.source_id IN ({sub})")
if "nea" in which:
    dra = R_DEG / 0.7554  # cos(40.942 deg)
    box = f"ra between {RA-dra:.6f} and {RA+dra:.6f} and dec between {DEC-R_DEG:.6f} and {DEC+R_DEG:.6f}"
    nea("C_nea_cumulative_box", f"select kepid,kepoi_name,kepler_name,koi_disposition,koi_pdisposition,koi_period,koi_time0bk,koi_depth,koi_duration,ra,dec from cumulative where {box}")
    nea("C_nea_cumulative_kepid", "select kepid,kepoi_name,kepler_name,koi_disposition,koi_period,koi_depth,ra,dec from cumulative where kepid in (5689116,5689121)")
    nea("C_nea_dr25tce_box", f"select * from q1_q17_dr25_tce where {box}")
    nea("C_nea_dr25tce_kepid", "select * from q1_q17_dr25_tce where kepid in (5689116,5689121)")
    nea("C_nea_dr24tce_kepid", "select * from q1_q17_dr24_tce where kepid in (5689116,5689121)")
    nea("C_nea_toi_box", f"select * from toi where {box}")
    nea("C_nea_dr25stellar_kepid", "select * from q1_q17_dr25_stellar where kepid in (5689116,5689121)")
    nea("C_nea_keplerstellar_kepid", "select kepid,teff,logg,radius,mass,kepmag,ra,dec from keplerstellar where kepid in (5689116,5689121)")
if "viz" in which:
    viz("C_vizier_keplerEB_kirk2016", "J/AJ/151/68/catalog")
    viz("C_vizier_KIC", "V/133/kic")
    viz("C_vizier_TICv82", "IV/39/tic82")
    viz("C_vizier_planethunters_schwamb2012", "J/ApJ/754/129")   # PH II: short-period candidates from Q1
    viz("C_vizier_planethunters_lintott2013", "J/AJ/145/151")    # PH: new candidates from Q2
    viz("C_vizier_planethunters_wang2013", "J/ApJ/776/10")       # PH V: 42 candidates (not in VizieR last time)
    viz("C_vizier_planethunters_schmitt2014_PH6", "J/AJ/148/28") # PH VI: long-period candidates
    viz("C_vizier_planethunters_schmitt2014", "J/ApJ/795/167")   # PH VII
    viz("C_vizier_kepler_fpp_morton2016", "J/ApJ/822/86")
if "simbad" in which:
    q("C_simbad_cone30", "https://simbad.cds.unistra.fr/simbad/sim-coo",
      {"Coord": f"{RA} {DEC:+}", "Radius": "30", "Radius.unit": "arcsec", "output.format": "ASCII", "list.idsel": "on"},
      "simbad", "txt")
man_path.write_text(json.dumps(manifest, indent=1))
