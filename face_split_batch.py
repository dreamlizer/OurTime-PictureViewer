"""Suggest same-occasion faces after a person split, without changing data."""
from __future__ import annotations

from datetime import datetime

import numpy as np


SAME_FACE = 0.62
CLEAR_MARGIN = 0.10
ANCHOR_GAP_SECONDS = 3 * 60 * 60
BATCH_LIMIT = 24


def _unit(vector):
    if isinstance(vector, (bytes, bytearray, memoryview)):
        values = np.frombuffer(vector, dtype=np.float32).copy()
    else:
        values = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(values))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("empty face vector")
    return values / norm


def _stamp(value):
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        parsed = datetime.fromisoformat(text[:19])
    except ValueError:
        return None
    return parsed.timestamp(), text[:10]


def suggest_split_batch(seed_vectors, positive_vectors, candidates, seed_times=None):
    """Return confirmed same-occasion faces and separate loose lookalikes.

    A face enters the default batch only when it resembles a rejected face,
    is clearly less like the person's retained examples, and was taken on the
    same day within three hours of a rejected face. The rejected faces are the
    only time anchors, so a later lookalike cannot pull in another day.
    """
    seeds = [_unit(vector) for vector in seed_vectors]
    positives = [_unit(vector) for vector in positive_vectors]
    if not seeds or not positives:
        return {"batch": [], "loose": [], "reason": "not_enough_examples"}

    scored = []
    for item in candidates:
        vector = _unit(item["embedding"])
        seed_score = max(float(vector @ seed) for seed in seeds)
        positive_score = max(float(vector @ example) for example in positives)
        when = _stamp(item.get("captured_at"))
        scored.append({
            **{key: value for key, value in item.items() if key != "embedding"},
            "seed_score": seed_score,
            "positive_score": positive_score,
            "margin": seed_score - positive_score,
            "stamp": None if when is None else when[0],
            "day": None if when is None else when[1],
        })

    anchors = []
    for value in seed_times or []:
        parsed = _stamp(value)
        if parsed is not None:
            anchors.append({"stamp": parsed[0], "day": parsed[1]})
    batch = []
    loose = []
    for item in scored:
        same_time = any(
            item["day"] == anchor["day"]
            and abs(item["stamp"] - anchor["stamp"]) <= ANCHOR_GAP_SECONDS
            for anchor in anchors
        )
        if same_time and item["seed_score"] >= SAME_FACE and item["margin"] >= CLEAR_MARGIN:
            batch.append(item)
        elif item["seed_score"] >= SAME_FACE and item["margin"] >= CLEAR_MARGIN:
            loose.append(item)
    batch.sort(key=lambda item: (-item["seed_score"], item["id"]))
    loose.sort(key=lambda item: (-item["seed_score"], item["id"]))
    return {
        "batch": batch[:BATCH_LIMIT],
        "loose": loose[:BATCH_LIMIT],
        "reason": "same_occasion" if batch else "no_confident_batch",
    }
