from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from skysift import net
from skysift.config import Thresholds
from skysift.models import LightCurve, Signal, Target, TestResult, TestStatus

log = logging.getLogger(__name__)


@dataclass
class DataBundle:
    lc: LightCurve
    fnorm: np.ndarray
    target: Target
    thresholds: Thresholds
    fnorm_alt: np.ndarray | None = None
    flux_detrended: np.ndarray | None = None    # the detrended flux before pre-whitening (rung 2's SWEET)
    store: object | None = None
    fetch: Callable = net.fetch
    cache: dict = field(default_factory=dict)   # per-field shared data (catalogue cones, etc.)


class Rung(Protocol):
    name: str
    cost: float

    def run(self, signal: Signal, bundle: DataBundle) -> TestResult: ...


class Ladder:
    def __init__(self, rungs: list[Rung], version: str):
        self.rungs = sorted(rungs, key=lambda r: r.cost)
        self.version = version

    def run(self, signal: Signal, bundle: DataBundle | None, on_result=None) -> tuple[str, list[TestResult]]:
        results: list[TestResult] = []
        any_undetermined = False
        for rung in self.rungs:
            t0 = time.perf_counter()
            try:
                r = rung.run(signal, bundle)
                if not isinstance(r, TestResult):
                    log.error("rung %s returned %s, not a TestResult", rung.name, type(r).__name__)
                    r = TestResult(rung=rung.name, status=TestStatus.UNDETERMINED,
                                   reason=f"exception: rung returned {type(r).__name__}")
            except Exception as e:  # a broken test must never pass a signal or kill the sweep
                log.exception("rung %s raised", rung.name)
                r = TestResult(rung=rung.name, status=TestStatus.UNDETERMINED, reason=f"exception: {e!r}")
            dt = time.perf_counter() - t0
            results.append(r)
            if on_result:
                try:
                    on_result(r, dt)
                except Exception:  # a failing progress callback must not abort the vetting of a signal
                    log.exception("on_result callback raised for rung %s", rung.name)
            if r.status == TestStatus.FAIL:
                return "fail", results
            if r.status == TestStatus.UNDETERMINED:
                any_undetermined = True
        return ("undetermined" if any_undetermined else "pass"), results
