"""One place that knows which adapters are live; the CLI and the dashboard both use it."""
from __future__ import annotations

from skysift.sources.base import load_registry


def make_source(name: str, store, blob):
    if name == "kbonus_bkg":
        from skysift.sources.kbonus_bkg import KBonusBkg
        return KBonusBkg(blob, store)
    status = load_registry().get(name)
    raise ValueError(f"source {name} is {'not live yet' if status else 'not live (unknown source)'}; see `skysift sources`")
