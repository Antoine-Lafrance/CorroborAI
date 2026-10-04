"""Exports the corroboration result to CSV and a formatted multi-sheet Excel report.

Sheet order follows the investigator's workflow: what to look at first (À investiguer, by
priority), then why the rest is acceptable (Écarts justifiés, Conformes), then the evidence
(rules applied, rules learned by the AI layer, expert decisions).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from .rules import RULE_CATALOG
from .tree import AMBIGU, ANOMALIE, CONFORME, ECART_JUSTIFIE

LABELS = {ANOMALIE: "Anomalie", AMBIGU: "Ambigu", ECART_JUSTIFIE: "Écart justifié", CONFORME: "Conforme"}
FILLS = {ANOMALIE: "F8D7D7", AMBIGU: "FDEFC8", ECART_JUSTIFIE: "D6E6F8", CONFORME: "D7EED7"}
INVESTIGATE_COLS = ["Priorite", "Verdict", "Couche", "Confiance", "Matricule", "Nom", "TypeAffectation", "CodePoste",
                    "ChampCible", "Impact", "ValeurAttendue", "ValeurCible", "Regle", "Justification", "Recurrence",
                    "Chemin"]
SHEETS = [(ECART_JUSTIFIE, "Écarts justifiés"), (CONFORME, "Conformes")]


def summary(df: pd.DataFrame) -> pd.DataFrame:
    order = [ANOMALIE, AMBIGU, ECART_JUSTIFIE, CONFORME]
    s = pd.crosstab(df["ChampCible"], df["Verdict"]).reindex(columns=order, fill_value=0)
    s.loc["TOTAL"] = s.sum()
    return s.rename(columns=LABELS)


def _layers(df: pd.DataFrame) -> pd.DataFrame:
    t = pd.crosstab(df["Couche"], df["Verdict"]).reindex(columns=[ANOMALIE, AMBIGU, ECART_JUSTIFIE, CONFORME],
                                                          fill_value=0)
    t["Total"] = t.sum(axis=1)
    return t.rename(columns=LABELS)


def _style(ws, verdict_col: Optional[int]) -> None:
    head = PatternFill("solid", fgColor="1F2A37")
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), head
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for col in ws.columns:
        width = max(len(str(c.value or "")) for c in col[:200])
        ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 70)
    if verdict_col is not None:
        for row in ws.iter_rows(min_row=2):
            v = row[verdict_col].value
            key = next((k for k, lbl in LABELS.items() if lbl == v or k == v), None)
            if key:
                row[verdict_col].fill = PatternFill("solid", fgColor=FILLS[key])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def write(df: pd.DataFrame, out_dir: Path, learned: Optional[List[Dict]] = None,
          overrides: Optional[List[Dict]] = None, name: str = "rapport_corroboration.xlsx") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "corroboration.csv", index=False, encoding="utf-8-sig")
    path = out_dir / name
    shown = df.assign(Verdict=df["Verdict"].map(LABELS).fillna(df["Verdict"]))
    todo = shown[df["Verdict"].isin([ANOMALIE, AMBIGU])].sort_values("Priorite", ascending=False)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        counts = df["Verdict"].value_counts()
        head = pd.DataFrame([
            ("Vérifications", len(df)),
            ("À investiguer (anomalies + ambigus)", int(counts.get(ANOMALIE, 0) + counts.get(AMBIGU, 0))),
            ("Écarts justifiés automatiquement", int(counts.get(ECART_JUSTIFIE, 0))),
            ("Conformes", int(counts.get(CONFORME, 0))),
            ("Résolus par la couche IA", int((df["Couche"] == "IA").sum())),
            ("Corrigés par un expert", int((df["Couche"] == "EXPERT").sum())),
        ], columns=["Indicateur", "Valeur"])
        head.to_excel(xw, sheet_name="Résumé", index=False)
        _layers(df).to_excel(xw, sheet_name="Résumé", startrow=len(head) + 2)
        summary(df).to_excel(xw, sheet_name="Résumé", startrow=len(head) + 9)
        todo[INVESTIGATE_COLS].to_excel(xw, sheet_name="À investiguer", index=False)
        for verdict, sheet in SHEETS:
            shown[df["Verdict"] == verdict].to_excel(xw, sheet_name=sheet, index=False)
        shown.to_excel(xw, sheet_name="Détail complet", index=False)
        if learned:
            pd.DataFrame(learned).to_excel(xw, sheet_name="Règles apprises (IA)", index=False)
        pd.DataFrame(sorted(RULE_CATALOG.items()), columns=["Règle", "Description"]).to_excel(
            xw, sheet_name="Catalogue des règles", index=False)
        if overrides:
            pd.DataFrame(overrides).to_excel(xw, sheet_name="Décisions expertes", index=False)
        for ws in xw.book.worksheets:
            header = [c.value for c in ws[1]]
            _style(ws, header.index("Verdict") if "Verdict" in header else None)
    return path
