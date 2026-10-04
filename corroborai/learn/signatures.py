"""S4 — difference signatures (unsupervised, population-level).

Each (expected → actual) difference is reduced to its *shape*: letters → 'a', digits → '9',
so 'PNom1545850850@…' and 'dev-08-v2_PNom10370370@…' become comparable patterns.

    share(σ) = #rows with signature σ / N
    share ≥ θ  → systemic difference (same transformation for the population) → justified
    share <  θ → isolated difference → anomaly, priority ∝ 1 − share

Needs the expected value from the documented rule; it does not learn a rule, it judges
whether a difference is systemic or isolated.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, List

from .common import Result, eq, key

THETA = 0.5


def shape(v: Any) -> str:
    k = key(v)
    if k is None:
        return "∅"
    k = re.sub(r"[^\W\d_]+", "a", k)
    return re.sub(r"\d+", "9", k)


def signature(expected: Any, actual: Any) -> str:
    return "=" if eq(expected, actual) else f"{shape(expected)} → {shape(actual)}"


def judge(field: str, expected: List[Any], actual: List[Any]) -> Result:
    sigs = [signature(e, a) for e, a in zip(expected, actual)]
    counts = Counter(sigs)
    n = len(sigs)
    flags = [s != "=" and counts[s] / n < THETA for s in sigs]
    diffs = {s: c for s, c in counts.items() if s != "="}
    systemic = [s for s, c in diffs.items() if c / n >= THETA]
    desc = "; ".join(f"« {s} » ×{c}" for s, c in sorted(diffs.items(), key=lambda x: -x[1])) or "aucun écart"
    verdict = (f"écart systémique ({', '.join(systemic)})" if systemic else "écarts isolés") if diffs else "aucun écart"
    conf = max(diffs.values()) / n if systemic else (1 - max(diffs.values(), default=0) / n)
    return Result("S4 Signatures d'écart", field, verdict, None, flags, confidence=conf, notes=desc)
