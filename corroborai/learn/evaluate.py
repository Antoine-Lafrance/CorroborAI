"""Compares the rule-learning strategies.

A. Known fields, real data — the documented rule is hidden from the learners and used as
   ground truth: does the learner rediscover it, and does it flag the same anomalies?
B. Known fields, synthetic errors — inject 2 wrong values per field (several seeds); the
   deterministic engine re-labels the corrupted data; precision / recall / F1 per method.
C. Ambiguous fields — what each method concludes where no documented rule decides.
"""
from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List

import pandas as pd

from .. import rules
from ..tree import ANOMALIE, ECART_JUSTIFIE, Context, evaluate
from . import dsl, llm, signatures, tree_induction
from .common import AMBIGUOUS_FIELDS, KNOWN_FIELDS, LearningSet, Result, eq, key, norm

RULES = {fr.target: fr for fr in rules.FIELD_RULES}


def oracle(ls: LearningSet, field: str, dst_rows: List[dict]):
    """Deterministic engine on (possibly corrupted) target rows → (verdicts, expected values)."""
    outs = [evaluate(RULES[field].tree, Context(src=s, dst=d, aux=ls.aux)) for (s, _), d in zip(ls.pairs, dst_rows)]
    return [o.verdict for o in outs], [norm(o.vars.get("expected")) for o in outs]


def run_methods(ls: LearningSet, field: str, y: List[Any], expected: List[Any], with_llm: bool) -> List[Result]:
    s1 = dsl.learn(field, ls.X, y)
    s1b = dsl.learn(field, ls.X, y, conditional=True)
    s2 = tree_induction.learn(field, ls.X, y)
    s4 = signatures.judge(field, expected, y)
    hybrid = Result("H Hybride (S1b, sinon S4)", field,
                    s1b.rule if not s1b.abstained else s4.rule, s1b.coverage if not s1b.abstained else None,
                    s1b.flags if not s1b.abstained else s4.flags, s1b.predictions,
                    notes="règle apprise" if not s1b.abstained else "pas de règle fiable → signatures")
    out = [s1, s1b, s2, s4, hybrid]
    if with_llm:
        out.insert(3, llm.learn(field, ls.X, y))
    return out


def _labels(ls, flags):
    return [ls.label(i) for i, f in enumerate(flags) if f]


# --------------------------------------------------------------------------- A
def experiment_a(ls: LearningSet, with_llm: bool) -> pd.DataFrame:
    rows = []
    for f in KNOWN_FIELDS:
        dsts = [d for _, d in ls.pairs]
        verdicts, expected = oracle(ls, f, dsts)
        truth = [v == ANOMALIE for v in verdicts]
        y = ls.y(f)
        for r in run_methods(ls, f, y, expected, with_llm=False):  # documented fields never go to the LLM
            if r.predictions is not None and not r.abstained:
                # agreement with the documented rule; a value the rule accepts as justified counts too
                agree = sum(eq(p, e) or (v == ECART_JUSTIFIE and eq(p, t))
                            for p, e, v, t in zip(r.predictions, expected, verdicts, y)) / len(expected)
            else:
                agree = None
            rows.append({
                "champ": f, "méthode": r.method, "règle apprise": r.rule.replace("\n", " ⏎ ")[:160],
                "couverture": r.coverage, "accord avec règle documentée": agree,
                "abstention": r.abstained,
                "signalés": ", ".join(_labels(ls, r.flags)), "anomalies réelles": ", ".join(_labels(ls, truth)),
                "VP": sum(a and b for a, b in zip(r.flags, truth)),
                "FP": sum(a and not b for a, b in zip(r.flags, truth)),
                "FN": sum(b and not a for a, b in zip(r.flags, truth)),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- B
def _corrupt(value: Any, column: List[Any], rng: random.Random, date_field: bool) -> Any:
    others = [v for v in column if v is not None and not eq(v, value)]
    if others:
        return rng.choice(others)
    if isinstance(value, bool):
        return not value
    if isinstance(value, float):
        return value + 1
    if isinstance(value, dt.date):
        return value + dt.timedelta(days=31)
    if isinstance(value, str):
        return value + "X"
    return dt.date(2020, 1, 1) if date_field else "X"


def experiment_b(ls: LearningSet, seeds: int = 5, per_field: int = 2) -> pd.DataFrame:
    rows = []
    for seed in range(seeds):
        rng = random.Random(seed)
        for f in KNOWN_FIELDS:
            base_dst = [dict(d) for _, d in ls.pairs]
            y0 = ls.y(f, base_dst)
            verdicts0, _ = oracle(ls, f, base_dst)
            candidates = [i for i, v in enumerate(verdicts0) if v != ANOMALIE]
            injected = rng.sample(candidates, per_field)
            for i in injected:
                base_dst[i][f] = _corrupt(y0[i], y0, rng, date_field="Date" in f)
            verdicts, expected = oracle(ls, f, base_dst)
            truth = [v == ANOMALIE for v in verdicts]
            y = ls.y(f, base_dst)
            for r in run_methods(ls, f, y, expected, with_llm=False):
                rows.append({
                    "graine": seed, "champ": f, "méthode": r.method,
                    "VP": sum(a and b for a, b in zip(r.flags, truth)),
                    "FP": sum(a and not b for a, b in zip(r.flags, truth)),
                    "FN": sum(b and not a for a, b in zip(r.flags, truth)),
                    "injectés détectés": sum(r.flags[i] for i in injected), "injectés": len(injected),
                })
    return pd.DataFrame(rows)


def prf(df: pd.DataFrame, by: List[str]) -> pd.DataFrame:
    g = df.groupby(by)[["VP", "FP", "FN"]].sum()
    g["précision"] = g.VP / (g.VP + g.FP).replace(0, float("nan"))
    g["rappel"] = g.VP / (g.VP + g.FN).replace(0, float("nan"))
    g["F1"] = 2 * g["précision"] * g["rappel"] / (g["précision"] + g["rappel"])
    return g


# --------------------------------------------------------------------------- C
def experiment_c(ls: LearningSet, with_llm: bool) -> pd.DataFrame:
    rows = []
    for f in AMBIGUOUS_FIELDS:
        dsts = [d for _, d in ls.pairs]
        _, expected = oracle(ls, f, dsts)
        for r in run_methods(ls, f, ls.y(f), expected, with_llm):
            rows.append({
                "champ": f, "méthode": r.method, "conclusion": r.rule.replace("\n", " ⏎ ")[:200],
                "couverture": r.coverage, "abstention": r.abstained,
                "nb signalés": sum(r.flags), "signalés": ", ".join(_labels(ls, r.flags)),
                "confiance": r.confidence, "notes": r.notes[:200],
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- report
METHOD_DOC = {
    "S1 DSL": "Recherche exhaustive de règles dans un petit langage (copie, transformations texte, "
              "concaténation, min/max de dates, préfixe/suffixe, constante). Score = couverture − λ·complexité.",
    "S1b DSL + conditions": "S1 + une condition SI colonne = valeur ALORS règle1 SINON règle2.",
    "S2 Arbre de décision (LOO)": "Arbre scikit-learn (profondeur ≤ 3) prédisant la valeur cible ; chaque ligne "
                                  "est jugée par un arbre entraîné sans elle (leave-one-out).",
    "S3 LLM": "Un LLM (API OpenAI) propose une règle dans le langage S1 ; elle n'est retenue que si son exécution "
              "sur les données réelles (en local) atteint la couverture minimale. Champs ambigus uniquement ; "
              "données pseudonymisées (liste blanche de colonnes, identifiants factices, dates décalées).",
    "S4 Signatures d'écart": "N'apprend pas de règle : réduit chaque écart attendu→cible à sa forme et juge "
                             "systémique (≥ 50 % des lignes) ou isolé. Utilise la valeur attendue de la règle documentée.",
    "H Hybride (S1b, sinon S4)": "Règle apprise par S1b si elle est fiable, sinon jugement par signatures.",
}


def _md(df: pd.DataFrame, floatfmt: str = "{:.2f}") -> str:
    def fmt(v):
        if isinstance(v, float):
            return "–" if v != v else floatfmt.format(v)
        return str(v).replace("|", "\\|")
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "---|" * len(df.columns)
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, sep] + body)


def write_report(ls: LearningSet, out_dir: Path, seeds: int = 5, with_llm: bool = False) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    A, B, C = experiment_a(ls, with_llm), experiment_b(ls, seeds), experiment_c(ls, with_llm)
    for name, df in (("A_regles_connues", A), ("B_erreurs_synthetiques", B), ("C_champs_ambigus", C)):
        df.to_csv(out_dir / f"{name}.csv", index=False, encoding="utf-8-sig")

    learners = A[A["accord avec règle documentée"].notna() | A["abstention"]]
    recov = A.groupby("méthode").apply(lambda g: pd.Series({
        "règle retrouvée (accord 100 %)": int((g["accord avec règle documentée"] == 1).sum()),
        "abstentions": int(g["abstention"].sum()),
    })).reset_index()
    pa = prf(A, ["méthode"]).reset_index()
    sa = recov.merge(pa, on="méthode")
    pb = prf(B, ["méthode"]).reset_index()
    inj = B.groupby("méthode")[["injectés détectés", "injectés"]].sum().reset_index()
    inj["taux de détection des erreurs injectées"] = inj["injectés détectés"] / inj["injectés"]
    sb = pb.merge(inj[["méthode", "taux de détection des erreurs injectées"]], on="méthode")
    fb = prf(B, ["champ", "méthode"])["F1"].unstack().reset_index()

    lines = [
        "# Comparaison des stratégies d'apprentissage de règles", "",
        f"{len(ls.pairs)} affectations appariées ; {len(KNOWN_FIELDS)} champs à règle documentée ; "
        f"{len(AMBIGUOUS_FIELDS)} champs ambigus. Erreurs synthétiques : {seeds} graines × 2 valeurs par champ.", "",
        "## Méthodes", "",
        *[f"- **{k}** — {v}" for k, v in METHOD_DOC.items()], "",
        "## A. Champs à règle documentée — données réelles", "",
        "La règle documentée est cachée aux méthodes et sert de vérité terrain. VP/FP/FN : anomalies du moteur déterministe.", "",
        _md(sa), "",
        "Détail par champ :", "",
        _md(A[["champ", "méthode", "règle apprise", "couverture", "accord avec règle documentée", "signalés", "anomalies réelles"]]), "",
        "## B. Erreurs synthétiques injectées", "",
        _md(sb), "",
        "F1 par champ :", "",
        _md(fb), "",
        "## C. Champs ambigus (aucune règle documentée ne tranche)", "",
        _md(C[["champ", "méthode", "conclusion", "couverture", "nb signalés", "confiance", "notes"]]), "",
    ]
    path = out_dir / "comparaison.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
