"""Shared learning set: one feature row per matched assignment pair, plus the target values.

Features = every source column (``src.*``) + aggregates of the position history (``poste.*``)
+ the joined status-reason row (``motif.*``). Learners only see these features and the target
column; the documented rule is used solely to evaluate them.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .. import engine, rules
from ..normalize import clean, fix_mojibake, to_date
from ..tree import Context

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")

# "Description" column of Mapping.xlsx — the only business context a learner may use.
FIELD_DESCRIPTIONS = {
    "givenName": "Prénom de l'employé", "surname": "Nom de l'employé", "contactEmail": "Email de l'employé",
    "onboardDate": "Date d'embauche", "siteName": "Emplacement", "siteCode": "Code d'emplacement",
    "divisionId": "Département", "divisionName": "Département (libellé)", "divisionCode": "Code de département",
    "positionId": "Id du rôle", "positionName": "Nom du rôle", "positionCode": "Code du rôle",
    "statusReasonCode": "Congé Absence Long Terme", "expectedReturnDate": "Date de retour prévu",
    "detailedStatus": "Situation d'emploi (actif / cessation / absence complète)",
    "contractTypeCode": "Type d'employé", "isPrimaryAssignment": "Type d'affectation (primaire)",
    "isTemporaryAssignment": "Type d'affectation (temporaire)", "assignmentStartDate": "Date d'effet du poste",
    "payGradeId": "Niveau du groupe de rémunération", "weeklyHoursOverride": "Nombre d'heures semaine du poste",
    "dailyHoursOverride": "Nombre d'heures par jour du poste", "assignmentEndDate": "Date d'expiration poste",
    "termEndDate": "Date d'effet du détail du poste",
}

AMBIGUOUS_FIELDS = ["contactEmail", "positionName", "weeklyHoursOverride", "dailyHoursOverride"]
KNOWN_FIELDS = [fr.target for fr in rules.FIELD_RULES if fr.target not in AMBIGUOUS_FIELDS]


def norm(v: Any) -> Any:
    """Canonical python value: None / bool / float / datetime.date / str."""
    v = clean(v)
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()
    return to_date(s) if ISO.match(s) else s


def key(v: Any) -> Optional[str]:
    """Comparison key: equal keys ⇔ same value after normalisation (incl. mojibake repair)."""
    v = norm(v)
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    return fix_mojibake(v)


def eq(a: Any, b: Any) -> bool:
    return key(a) == key(b)


def text(v: Any) -> Optional[str]:
    return key(v)


HIST_COLS = ["IdentifiantEmploi", "CodeDirectionAffectée", "CodeBudget", "IndicateurGestion",
             "HeuresSemaineContrat", "HeuresJourContrat", "JoursTravailléesSemaine"]
MOTIF_COLS = ["CodeStatutSystèmeExterne", "CodeGestionAccès"]


def features(s: dict, aux) -> Dict[str, Any]:
    f = {f"src.{k}": norm(v) for k, v in s.items()}
    h = aux.history(s["CodePoste"]).reset_index(drop=True)
    f.update({f"poste.{c}": None for c in HIST_COLS + ["min_eff", "max_eff", "date_chg_unite", "fin_unite"]})
    if not h.empty:
        cur = h.iloc[rules._current_index(h)]
        f.update({f"poste.{c}": norm(cur[c]) for c in HIST_COLS})
        ctx = Context(src=s, dst={}, aux=aux)
        f["poste.min_eff"], f["poste.max_eff"] = h["DateEffet"].min(), h["DateEffet"].max()
        f["poste.date_chg_unite"] = rules._unit_change_date(ctx)
        f["poste.fin_unite"] = rules._unit_end_date(ctx)
    m = aux.motif_row(s["CodeRaisonStatut"]) or {}
    f.update({f"motif.{c}": norm(m.get(c)) for c in MOTIF_COLS})
    return f


@dataclass
class LearningSet:
    pairs: List[tuple]          # (source row, target row)
    X: List[Dict[str, Any]]     # feature dict per pair
    aux: Any

    def y(self, field: str, dst_rows: Optional[List[dict]] = None) -> List[Any]:
        rows = dst_rows if dst_rows is not None else [d for _, d in self.pairs]
        return [norm(d.get(field)) for d in rows]

    def label(self, i: int) -> str:
        s = self.pairs[i][0]
        return f"{s['Matricule']}/{s['TypeAffectation']}"


def build(data_dir: Path) -> LearningSet:
    source, target, aux = engine.load_all(data_dir)
    pairs = [p for _, ps, _, _ in engine.match_all(source, target) for p in ps]
    return LearningSet(pairs, [features(s, aux) for s, _ in pairs], aux)


@dataclass
class Result:
    """What one method concludes for one field."""
    method: str
    field: str
    rule: str                              # learned rule, or the verdict logic used
    coverage: Optional[float]              # share of rows the rule reproduces
    flags: List[bool]                      # rows the method calls an anomaly
    predictions: Optional[List[Any]] = None
    abstained: bool = False                # no rule good enough -> flags nothing
    confidence: Optional[float] = None
    notes: str = ""
    expr: Any = None                       # S1/S1b: the executable rule
