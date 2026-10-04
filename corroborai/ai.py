"""Layer 3 — AI analysis of what the deterministic rules could not settle, plus prioritisation.

For every field that has at least one ``AMBIGU`` row, two local learners look at the whole
population (no data leaves the machine):

* **S1b rule synthesis** (``learn.dsl``) — searches the rule language for the mapping the target
  actually applies; accepted only if it reproduces ≥ 80 % of rows and reads the source.
* **S4 difference signatures** (``learn.signatures``) — reduces each expected → actual difference
  to its shape and asks whether it is systemic (≥ 50 % of rows) or isolated.

Decision for one ambiguous row (in this order):

1. the learned rule reproduces the target value     → ECART_JUSTIFIE, confidence = coverage × LOO stability
2. the row's difference signature is systemic       → ECART_JUSTIFIE, confidence = signature share
3. a reliable learned rule exists but the row breaks it, or the signature is isolated
                                                     → ANOMALIE, confidence = coverage or 1 − share
4. otherwise                                         → stays AMBIGU (expert review)

Every row then gets a ``Confiance`` (1.0 for a deterministic rule) and anomalies get a
``Priorite`` 0–100 = business impact of the field × confidence, so the report can be sorted.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from .learn import common, dsl, evaluate, proof, signatures
from .tree import AMBIGU, ANOMALIE, ECART_JUSTIFIE

# Business impact of an error per target field (drives the priority score).
IMPACT: Dict[str, tuple] = {
    "(affectation)": (1.0, "Affectation manquante"),
    "contractTypeCode": (0.9, "Paie & horaire"),
    "weeklyHoursOverride": (0.9, "Paie & horaire"),
    "dailyHoursOverride": (0.9, "Paie & horaire"),
    "payGradeId": (0.85, "Paie & horaire"),
    "isPrimaryAssignment": (0.85, "Affectation"),
    "isTemporaryAssignment": (0.85, "Affectation"),
    "assignmentStartDate": (0.8, "Affectation"),
    "assignmentEndDate": (0.8, "Affectation"),
    "termEndDate": (0.8, "Affectation"),
    "detailedStatus": (0.8, "Statut d'emploi"),
    "statusReasonCode": (0.8, "Statut d'emploi"),
    "expectedReturnDate": (0.7, "Statut d'emploi"),
    "positionId": (0.7, "Poste"),
    "positionCode": (0.7, "Poste"),
    "positionName": (0.4, "Poste"),
    "onboardDate": (0.6, "Identité"),
    "divisionId": (0.6, "Organisation"),
    "divisionCode": (0.6, "Organisation"),
    "divisionName": (0.4, "Organisation"),
    "siteCode": (0.6, "Organisation"),
    "siteName": (0.5, "Organisation"),
    "contactEmail": (0.5, "Identité"),
    "givenName": (0.4, "Identité"),
    "surname": (0.4, "Identité"),
}


@dataclass
class FieldModel:
    """What the AI layer learned about one field from the whole population."""
    field: str
    rule: str
    expr: Optional[dsl.Expr]
    coverage: float
    stability: float
    reliable: bool
    predictions: List
    signatures: List[str]
    signature_counts: Counter

    def share(self, sig: str) -> float:
        return self.signature_counts[sig] / max(1, len(self.signatures))


def impact(field: str) -> tuple:
    return IMPACT.get(field, (0.5, "Autre"))


def field_model(ls: common.LearningSet, field: str) -> FieldModel:
    y = ls.y(field)
    _, expected = evaluate.oracle(ls, field, [d for _, d in ls.pairs])
    s1b = dsl.learn(field, ls.X, y, conditional=True)
    sigs = [signatures.signature(e, a) for e, a in zip(expected, y)]
    stab = 0.0
    if not s1b.abstained:
        st = proof.stability(ls, field)
        stab = float(st.loc[st["règle apprise"] == s1b.rule, "part"].sum())
    return FieldModel(field, s1b.rule, s1b.expr, float(s1b.coverage or 0), stab, not s1b.abstained,
                      s1b.predictions or [None] * len(y), sigs, Counter(sigs))


def _resolve(m: FieldModel, i: int, actual) -> dict:
    sig = m.signatures[i]
    share = m.share(sig)
    pred = m.predictions[i]
    rule_txt = f"règle apprise « {m.rule} » (reproduit {m.coverage:.0%} des affectations, stable dans {m.stability:.0%} des plis leave-one-out)"
    if m.reliable and common.eq(pred, actual):
        conf = m.coverage * max(m.stability, 0.5)
        return dict(Verdict=ECART_JUSTIFIE, Confiance=conf, Methode="S1b règle apprise",
                    Justification=f"IA : la cible applique de façon cohérente la {rule_txt} ; "
                                  f"cette ligne la respecte (valeur prédite {common.key(pred)}).")
    if sig != "=" and share >= signatures.THETA:
        return dict(Verdict=ECART_JUSTIFIE, Confiance=share, Methode="S4 signature systémique",
                    Justification=f"IA : écart systémique — la même transformation « {sig} » s'observe sur "
                                  f"{m.signature_counts[sig]}/{len(m.signatures)} affectations "
                                  f"({share:.0%}) ; il s'agit d'une convention de l'interface, pas d'une erreur isolée.")
    if m.reliable:
        return dict(Verdict=ANOMALIE, Confiance=m.coverage, Methode="S1b règle apprise",
                    Justification=f"IA : la {rule_txt} prédit {common.key(pred)}, la cible contient "
                                  f"{common.key(actual)} : écart isolé à investiguer.")
    if sig != "=":
        return dict(Verdict=ANOMALIE, Confiance=1 - share, Methode="S4 signature isolée",
                    Justification=f"IA : écart « {sig} » observé sur seulement {m.signature_counts[sig]}/"
                                  f"{len(m.signatures)} affectations ({share:.0%}) : écart isolé à investiguer.")
    return dict(Verdict=AMBIGU, Confiance=0.0, Methode="aucune",
                Justification="IA : aucune règle fiable ni signature systémique ; validation experte requise.")


def resolve(df: pd.DataFrame, ls: common.LearningSet) -> tuple:
    """Adds AI verdicts to AMBIGU rows. Returns (df, {field: FieldModel})."""
    df = df.copy()
    df["VerdictDeterministe"] = df["Verdict"]
    df["Confiance"] = 1.0
    df["MethodeIA"] = ""
    df["Signature"] = ""
    index = {ls.label(i): i for i in range(len(ls.pairs))}
    models: Dict[str, FieldModel] = {}

    for field in sorted(df.loc[df["Verdict"] == AMBIGU, "ChampCible"].unique()):
        if field not in evaluate.RULES:
            continue
        m = models[field] = field_model(ls, field)
        for r in df.index[(df["ChampCible"] == field)]:
            i = index.get(f"{df.at[r, 'Matricule']}/{df.at[r, 'TypeAffectation']}")
            if i is None:
                continue
            df.at[r, "Signature"] = m.signatures[i]
            if df.at[r, "Verdict"] != AMBIGU:
                continue
            out = _resolve(m, i, ls.y(field)[i])
            df.at[r, "Verdict"] = out["Verdict"]
            df.at[r, "Confiance"] = round(out["Confiance"], 3)
            df.at[r, "MethodeIA"] = out["Methode"]
            df.at[r, "Couche"] = "IA"
            df.at[r, "Justification"] = f"{out['Justification']} — Règle métier : {df.at[r, 'Justification']}"
            df.at[r, "Chemin"] = f"{df.at[r, 'Chemin']} | [IA.{out['Methode'].split()[0]}] {out['Justification']}"
    return df, models


def prioritise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Impact"] = df["ChampCible"].map(lambda f: impact(f)[1])
    sig_counts = df[df["Signature"] != ""].groupby(["ChampCible", "Signature"]).size()
    df["Recurrence"] = [int(sig_counts.get((f, s), 1)) if s else 1 for f, s in zip(df["ChampCible"], df["Signature"])]
    weight = df["ChampCible"].map(lambda f: impact(f)[0])
    df["Priorite"] = 0
    bad = df["Verdict"].isin([ANOMALIE, AMBIGU])
    df.loc[bad, "Priorite"] = (100 * weight[bad] * df.loc[bad, "Confiance"].clip(lower=0.5)).round().astype(int)
    return df

