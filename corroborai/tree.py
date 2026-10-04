"""Hand-built decision trees with full decision-path tracing.

A rule is a tree of three node kinds:

* ``Compute`` – derives a value (e.g. the expected target value) and stores it in
  ``ctx.vars``, then continues to its single child.
* ``Node``    – asks a question about the context and follows the matching branch.
* ``Leaf``    – emits a verdict, the rule id, and a human-readable justification.

Every node visited is recorded as a ``Step`` so each verdict carries the exact
path that produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Hashable, List, Optional, Union

CONFORME = "CONFORME"
ECART_JUSTIFIE = "ECART_JUSTIFIE"
ANOMALIE = "ANOMALIE"
AMBIGU = "AMBIGU"  # not resolvable by a deterministic rule -> AI layer
VERDICTS = (CONFORME, ECART_JUSTIFIE, ANOMALIE, AMBIGU)


@dataclass
class Context:
    src: Dict[str, Any]          # source (System A - RH) row
    dst: Dict[str, Any]          # target (System B - Temps) row
    aux: Any                     # lookups: position history, motif table, dataset-level indexes
    vars: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Step:
    node_id: str
    question: str
    answer: str

    def __str__(self) -> str:
        return f"[{self.node_id}] {self.question} → {self.answer}"


@dataclass
class Outcome:
    verdict: str
    rule_id: str
    explanation: str
    path: List[Step]
    vars: Dict[str, Any]
    layer: str = "DETERMINISTE"

    @property
    def path_str(self) -> str:
        return " | ".join(str(s) for s in self.path)


def _fmt(v: Any) -> str:
    if v is None:
        return "∅"
    if isinstance(v, bool):
        return "oui" if v else "non"
    return str(v)


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


TreeT = Union["Node", "Compute", "Leaf"]


class Leaf:
    def __init__(self, verdict: str, rule_id: str, explanation: str):
        assert verdict in VERDICTS, verdict
        self.verdict, self.rule_id, self.explanation = verdict, rule_id, explanation

    def eval(self, ctx: Context, path: List[Step]) -> Outcome:
        shown = _SafeDict({k: _fmt(v) for k, v in ctx.vars.items()})
        return Outcome(self.verdict, self.rule_id, self.explanation.format_map(shown), path, dict(ctx.vars))

    def describe(self, indent: str = "") -> List[str]:
        return [f"{indent}⇒ {self.verdict} ({self.rule_id})"]


class Compute:
    def __init__(self, node_id: str, label: str, var: str, fn: Callable[[Context], Any], then: TreeT):
        self.node_id, self.label, self.var, self.fn, self.then = node_id, label, var, fn, then

    def eval(self, ctx: Context, path: List[Step]) -> Outcome:
        ctx.vars[self.var] = self.fn(ctx)
        path.append(Step(self.node_id, self.label, f"{self.var} = {_fmt(ctx.vars[self.var])}"))
        return self.then.eval(ctx, path)

    def describe(self, indent: str = "") -> List[str]:
        return [f"{indent}• {self.label}  [{self.var}]"] + self.then.describe(indent)


class Node:
    def __init__(
        self,
        node_id: str,
        question: str,
        test: Callable[[Context], Hashable],
        branches: Dict[Hashable, TreeT],
        default: Optional[TreeT] = None,
    ):
        self.node_id, self.question, self.test = node_id, question, test
        self.branches, self.default = branches, default

    def eval(self, ctx: Context, path: List[Step]) -> Outcome:
        answer = self.test(ctx)
        path.append(Step(self.node_id, self.question, _fmt(answer)))
        child = self.branches.get(answer, self.default)
        if child is None:
            return Outcome(
                AMBIGU, f"{self.node_id}-NON-COUVERT",
                f"Aucune branche de règle ne couvre la réponse {_fmt(answer)!r} à « {self.question} ».",
                path, dict(ctx.vars),
            )
        return child.eval(ctx, path)

    def describe(self, indent: str = "") -> List[str]:
        lines = [f"{indent}? {self.question}"]
        items = list(self.branches.items()) + ([("(autre)", self.default)] if self.default else [])
        for ans, child in items:
            lines.append(f"{indent}  ├─ {_fmt(ans)}:")
            lines += child.describe(indent + "  │   ")
        return lines


def evaluate(tree: TreeT, ctx: Context) -> Outcome:
    return tree.eval(ctx, [])
