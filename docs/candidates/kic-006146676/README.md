# KIC 6146676 candidate dossier (20 October 2026)

First new candidate from the cool-dwarf sweep: a 40.29109 d, 2,197 ppm transit on an M1 dwarf (Gaia DR3 2073896294004902272, 303 pc) that Kepler never targeted. Published page: https://claude.ai/artifact/X8Kits4gjyeJZPcwaQv2N5 (private).

- `dossier.html`: the page (Normal and Expert levels). Rebuild with `prep_data.py` (merges `lc_data.json`, the engine vet json and spectrum, and the two subagent result files) then `build.py` (injects `dossier_data.json` into `dossier_template.html`). `patch_star.py` and `patch_look.py` record how the star and illustration sections were added to the template.
- `lc_analysis.py`: light-curve numbers and plot arrays from the cached KBonus file using the engine's own functions.
- `localisation/`: difference-image localisation from the KIC 6146666 target pixel files (`REPORT.md`, `results.json`, `centroids.svg`). The 18 TPFs (48 MB) and `diffimage_products.npz` are not committed; `download_tpfs.py` fetches them.
- `context/`: neighbour leak test, Gaia neighbourhood, archive checks and stellar/planet parameters (`REPORT.md`, `results.json`, raw responses in `archive-proof/`). The downloaded Kepler and KBonus light curves (52 MB) are not committed.

The engine's own vet record is `skysift vet --source kbonus_bkg --target-id kic-006146676 --spectrum` under the sample-run data directory.
