from __future__ import annotations

from typing import Protocol

import numpy as np

from skysift.config import Thresholds
from skysift.models import LightCurve, Signal


class Detector(Protocol):
    name: str
    version: str
    cost_class: str

    def detect(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds) -> list[Signal]: ...

    # Optional: signals plus a periodogram summary (``BoxDip.search`` returns a ``SearchResult``). Detectors without
    # it are wrapped by ``pipeline._search``, which calls ``detect`` and reports no periodogram.
    def search(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds, keep_spectrum: bool = False): ...


DETECTORS: dict[str, Detector] = {}


def register(det: Detector) -> Detector:
    DETECTORS[det.name] = det
    return det


def get_detector(name: str) -> Detector:
    import skysift.detectors.box_dip  # noqa: F401  ensure registration
    return DETECTORS[name]
