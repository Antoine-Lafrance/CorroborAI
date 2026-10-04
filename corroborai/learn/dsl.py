"""S1 / S1b — rule synthesis by enumerative search over a small rule language.

    φ* = argmax_φ  coverage(φ) − λ·complexity(φ),   accepted iff coverage(φ*) ≥ τ

The language mirrors how Mapping.xlsx states rules: copy a column, apply a text transform,
concatenate with a separator, take the min/max of two dates, wrap with a constant
prefix/suffix, use a constant, and (S1b only) branch on one low-cardinality column.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from ..normalize import strip_accents
from .common import Result, eq, key, text

LAMBDA = 0.02          # complexity penalty per node
TAU = 0.80             # minimum coverage to accept a rule
MIN_BRANCH = 2         # S1b: each branch of a condition must cover ≥ 2 rows
SEPARATORS = ["", "-"]
AFFIX_CHARS = "_-@."


# --------------------------------------------------------------------------- expressions
class Expr:
    complexity: float = 1.0

    def __call__(self, row: Dict[str, Any]) -> Any:
        raise NotImplementedError

    def to_json(self) -> dict:
        raise NotImplementedError


class Col(Expr):
    def __init__(self, name: str):
        self.name, self.complexity = name, 1.0

    def __call__(self, row):
        return row.get(self.name)

    def __str__(self):
        return self.name

    def to_json(self):
        return {"op": "col", "name": self.name}


class Const(Expr):
    def __init__(self, value: Any):
        self.value, self.complexity = value, 1.5   # slightly dearer: explains nothing about the source

    def __call__(self, row):
        return self.value

    def __str__(self):
        return repr(key(self.value))

    def to_json(self):
        return {"op": "const", "value": key(self.value)}


FNS: Dict[str, Callable[[str], Optional[str]]] = {
    "strip_accents": strip_accents,
    "lower": str.lower,
    "upper": str.upper,
    "first_char": lambda s: s[:1],
    "last3": lambda s: s[-3:],
    "zpad5": lambda s: s.zfill(5) if s.isdigit() else None,
}


NUM_FNS = ["first_char", "last3", "zpad5"]


class Fn(Expr):
    def __init__(self, fn: str, arg: Expr):
        self.fn, self.arg, self.complexity = fn, arg, arg.complexity + 1

    def __call__(self, row):
        t = text(self.arg(row))
        return None if t is None else FNS[self.fn](t)

    def __str__(self):
        return f"{self.fn}({self.arg})"

    def to_json(self):
        return {"op": "fn", "fn": self.fn, "arg": self.arg.to_json()}


class Concat(Expr):
    def __init__(self, sep: str, parts: Sequence[Expr]):
        self.sep, self.parts = sep, list(parts)
        self.complexity = sum(p.complexity for p in parts) + 1

    def __call__(self, row):
        vals = [text(p(row)) for p in self.parts]
        return None if any(v is None for v in vals) else self.sep.join(vals)

    def __str__(self):
        return f" + {self.sep!r} + ".join(str(p) for p in self.parts) if self.sep else " + ".join(map(str, self.parts))

    def to_json(self):
        return {"op": "concat", "sep": self.sep, "args": [p.to_json() for p in self.parts]}


class DateAgg(Expr):
    def __init__(self, op: str, a: Expr, b: Expr):
        self.op, self.a, self.b = op, a, b
        self.complexity = a.complexity + b.complexity + 1

    def __call__(self, row):
        vals = [v for v in (self.a(row), self.b(row)) if isinstance(v, dt.date)]
        if not vals:
            return None
        return min(vals) if self.op == "min" else max(vals)

    def __str__(self):
        return f"{self.op}({self.a}, {self.b})"

    def to_json(self):
        return {"op": f"date_{self.op}", "args": [self.a.to_json(), self.b.to_json()]}


class Affix(Expr):
    def __init__(self, prefix: str, core: Expr, suffix: str):
        self.prefix, self.core, self.suffix = prefix, core, suffix
        self.complexity = core.complexity + 0.5 * (bool(prefix) + bool(suffix))

    def __call__(self, row):
        t = text(self.core(row))
        return None if t is None else f"{self.prefix}{t}{self.suffix}"

    def __str__(self):
        parts = ([repr(self.prefix)] if self.prefix else []) + [str(self.core)] + ([repr(self.suffix)] if self.suffix else [])
        return " + ".join(parts)

    def to_json(self):
        return {"op": "affix", "prefix": self.prefix, "suffix": self.suffix, "core": self.core.to_json()}


class IfEq(Expr):
    def __init__(self, col: str, value: Any, then: Expr, other: Expr):
        self.col, self.value, self.then, self.other = col, value, then, other
        self.complexity = 1 + then.complexity + other.complexity

    def __call__(self, row):
        return self.then(row) if eq(row.get(self.col), self.value) else self.other(row)

    def __str__(self):
        return f"SI {self.col} = {key(self.value)!r} ALORS {self.then} SINON {self.other}"

    def to_json(self):
        return {"op": "if_eq", "col": self.col, "value": key(self.value),
                "then": self.then.to_json(), "else": self.other.to_json()}


def from_json(j: dict) -> Expr:
    """Builds an expression from JSON (used to verify LLM proposals)."""
    op = j["op"]
    if op == "col":
        return Col(j["name"])
    if op == "const":
        return Const(j["value"])
    if op == "fn":
        if j["fn"] not in FNS:
            raise ValueError(f"fonction inconnue {j['fn']}")
        return Fn(j["fn"], from_json(j["arg"]))
    if op == "concat":
        return Concat(j.get("sep", ""), [from_json(a) for a in j["args"]])
    if op in ("date_min", "date_max"):
        a, b = j["args"]
        return DateAgg(op[5:], from_json(a), from_json(b))
    if op == "affix":
        return Affix(j.get("prefix", ""), from_json(j["core"]), j.get("suffix", ""))
    if op == "if_eq":
        return IfEq(j["col"], j["value"], from_json(j["then"]), from_json(j["else"]))
    raise ValueError(f"opération inconnue {op}")


# --------------------------------------------------------------------------- search
def _col_types(X: List[dict]) -> Dict[str, str]:
    types = {}
    for c in X[0]:
        vals = [r[c] for r in X if r.get(c) is not None]
        if not vals:
            types[c] = "empty"
        elif all(isinstance(v, bool) for v in vals):
            types[c] = "bool"
        elif all(isinstance(v, dt.date) for v in vals):
            types[c] = "date"
        elif all(isinstance(v, float) for v in vals):
            types[c] = "num"
        else:
            types[c] = "str"
    return types


def _affix(y: List[Any]):
    """Longest common prefix/suffix of the target strings, cut at a separator."""
    ys = [v for v in y if isinstance(v, str)]
    if len(ys) < 2 or len(ys) != len([v for v in y if v is not None]):
        return "", ""
    pre = os.path.commonprefix(ys)
    suf = os.path.commonprefix([s[::-1] for s in ys])[::-1]
    cut = max((pre.rfind(ch) for ch in AFFIX_CHARS), default=-1)
    pre = pre[:cut + 1] if cut >= 0 else ""
    cuts = [suf.find(ch) for ch in AFFIX_CHARS if suf.find(ch) >= 0]
    suf = suf[min(cuts):] if cuts else ""
    return pre, suf


def _strip(v, pre: str, suf: str):
    if not isinstance(v, str) or not v.startswith(pre) or not v.endswith(suf) or len(v) < len(pre) + len(suf):
        return None
    return v[len(pre):len(v) - len(suf)] if suf else v[len(pre):]


def _matches(expr: Expr, X: List[dict], y: List[Any]) -> np.ndarray:
    return np.array([eq(expr(r), t) for r, t in zip(X, y)], dtype=bool)


def _base_candidates(X: List[dict], y: List[Any], types: Dict[str, str]) -> List[Expr]:
    cands: List[Expr] = []
    for c, t in types.items():
        cands.append(Col(c))
        if t == "str":
            cands += [Fn(f, Col(c)) for f in FNS]
        elif t == "num":
            cands += [Fn(f, Col(c)) for f in NUM_FNS]
    dates = [c for c, t in types.items() if t == "date"]
    for i, a in enumerate(dates):
        for b in dates[i + 1:]:
            cands += [DateAgg("min", Col(a), Col(b)), DateAgg("max", Col(a), Col(b))]
    distinct = {key(v): v for v in y}
    if len(distinct) <= 12:
        cands += [Const(v) for v in distinct.values()]
    return cands


def _concat_beam(X, y_core, textual: List[Expr], max_parts: int = 3, beam: int = 40) -> List[Expr]:
    """Grows concatenations left to right, keeping those that stay a prefix of the target."""
    n = len(y_core)

    def prefix_rate(expr):
        ok = 0
        for r, t in zip(X, y_core):
            v = text(expr(r))
            ok += isinstance(t, str) and v is not None and t.startswith(v) and v != ""
        return ok / n

    starters = sorted(((prefix_rate(e), e) for e in textual), key=lambda p: -p[0])
    frontier = [e for r, e in starters[:beam] if r >= 0.5]
    out: List[Expr] = []
    for _ in range(max_parts - 1):
        nxt = []
        for left in frontier:
            for sep in SEPARATORS:
                for right in textual:
                    cand = Concat(sep, (left.parts if isinstance(left, Concat) and left.sep == sep else [left]) + [right])
                    r = prefix_rate(cand)
                    if r >= 0.5:
                        nxt.append((r, cand))
        nxt.sort(key=lambda p: (-p[0], p[1].complexity))
        frontier = [e for _, e in nxt[:beam]]
        out += frontier
    return out


def synthesize(X: List[dict], y: List[Any], conditional: bool = False, top_k: int = 5):
    """Returns the ranked hypotheses [(score, coverage, expr, match_vector)]."""
    n = len(y)
    types = _col_types(X)
    base = _base_candidates(X, y, types)
    textual = [e for e in base if not isinstance(e, (DateAgg, Const))
               and types.get(getattr(e, "name", getattr(getattr(e, "arg", None), "name", "")), "") in ("str", "num")]

    pre, suf = _affix(y)
    variants = [("", "")] + ([(pre, suf)] if (pre or suf) else [])
    pool: List[Expr] = list(base)
    for p, s in variants:
        y_core = [_strip(v, p, s) for v in y] if (p or s) else y
        concats = _concat_beam(X, y_core, textual) if any(isinstance(v, str) for v in y_core) else []
        wrapped = textual + concats
        if p or s:
            pool += [Affix(p, e, s) for e in wrapped]
        else:
            pool += concats

    scored = []
    for e in pool:
        m = _matches(e, X, y)
        cov = m.mean()
        scored.append((cov - LAMBDA * e.complexity, cov, e, m))

    if conditional:
        scored += _conditional(X, y, types, scored)

    scored.sort(key=lambda t: (-t[0], t[2].complexity))
    return scored[:top_k] if top_k else scored


def _conditional(X, y, types, scored) -> list:
    """S1b: SI col = v ALORS e1 SINON e2, each branch picks its best expression."""
    n = len(y)
    simple = [t for t in scored if t[2].complexity <= 3]
    M = np.array([t[3] for t in simple])               # candidates × rows
    cx = np.array([t[2].complexity for t in simple])
    out = []
    for c, t in types.items():
        vals = {key(r.get(c)): r.get(c) for r in X if r.get(c) is not None}
        if not 2 <= len(vals) <= 6:
            continue
        for v in vals.values():
            mask = np.array([eq(r.get(c), v) for r in X])
            if mask.sum() < MIN_BRANCH or (~mask).sum() < MIN_BRANCH:
                continue
            s_in = M[:, mask].sum(1) / n - LAMBDA * cx
            s_out = M[:, ~mask].sum(1) / n - LAMBDA * cx
            i, j = int(s_in.argmax()), int(s_out.argmax())
            if str(simple[i][2]) == str(simple[j][2]):
                continue
            expr = IfEq(c, v, simple[i][2], simple[j][2])
            m = np.where(mask, M[i], M[j])
            cov = m.mean()
            out.append((cov - LAMBDA * expr.complexity, cov, expr, m))
    return out


def grounded(expr: Expr) -> bool:
    """A mapping must read the source: pure constants are only a fallback."""
    if isinstance(expr, Const):
        return False
    if isinstance(expr, IfEq):
        return str(expr.then) != str(expr.other) or grounded(expr.then)
    return True


def learn(field: str, X: List[dict], y: List[Any], conditional: bool = False) -> Result:
    hyps = synthesize(X, y, conditional=conditional, top_k=0)
    name = "S1b DSL + conditions" if conditional else "S1 DSL"
    if not hyps:
        return Result(name, field, "(aucune hypothèse)", None, [False] * len(y), abstained=True)
    ok = [h for h in hyps if h[1] >= TAU and grounded(h[2])]
    best = ok[0] if ok else hyps[0]
    score, cov, expr, m = best
    alts = " ; ".join(f"{e} ({c:.0%})" for _, c, e, _ in [h for h in hyps if h is not best][:2])
    preds = [expr(r) for r in X]
    if cov < TAU:
        return Result(name, field, str(expr), float(cov), [False] * len(y), preds, abstained=True,
                      notes=f"couverture < {TAU:.0%} : règle rejetée. Alternatives : {alts}", expr=expr)
    return Result(name, field, str(expr), float(cov), [not x for x in m], preds, confidence=float(cov),
                  notes=f"Alternatives : {alts}", expr=expr)


def used_columns(expr: Expr) -> List[str]:
    cols: List[str] = []

    def walk(j):
        if isinstance(j, dict):
            for k in ("name", "col"):
                if k in j and j.get("op") in ("col", "if_eq") and j[k] not in cols:
                    cols.append(j[k])
            for v in j.values():
                walk(v)
        elif isinstance(j, list):
            for v in j:
                walk(v)

    walk(expr.to_json())
    return cols
