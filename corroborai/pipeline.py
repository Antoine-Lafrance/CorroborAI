"""Full corroboration pipeline:

    1. raw comparison + normalisation   (normalize.py, engine.py)
    2. business rules                   (rules.py → CONFORME / ECART_JUSTIFIE / ANOMALIE / AMBIGU)
    3. AI analysis of AMBIGU rows       (ai.py → verdict, confidence, method)
    4. expert overrides                 (out/expert_overrides.jsonl → Couche = EXPERT)
    5. prioritisation of what remains to investigate
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from . import ai, engine
from .learn import common, signatures
from .tree import AMBIGU, ANOMALIE, CONFORME, ECART_JUSTIFIE, VERDICTS

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "out" / "expert_overrides.jsonl"
COLUMNS = ["Id", "Priorite", "Verdict", "Couche", "Confiance", "Matricule", "Nom", "TypeAffectation", "CodePoste",
           "CodeEmploi", "ChampCible", "ChampsSource", "ValeurAttendue", "ValeurCible", "Regle", "DescriptionRegle",
           "Justification", "Chemin", "VerdictDeterministe", "MethodeIA", "Signature", "Recurrence", "Impact"]


@dataclass
class Result:
    df: pd.DataFrame
    models: Dict[str, ai.FieldModel] = field(default_factory=dict)
    learning_set: Optional[common.LearningSet] = None


def row_id(r) -> str:
    return "/".join(str(r.get(k) or "-") for k in ("Matricule", "TypeAffectation", "CodePoste", "ChampCible"))


# --------------------------------------------------------------------------- expert feedback
def load_overrides(path: Path = OVERRIDES) -> List[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def add_override(entry: dict, path: Path = OVERRIDES) -> dict:
    assert entry["verdict"] in VERDICTS, entry["verdict"]
    assert entry["portee"] in ("ligne", "motif"), entry["portee"]
    entry = {"date": dt.datetime.now().isoformat(timespec="seconds"), **entry}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def remove_override(index: int, path: Path = OVERRIDES) -> None:
    entries = load_overrides(path)
    del entries[index]
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")


def apply_overrides(df: pd.DataFrame, overrides: List[dict]) -> pd.DataFrame:
    """A 'ligne' override targets one row; a 'motif' override becomes a rule for every row of the
    same field sharing the same difference signature — the expert's decision generalises."""
    df = df.copy()
    for o in overrides:
        if o["portee"] == "ligne":
            hit = df["Id"] == o["id"]
        else:
            hit = (df["ChampCible"] == o["champ"]) & (df["Signature"] == o["signature"]) & (df["Signature"] != "")
        for r in df.index[hit]:
            if df.at[r, "Verdict"] == o["verdict"] and df.at[r, "Couche"] == "EXPERT":
                continue
            scope = "cette ligne" if o["portee"] == "ligne" else f"motif « {o['signature']} » du champ {o['champ']}"
            df.at[r, "Justification"] = (f"Expert ({o['date'][:10]}, {scope}) : {df.at[r, 'Verdict']} → {o['verdict']}"
                                         f"{' — ' + o['commentaire'] if o.get('commentaire') else ''}. "
                                         f"Analyse initiale : {df.at[r, 'Justification']}")
            df.at[r, "Chemin"] = f"{df.at[r, 'Chemin']} | [EXPERT] Verdict corrigé → {o['verdict']}"
            df.at[r, "Verdict"] = o["verdict"]
            df.at[r, "Couche"] = "EXPERT"
            df.at[r, "Confiance"] = 1.0
    return df


# --------------------------------------------------------------------------- run
def run(data_dir: Path, overrides: Optional[List[dict]] = None, use_ai: bool = True) -> Result:
    df = engine.run(data_dir)
    df["Id"] = [row_id(r) for r in df.to_dict("records")]
    models, ls = {}, None
    if use_ai and (df["Verdict"] == AMBIGU).any():
        ls = common.build(data_dir)
        df, models = ai.resolve(df, ls)
    else:
        df["VerdictDeterministe"], df["Confiance"], df["MethodeIA"], df["Signature"] = df["Verdict"], 1.0, "", ""
    missing = (df["Signature"] == "") & (df["Regle"] != "R-MATCH")
    df.loc[missing, "Signature"] = [signatures.signature(e, a) for e, a in
                                    zip(df.loc[missing, "ValeurAttendue"], df.loc[missing, "ValeurCible"])]
    df = apply_overrides(df, load_overrides() if overrides is None else overrides)
    df = ai.prioritise(df)
    order = {ANOMALIE: 0, AMBIGU: 1, ECART_JUSTIFIE: 2, CONFORME: 3}
    df = df.assign(_o=df["Verdict"].map(order)).sort_values(["_o", "Priorite", "Matricule"],
                                                             ascending=[True, False, True]).drop(columns="_o")
    return Result(df[COLUMNS].reset_index(drop=True), models, ls)


def learned_rules(res: Result) -> List[dict]:
    """One row per field the AI layer analysed — for the report and the UI."""
    out = []
    for f, m in res.models.items():
        top = m.signature_counts.most_common()
        out.append({
            "Champ": f, "Règle apprise (S1b)": m.rule if m.reliable else f"(rejetée) {m.rule}",
            "Couverture": round(m.coverage, 3), "Stabilité LOO": round(m.stability, 3), "Fiable": m.reliable,
            "Signatures d'écart": " ; ".join(f"{s} ×{c}" for s, c in top),
            "Lignes résolues par l'IA": int(((res.df["ChampCible"] == f) & (res.df["Couche"] == "IA")).sum()),
        })
    return out


def demo_cases(df: pd.DataFrame) -> Dict[str, dict]:
    """One representative row per case type required by the challenge demo."""
    picks = {
        "conforme": df[(df["Verdict"] == CONFORME) & (df["ChampCible"] == "contractTypeCode")],
        "ecart_justifie": df[(df["Verdict"] == ECART_JUSTIFIE) & (df["Couche"] == "DETERMINISTE")
                             & (df["ChampCible"] == "statusReasonCode")],
        "anomalie": df[(df["Verdict"] == ANOMALIE) & (df["ChampCible"] == "contractTypeCode")],
        "ia": df[(df["Couche"] == "IA") & (df["ChampCible"] == "weeklyHoursOverride")],
    }
    fallback = {"conforme": df["Verdict"] == CONFORME, "ecart_justifie": df["Verdict"] == ECART_JUSTIFIE,
                "anomalie": df["Verdict"] == ANOMALIE, "ia": df["Couche"] == "IA"}
    out = {}
    for k, sub in picks.items():
        sub = sub if len(sub) else df[fallback[k]]
        if len(sub):
            out[k] = sub.iloc[0].to_dict()
    return out
