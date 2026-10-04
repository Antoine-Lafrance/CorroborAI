"""Evidence that a learned rule makes sense — used by the UI.

For one field and its S1b rule:
* evidence    — the rule executed row by row against the target (and the documented rule);
* alternatives — the competing hypotheses and why they scored lower;
* stability   — leave-one-out: is the same rule learned when any single row is hidden?
* privacy     — the rule's coverage on pseudonymised data equals its coverage on real data.
"""
from __future__ import annotations

from collections import Counter
from typing import List

import pandas as pd

from . import dsl, privacy
from .common import LearningSet, eq, key
from .evaluate import RULES, oracle


def evidence(ls: LearningSet, field: str, expr: dsl.Expr) -> pd.DataFrame:
    dsts = [d for _, d in ls.pairs]
    y = ls.y(field)
    rows = []
    documented = field in RULES
    verdicts, expected = oracle(ls, field, dsts) if documented else ([None] * len(y), [None] * len(y))
    cols = dsl.used_columns(expr)
    for i, x in enumerate(ls.X):
        out = expr(x)
        rows.append({
            "affectation": ls.label(i),
            **{c: key(x.get(c)) for c in cols},
            "règle apprise →": key(out),
            "valeur cible": key(y[i]),
            "reproduit": eq(out, y[i]),
            "règle documentée →": key(expected[i]) if documented else None,
            "verdict moteur": verdicts[i],
        })
    return pd.DataFrame(rows)


def alternatives(ls: LearningSet, field: str, k: int = 8) -> pd.DataFrame:
    hyps = dsl.synthesize(ls.X, ls.y(field), conditional=True, top_k=0)
    seen, rows = set(), []
    for score, cov, e, _ in hyps:
        if str(e) in seen:
            continue
        seen.add(str(e))
        rows.append({"règle": str(e), "couverture": cov, "complexité": e.complexity, "score": score,
                     "lit la source": dsl.grounded(e)})
        if len(rows) == k:
            break
    return pd.DataFrame(rows)


def stability(ls: LearningSet, field: str) -> pd.DataFrame:
    """Rule learned on each leave-one-out subset."""
    y = ls.y(field)
    rules: List[str] = []
    for i in range(len(y)):
        X_ = ls.X[:i] + ls.X[i + 1:]
        y_ = y[:i] + y[i + 1:]
        rules.append(dsl.learn(field, X_, y_, conditional=True).rule)
    c = Counter(rules)
    return pd.DataFrame([{"règle apprise": r, "plis": n, "part": n / len(rules)} for r, n in c.most_common()])


def privacy_check(ls: LearningSet, field: str, expr: dsl.Expr, seed: int = 7) -> dict:
    y = ls.y(field)
    Xp, yp = privacy.Pseudonymizer(seed=seed).rows(ls.X, y)
    return {"réel": float(dsl._matches(expr, ls.X, y).mean()),
            "pseudonymisé": float(dsl._matches(expr, Xp, yp).mean())}
