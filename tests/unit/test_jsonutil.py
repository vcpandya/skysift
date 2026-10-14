import json

import numpy as np
import pytest

from skysift import jsonutil


def test_sanitize_numpy_nonfinite_nested_and_tuples():
    obj = {"f32": np.float32(1.5), "b": np.bool_(True), "i": np.int64(7), "inf": float("inf"), "ninf": -np.inf,
           "nan": np.float64("nan"), "nested": {"t": (1, np.float64(2.0), float("nan")), 3: "int key"},
           "arr": np.array([1.0, np.nan]), "set": {4}}
    out = jsonutil.sanitize(obj)
    assert out == {"f32": 1.5, "b": True, "i": 7, "inf": None, "ninf": None, "nan": None,
                   "nested": {"t": [1, 2.0, None], "3": "int key"}, "arr": [1.0, None], "set": [4]}
    assert type(out["f32"]) is float and type(out["b"]) is bool and type(out["i"]) is int


def test_dumps_is_strict_json():
    text = jsonutil.dumps({"x": float("nan"), "y": [np.float32("inf")]})
    assert text == '{"x": null, "y": [null]}'
    json.loads(text, parse_constant=lambda c: pytest.fail(f"non-standard token {c}"))
    assert jsonutil.dumps({"d": object()}, default=lambda o: "obj") == '{"d": "obj"}'
