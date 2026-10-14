"""Strict JSON for everything SkySift stores or prints: Postgres JSON columns and strict parsers reject the NaN and
Infinity tokens that Python's json module emits by default, and rung metrics carry numpy scalars and non-finite values
(rung 2 always reports ``timing_rms_min = NaN`` when it has no timing data)."""
from __future__ import annotations

import json
import math

import numpy as np


def sanitize(obj):
    """Return a JSON-safe copy: numpy scalars become Python scalars, arrays/tuples/sets become lists, non-finite
    floats become None and dict keys become strings. Other objects are returned unchanged."""
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [sanitize(v) for v in obj.tolist()]
    if isinstance(obj, np.generic):
        return sanitize(obj.item())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def dumps(obj, **kw) -> str:
    """``json.dumps`` of the sanitized object with ``allow_nan=False``: the output is always standard JSON."""
    return json.dumps(sanitize(obj), allow_nan=False, **kw)
