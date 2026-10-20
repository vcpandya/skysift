"""Figure for the KIC 6146676 difference-image localisation.
Usage:  python -I make_figure.py <tpf_dir> <out_dir>   (reads <out_dir>/results.json and diffimage_products.npz)"""
import sys, json, warnings
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse
from matplotlib.colors import LogNorm, TwoSlopeNorm
from astropy.io import fits
from astropy.wcs import WCS

warnings.simplefilter("ignore")
TPF_DIR, OUT = Path(sys.argv[1]), Path(sys.argv[2])
R = json.loads((OUT / "results.json").read_text())
Pz = np.load(OUT / "diffimage_products.npz")
STARS = R["inputs"]["stars"]
DEPTH = R["inputs"]["input_depth_ppm"]
Q = {r["quarter"]: r for r in R["quarters"]}
S = R["seasons"]
WCSQ = {}
for f in sorted(TPF_DIR.glob("kplr006146666-*_lpd-targ.fits.gz")):
    with fits.open(f) as h:
        WCSQ[int(h[0].header["QUARTER"])] = WCS(h[2].header)

C_CAND, C_TARG, C_CEN, C_SNR = "#e6a700", "#1f77b4", "#111111", "#2ca02c"


def ellipse(ax, xy, cov, **kw):
    cov = np.asarray(cov)
    if not np.all(np.isfinite(cov)):
        return
    w, v = np.linalg.eigh(cov)
    ang = np.degrees(np.arctan2(v[1, 1], v[0, 1]))
    ax.add_patch(Ellipse(xy, 2 * np.sqrt(max(w[1], 0)), 2 * np.sqrt(max(w[0], 0)), angle=ang, fill=False, **kw))


def marks(ax, rec, show_cen=True):
    (cc, cr), (tc, tr) = rec["pix_candidate_colrow"], rec["pix_target_colrow"]
    ax.plot(cc, cr, marker="*", ms=11, mfc="none", mec=C_CAND, mew=1.6, ls="none")
    ax.plot(tc, tr, marker="X", ms=8, mfc="none", mec=C_TARG, mew=1.4, ls="none")
    if show_cen and "diff_centroid_colrow" in rec:
        x, y = rec["diff_centroid_colrow"]
        ax.plot(x, y, marker="+", ms=10, color=C_CEN, mew=1.6, ls="none")
        ellipse(ax, (x, y), rec["diff_centroid_cov_adopted"], ec=C_CEN, lw=1.0, ls="--")
        sx, sy = rec["snrbox_diff_centroid_colrow"]
        ax.plot(sx, sy, marker="D", ms=5, mfc="none", mec=C_SNR, mew=1.3, ls="none")


def show(ax, img, valid, kind, title):
    im = np.where(valid, img, np.nan)
    ext = (-0.5, im.shape[1] - 0.5, -0.5, im.shape[0] - 0.5)
    if kind == "direct":
        ax.imshow(im, origin="lower", cmap="Greys_r", norm=LogNorm(vmin=max(np.nanmin(im[im > 0]), 10), vmax=np.nanmax(im)), extent=ext)
    else:
        m = np.nanmax(np.abs(im))
        ax.imshow(im, origin="lower", cmap="RdBu_r", norm=TwoSlopeNorm(0, -m, m), extent=ext)
    ax.set_title(title, fontsize=7.5, pad=2)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])


season_rows = {s: sorted(S[s]["quarters"]) for s in sorted(S, key=int)}
fig = plt.figure(figsize=(17, 12.5))
gs = fig.add_gridspec(5, 10, height_ratios=[1, 1, 1, 1, 2.1], hspace=0.28, wspace=0.08)
for i, (s, qs) in enumerate(season_rows.items()):
    for j, q in enumerate(qs):
        rec = Q[q]
        a1 = fig.add_subplot(gs[i, 2 * j]); a2 = fig.add_subplot(gs[i, 2 * j + 1])
        show(a1, Pz[f"q{q}_direct"], Pz[f"q{q}_valid"], "direct", f"Q{q} direct")
        show(a2, Pz[f"q{q}_diff"], Pz[f"q{q}_valid"], "diff", f"Q{q} diff, {rec['n_events_used']} ev, S/N {rec['max_snr']:.1f}")
        marks(a1, rec, show_cen=False); marks(a2, rec)
        if j == 0:
            a1.set_ylabel(f"season {s}", fontsize=9)
    srec = S[s]
    a1 = fig.add_subplot(gs[i, 8]); a2 = fig.add_subplot(gs[i, 9])
    show(a1, np.nan_to_num(Pz[f"s{s}_direct"]), Pz[f"s{s}_valid"], "direct", f"s{s} stack direct")
    show(a2, Pz[f"s{s}_diff"], Pz[f"s{s}_valid"], "diff", f"s{s} stack diff, S/N {srec['max_snr']:.1f}")
    marks(a1, srec, show_cen=False); marks(a2, srec)
    for a in (a1, a2):
        for sp in a.spines.values():
            sp.set_linewidth(2.0)

# ---- summary panel 1: centroid positions on the sky relative to the bright target ----
ax = fig.add_subplot(gs[4, 0:5])
ra_t, de_t = STARS["target"]; ra_c, de_c = STARS["candidate"]
cosd = np.cos(np.radians(de_t))
cand_EN = ((ra_c - ra_t) * cosd * 3600, (de_c - de_t) * 3600)


def to_EN(q, col, row):
    ra, de = np.array(WCSQ[q].all_pix2world([col], [row], 0)).ravel()
    return (ra - ra_t) * cosd * 3600, (de - de_t) * 3600


for q, rec in Q.items():
    if not rec["usable"]:
        continue
    e, n = to_EN(q, *rec["diff_centroid_colrow"])
    ax.plot(e, n, "+", color="#777777", ms=8, mew=1.2, label="per-quarter centroid, box on brightest diff pixel" if q == 1 else None)
    e, n = to_EN(q, *rec["snrbox_diff_centroid_colrow"])
    ax.plot(e, n, "D", mfc="none", mec=C_SNR, ms=5, mew=1.1, label="per-quarter centroid, box on highest-S/N diff pixel" if q == 1 else None)
for s, srec in S.items():
    q0 = srec["quarters"][0]; r0 = Q[q0]
    oc = r0["ccd_origin_colrow"][0] - srec["grid_ccd_origin_colrow"][0]; orow = r0["ccd_origin_colrow"][1] - srec["grid_ccd_origin_colrow"][1]
    c, rr = srec["diff_centroid_colrow"]
    e, n = to_EN(q0, c - oc, rr - orow)
    ax.plot(e, n, "o", color=C_CEN, ms=8, mfc="white", mew=1.6, label="season-stack centroid (brightest-pixel box)" if s == "0" else None)
    ax.annotate(f"s{s}", (e, n), xytext=(5, 5), textcoords="offset points", fontsize=8)
ax.plot(*cand_EN, "*", ms=18, mfc="none", mec=C_CAND, mew=2, ls="none", label="KIC 6146676 (candidate, Gaia)")
ax.plot(0, 0, "X", ms=12, mfc="none", mec=C_TARG, mew=2, ls="none", label="KIC 6146666 (bright target, Gaia)")
for rad in (3.98,):
    ax.add_patch(plt.Circle(cand_EN, rad, fill=False, ls=":", ec=C_CAND, lw=1))
    ax.add_patch(plt.Circle((0, 0), rad, fill=False, ls=":", ec=C_TARG, lw=1))
ax.set_xlabel("offset east of target [arcsec]  (dotted circles: 1 Kepler pixel)"); ax.set_ylabel("offset north of target [arcsec]")
ax.set_aspect("equal"); ax.invert_xaxis(); ax.grid(alpha=0.25)
ax.legend(fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, -0.19), ncol=2, framealpha=0.9)
ax.set_title("Difference-image centroids on the sky (E left)", fontsize=10)

# ---- summary panel 2: implied transit depth on each star (two-source fixed-position fit) ----
ax = fig.add_subplot(gs[4, 6:10])
qs = [q for q in sorted(Q) if Q[q]["usable"]]
x = np.arange(len(qs))
dc = [Q[q]["two_source_fit"]["implied_depth_ppm_candidate"] for q in qs]; ec = [Q[q]["two_source_fit"]["implied_depth_ppm_candidate_err"] for q in qs]
dt = [Q[q]["two_source_fit"]["implied_depth_ppm_target"] for q in qs]; et = [Q[q]["two_source_fit"]["implied_depth_ppm_target_err"] for q in qs]
ax.errorbar(x - 0.12, dc, ec, fmt="*", color=C_CAND, ms=9, capsize=2, label="depth if on candidate")
ax.errorbar(x + 0.12, dt, et, fmt="X", color=C_TARG, ms=7, capsize=2, label="depth if on target")
ax.axhline(DEPTH, color=C_CAND, ls="--", lw=1, label=f"light-curve depth {DEPTH:.0f} ppm")
ax.axhline(DEPTH / 23, color=C_TARG, ls=":", lw=1, label=f"min. target depth if it were the source ({DEPTH/23:.0f} ppm)")
ax.axhline(0, color="#999999", lw=0.8)
sm = R["summary"]
ax.set_xticks(x); ax.set_xticklabels([f"Q{q}" for q in qs], fontsize=8)
ax.set_ylabel("implied fractional depth [ppm]")
ax.set_title(f"Fixed-position two-star fit (Gaussian PSF): candidate {sm['two_source_fit_depth_ppm_candidate_weighted_mean'][0]:.0f} +/- "
             f"{sm['two_source_fit_depth_ppm_candidate_weighted_mean'][1]:.0f} ppm, target {sm['two_source_fit_depth_ppm_target_weighted_mean'][0]:.0f} +/- "
             f"{sm['two_source_fit_depth_ppm_target_weighted_mean'][1]:.0f} ppm", fontsize=9)
ax.legend(fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=2); ax.grid(alpha=0.25, axis="y")

fig.suptitle("KIC 6146676 (P = 40.291 d) - Kepler difference images from the TPFs of KIC 6146666. "
             "Diff = out-of-transit minus in-transit (red = dims in transit). Star = candidate, X = bright target, "
             "+ / dashed = centroid in 3x3 box on brightest diff pixel with 1-sigma ellipse, green diamond = box on highest-S/N pixel. "
             "Images: column right, row up.", fontsize=9, y=0.93, wrap=True)
fig.savefig(OUT / "centroids.png", dpi=130, bbox_inches="tight")
fig.savefig(OUT / "centroids.svg", bbox_inches="tight")
print("wrote", OUT / "centroids.png", OUT / "centroids.svg")
