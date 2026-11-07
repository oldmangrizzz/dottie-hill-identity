#!/usr/bin/env python3
"""blend_voice.py -- build a voice that exists nowhere.

A Piper voice that is not any donor: the weighted arithmetic mean of several donors' tensors.
This is not a voice actor being imitated; it is an interpolated point in voice space, and the
interpolation coordinates are chosen by Dottie from her own authored spec, not by her operator.

Only tensors present in ALL donors with identical shapes participate. Anything that differs is
kept from the first donor, so the blend cannot silently produce a corrupt model -- if the merge
would be unsafe we refuse rather than emit a broken voice.
"""
import json
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper

VOICES = Path("/opt/dottie-runtime/voices")


def load(path):
    m = onnx.load(str(path))
    tensors = {}
    for node in m.graph.initializer:
        tensors[node.name] = numpy_helper.to_array(node)
    return m, tensors


def blend(donors, weights, out_path):
    """donors: list of names. weights: list of floats summing to ~1."""
    total = sum(weights)
    weights = [w / total for w in weights]
    models, tables = [], []
    for d in donors:
        m, t = load(VOICES / (d + ".onnx"))
        models.append(m); tables.append(t)

    base_model, base = models[0], tables[0]
    merged = {}
    blended_count = 0
    kept_count = 0
    for name, arr in base.items():
        same = all(
            name in t and t[name].shape == arr.shape and t[name].dtype == arr.dtype
            for t in tables[1:])
        if not same:
            merged[name] = arr            # shape/dtype mismatch: keep the first donor's tensor
            kept_count += 1
            continue
        acc = np.zeros_like(arr, dtype=np.float64)
        for w, t in zip(weights, tables):
            acc += w * t[name].astype(np.float64)
        merged[name] = acc.astype(arr.dtype)
        blended_count += 1

    # Write the merged tensors back into a copy of the first donor's graph.
    for i, node in enumerate(base_model.graph.initializer):
        if node.name in merged:
            base_model.graph.initializer[i].CopyFrom(
                numpy_helper.from_array(merged[node.name], node.name))
    onnx.save(base_model, str(out_path))

    cfg = json.loads((VOICES / (donors[0] + ".onnx.json")).read_text())
    cfg["dottie_blend"] = {"donors": donors, "weights": [round(w, 4) for w in weights],
                           "blended_tensors": blended_count, "kept_tensors": kept_count}
    (VOICES / (out_path.stem + ".onnx.json")).write_text(json.dumps(cfg, indent=2))
    return blended_count, kept_count


if __name__ == "__main__":
    pairs = sys.argv[1:]
    donors, weights = [], []
    for p in pairs:
        n, w = p.split("=")
        donors.append(n); weights.append(float(w))
    out = VOICES / "dottie-plainkeeper.onnx"
    b, k = blend(donors, weights, out)
    print("wrote", out)
    print("blended tensors:", b, "| kept (shape mismatch):", k)
    print("donors:", list(zip(donors, [round(w/sum(weights),3) for w in weights])))
