"""S2 — decision-tree induction (scikit-learn) with leave-one-out predictions.

Treats the target column as a class label and learns it from source/history features.
Each row is judged by a tree trained on the *other* rows, so a row cannot vouch for itself:
a row whose LOO prediction differs from its target value is flagged.
Only applicable to categorical targets (≤ MAX_CLASSES distinct values).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, List

import numpy as np
from sklearn.tree import DecisionTreeClassifier, export_text

from .common import Result, key

MAX_CLASSES = 12
MAX_ONEHOT = 15
MAX_DEPTH = 3


def _encode(X: List[dict]):
    cols, names = [], []
    for c in X[0]:
        vals = [r.get(c) for r in X]
        present = [v for v in vals if v is not None]
        if not present:
            continue
        if all(isinstance(v, float) for v in present):
            cols.append([v if v is not None else -1.0 for v in vals]); names.append(c)
        elif all(isinstance(v, dt.date) for v in present):
            cols.append([v.toordinal() if v is not None else -1 for v in vals]); names.append(c)
        else:
            cats = sorted({key(v) for v in present})
            if len(cats) > MAX_ONEHOT:
                continue
            for cat in cats:
                cols.append([1.0 if key(v) == cat else 0.0 for v in vals]); names.append(f"{c} = {cat}")
    return np.array(cols, dtype=float).T, names


def learn(field: str, X: List[dict], y: List[Any]) -> Result:
    name = "S2 Arbre de décision (LOO)"
    labels = [key(v) or "∅" for v in y]
    classes = sorted(set(labels))
    if len(classes) > MAX_CLASSES:
        return Result(name, field, f"non applicable ({len(classes)} valeurs distinctes)", None,
                      [False] * len(y), abstained=True)
    F, names = _encode(X)
    lab = np.array(labels)

    loo = []
    for i in range(len(y)):
        mask = np.arange(len(y)) != i
        if len(set(lab[mask])) == 1:
            loo.append(lab[mask][0])
            continue
        clf = DecisionTreeClassifier(max_depth=MAX_DEPTH, random_state=0).fit(F[mask], lab[mask])
        loo.append(clf.predict(F[i:i + 1])[0])

    full = DecisionTreeClassifier(max_depth=MAX_DEPTH, random_state=0).fit(F, lab)
    rule = export_text(full, feature_names=names, max_depth=MAX_DEPTH).strip() if len(classes) > 1 else f"constante {classes[0]!r}"
    flags = [p != t for p, t in zip(loo, labels)]
    cov = 1 - np.mean(flags)
    preds = [None if p == "∅" else p for p in loo]
    return Result(name, field, rule, float(cov), flags, preds, confidence=float(cov),
                  notes=f"{len(classes)} classes ; profondeur ≤ {MAX_DEPTH}")
