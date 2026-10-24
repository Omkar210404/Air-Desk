"""Gestures you teach: landmark features, a small kNN classifier and the config file.

Only landmark numbers are stored (21 points per sample), never camera images.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
from sklearn.neighbors import KNeighborsClassifier

CONFIG_DIR = Path.home() / ".airdesk"
CONFIG_FILE = CONFIG_DIR / "config.json"

SAMPLES_PER_GESTURE = 20
HOLDOUT_FRACTION = 0.25      # last quarter of each recording is kept aside for the check
K = 5
MIN_VOTE = 0.6               # share of neighbours that must agree
Z_WEIGHT = 0.5               # MediaPipe depth is noisier than x/y, so it counts less


def features(lm: np.ndarray, aspect: float, handedness: str = "Right") -> np.ndarray:
    """21 landmarks -> 60 numbers that ignore where the hand is and how big it looks."""
    p = np.asarray(lm, dtype=np.float64).copy()
    p[:, 0] *= aspect                     # x and z are relative to width, y to height
    p[:, 2] *= aspect
    p -= p[0]                             # position: wrist at the origin
    p /= np.linalg.norm(p[9]) + 1e-6      # scale: wrist to middle knuckle = 1
    if handedness == "Left":              # a left hand matches the same right-hand gesture
        p[:, 0] *= -1
    p[:, 2] *= Z_WEIGHT
    return p[1:].ravel()                  # the wrist is always 0, drop it


class CustomModel:
    """k-nearest-neighbours over the stored samples, with a 'no match' rule.

    kNN on its own always names *some* gesture. To avoid firing on every hand
    shape, a frame only matches if its nearest sample is about as close as the
    taught samples are to each other.
    """

    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X, self.y = X, y
        self.k = min(K, len(X))
        self.knn = KNeighborsClassifier(n_neighbors=self.k, weights="distance").fit(X, y)
        self.threshold = _match_threshold(X, y)

    @classmethod
    def from_gestures(cls, gestures: list) -> "CustomModel | None":
        X, y = [], []
        for g in gestures:
            for s in g["samples"]:
                X.append(s)
                y.append(g["name"])
        if len(X) < 2:
            return None
        return cls(np.asarray(X, dtype=np.float64), np.asarray(y))

    def predict(self, f: np.ndarray):
        """(name, confidence) for a match, else None."""
        dist, idx = self.knn.kneighbors(f[None], n_neighbors=self.k)
        dist, idx = dist[0], idx[0]
        if dist[0] > self.threshold:
            return None
        w = 1.0 / (dist + 1e-6)
        votes: dict = {}
        for name, weight in zip(self.y[idx], w):
            votes[name] = votes.get(name, 0.0) + weight
        name = max(votes, key=votes.get)
        share = votes[name] / sum(votes.values())
        if share < MIN_VOTE:
            return None
        closeness = 1.0 - 0.5 * dist[0] / self.threshold
        return str(name), float(share * closeness)


def _match_threshold(X: np.ndarray, y: np.ndarray) -> float:
    """How far a frame may be from the nearest sample and still count as a match."""
    gaps = []
    for name in np.unique(y):
        A = X[y == name]
        if len(A) < 2:
            continue
        d = np.linalg.norm(A[:, None, :] - A[None, :, :], axis=2)
        np.fill_diagonal(d, np.inf)
        gaps.extend(d.min(axis=1))
    if not gaps:
        return 0.6
    return float(np.clip(2.0 * np.percentile(gaps, 90), 0.45, 1.0))


def holdout_check(gestures: list) -> dict:
    """Train on the first 75% of each recording, test on the last 25%.

    Returns {"per": {name: (correct, total)}, "correct": n, "total": n}.
    Samples are recorded in order, so the held-out ones are the latest frames,
    not random picks next to training frames.
    """
    train, test = [], []
    for g in gestures:
        s = g["samples"]
        n_test = max(1, math.ceil(len(s) * HOLDOUT_FRACTION)) if len(s) >= 4 else 0
        train.append({"name": g["name"], "samples": s[:len(s) - n_test]})
        test.extend((g["name"], x) for x in s[len(s) - n_test:])
    model = CustomModel.from_gestures(train)
    per: dict = {g["name"]: [0, 0] for g in gestures}
    for name, x in test:
        hit = model.predict(np.asarray(x)) if model else None
        per[name][1] += 1
        per[name][0] += int(hit is not None and hit[0] == name)
    correct = sum(c for c, _ in per.values())
    total = sum(t for _, t in per.values())
    return {"per": {k: tuple(v) for k, v in per.items()}, "correct": correct, "total": total}


# -- config file ----------------------------------------------------------------
def load_config() -> dict:
    default = {"version": 1, "gestures": []}
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        cfg.setdefault("gestures", [])
        return cfg
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        # Keep the broken file for the user instead of silently overwriting it.
        try:
            CONFIG_FILE.replace(CONFIG_FILE.with_suffix(".broken.json"))
        except OSError:
            pass
        return default


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)        # atomic: never leaves a half-written file


def round_sample(f: np.ndarray) -> list:
    return [round(float(v), 4) for v in f]
