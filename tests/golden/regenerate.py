"""Rewrite the golden dossier (``tests/golden/kic-006146676-generated.json``) from a copy of the live store.

The golden is never edited by hand (referee pack plan, round 2): each task that changes dossier content runs this
once and commits what it writes. It reaches Gaia through ``net.fetch``, so no test imports it. Run it by path from
the engine root, with the worktree's ``src`` and the worktree itself on ``PYTHONPATH``, so that the main checkout's
``tests`` package does not shadow the worktree's::

    PYTHONUTF8=1 PYTHONPATH=".worktrees/referee-pack/src;.worktrees/referee-pack" .venv/Scripts/python.exe \\
        .worktrees/referee-pack/tests/golden/regenerate.py --engine-root C:/Research/skysift \\
        --data-dir .superpowers/sample-run --target kic-006146676 \\
        --out .worktrees/referee-pack/tests/golden/kic-006146676-generated.json

In this order: the working directory becomes ``--engine-root`` (the stored blob locations are relative to it); the
live ``<data-dir>/engine.db`` is copied with SQLite's backup API (opened read-only) and
``<data-dir>/calibration/kbonus_bkg.jsonl`` copied beside it, into a fresh folder under the system temp directory;
``dossier.generate`` runs on the copy with ``force=True``, reading the light curves from the live blob folder; and
``tools.dossier_compare.golden_block`` of the result is written to ``--out``. The live store and the blob folder are
only read. When the dossier's ``dossier_version`` is not ``dossier.DOSSIER_VERSION`` nothing is written and the exit
code is 1."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

CALIBRATION = Path("calibration") / "kbonus_bkg.jsonl"


def _copy_store(src: Path, dst: Path) -> None:
    """``src`` into ``dst`` through SQLite's backup API, the source opened read-only (a WAL database stays whole)."""
    source = sqlite3.connect(f"file:{src.resolve().as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--engine-root", type=Path, required=True, help="the main checkout the blob paths are relative to")
    ap.add_argument("--data-dir", type=Path, required=True, help="the live data folder, relative to the engine root")
    ap.add_argument("--target", required=True)
    ap.add_argument("--out", type=Path, required=True, help="the golden JSON to write")
    args = ap.parse_args(argv)
    out = args.out.resolve()
    os.chdir(args.engine_root)

    from skysift import dossier, jsonutil, net
    from skysift.config import Settings
    from skysift.store.blob import blob_from_url
    from skysift.store.db import Store
    from tools.dossier_compare import golden_block

    data_dir = args.data_dir
    scratch = Path(tempfile.mkdtemp(prefix="skysift-golden-"))
    _copy_store(data_dir / "engine.db", scratch / "engine.db")
    (scratch / CALIBRATION.parent).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(data_dir / CALIBRATION, scratch / CALIBRATION)
    settings = Settings(data_dir=scratch, database_url=f"sqlite:///{scratch.as_posix()}/engine.db",
                        blob_url=f"file://./{data_dir.as_posix()}/blobs")
    store = Store(settings.database_url)
    blob = blob_from_url(settings.blob_url)
    report = dossier.generate(store, blob, settings, args.target, force=True, fetch=net.fetch, log=print)
    data = json.loads(report.data_path.read_text(encoding="utf-8"))
    if data.get("dossier_version") != dossier.DOSSIER_VERSION:
        print(f"dossier version {data.get('dossier_version')!r} is not {dossier.DOSSIER_VERSION!r}; nothing written",
              file=sys.stderr)
        return 1
    out.write_text(jsonutil.dumps(golden_block(data), indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out} (scratch store {scratch})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
