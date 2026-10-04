"""Read-only loading of the five input files into tidy DataFrames."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set

import pandas as pd

from .normalize import to_str

SOURCE_FILE = "Employe_Source_Anonymise_VF.xlsx"
TARGET_FILE = "Employe_Destination_Anonymise_VF.xlsx"
POSTE_FILE = "détail_du_poste.xlsx"
MOTIF_FILE = "Motif de la situation d'emploi.xlsx"


def load_source(data_dir: Path) -> pd.DataFrame:
    return pd.read_excel(data_dir / SOURCE_FILE)


def load_target(data_dir: Path) -> pd.DataFrame:
    return pd.read_excel(data_dir / TARGET_FILE)


def load_poste_history(data_dir: Path) -> pd.DataFrame:
    """The file is CSV text stored in a single Excel column; dates are Excel serials."""
    raw = pd.read_excel(data_dir / POSTE_FILE, header=None, dtype=str)
    df = pd.read_csv(io.StringIO("\n".join(raw[0].dropna().tolist())))
    df["DateEffet"] = pd.to_datetime(df["DateEffetAffectation"], unit="D", origin="1899-12-30").dt.date
    return df.sort_values(["IdentifiantPoste", "DateEffet"]).reset_index(drop=True)


def load_motif(data_dir: Path) -> pd.DataFrame:
    return pd.read_excel(data_dir / MOTIF_FILE)


@dataclass
class Aux:
    """Lookups the rules need beyond the source/target row pair."""

    poste: pd.DataFrame
    motif: pd.DataFrame
    position_names_by_code: Dict[str, Set[str]]
    codes_by_position_name: Dict[str, Set[str]]

    def history(self, code_poste) -> pd.DataFrame:
        return self.poste[self.poste["IdentifiantPoste"] == code_poste]

    def motif_row(self, code_raison) -> Optional[dict]:
        hit = self.motif[self.motif["CodeCatégorieStatut"] == code_raison]
        return None if hit.empty else hit.iloc[0].to_dict()


def build_aux(poste: pd.DataFrame, motif: pd.DataFrame, target: pd.DataFrame) -> Aux:
    by_code: Dict[str, Set[str]] = {}
    by_name: Dict[str, Set[str]] = {}
    for code, name in zip(target["positionId"], target["positionName"]):
        c, n = to_str(code), to_str(name)
        by_code.setdefault(c, set()).add(n)
        by_name.setdefault(n, set()).add(c)
    return Aux(poste, motif, by_code, by_name)
