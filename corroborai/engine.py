"""Pipeline: load → pair assignments → evaluate every mapped field's tree → result rows."""
from __future__ import annotations

from itertools import permutations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import loaders
from .normalize import same, to_bool, to_date, to_str
from .rules import FIELD_RULES, RULE_CATALOG
from .tree import ANOMALIE, Context, evaluate

TYPE_FLAGS = {"P": (True, False), "A": (False, True), "S": (False, False)}


def _pair_score(s: dict, d: dict) -> int:
    score = 0
    if same(s["CodeEmploi"], d["positionId"]):
        score += 2
    if TYPE_FLAGS.get(s["TypeAffectation"]) == (to_bool(d["isPrimaryAssignment"]), to_bool(d["isTemporaryAssignment"])):
        score += 1
    if to_date(s["DateEntréePoste"]) == to_date(d["assignmentStartDate"]):
        score += 1
    return score


def pair_assignments(src: List[dict], dst: List[dict]) -> Tuple[List[Tuple[dict, dict]], List[dict], List[dict]]:
    """Best one-to-one pairing of one employee's assignments (n ≤ ~4, brute force is fine)."""
    small, large, flipped = (src, dst, False) if len(src) <= len(dst) else (dst, src, True)
    best, best_score = (), -1
    for perm in permutations(range(len(large)), len(small)):
        pairs = [(small[i], large[j]) for i, j in enumerate(perm)]
        score = sum(_pair_score(*(p[::-1] if flipped else p)) for p in pairs)
        if score > best_score:
            best, best_score = perm, score
    used = set(best)
    pairs = [(small[i], large[j]) for i, j in enumerate(best)]
    pairs = [(b, a) for a, b in pairs] if flipped else pairs
    leftovers = [large[j] for j in range(len(large)) if j not in used]
    return pairs, (leftovers if flipped else []), ([] if flipped else leftovers)


def _row(s: Optional[dict], d: Optional[dict], **kw) -> dict:
    base = s or {}
    return {
        "Matricule": to_str(base.get("Matricule")) or to_str((d or {}).get("personId")),
        "Nom": to_str(base.get("NomFamille")) or to_str((d or {}).get("surname")),
        "TypeAffectation": to_str(base.get("TypeAffectation")),
        "CodePoste": to_str(base.get("CodePoste")),
        "CodeEmploi": to_str(base.get("CodeEmploi")) or to_str((d or {}).get("positionId")),
        **kw,
    }


def match_all(source: pd.DataFrame, target: pd.DataFrame):
    """Yields (employee id, pairs, source-only rows, target-only rows) in a stable order."""
    src_by_id: Dict[str, List[dict]] = {}
    for r in source.to_dict("records"):
        src_by_id.setdefault(to_str(r["Matricule"]), []).append(r)
    dst_by_id: Dict[str, List[dict]] = {}
    for r in target.to_dict("records"):
        dst_by_id.setdefault(to_str(r["personId"]), []).append(r)
    for emp_id in sorted(set(src_by_id) | set(dst_by_id)):
        yield (emp_id, *pair_assignments(src_by_id.get(emp_id, []), dst_by_id.get(emp_id, [])))


def load_all(data_dir: Path):
    source = loaders.load_source(data_dir)
    target = loaders.load_target(data_dir)
    aux = loaders.build_aux(loaders.load_poste_history(data_dir), loaders.load_motif(data_dir), target)
    return source, target, aux


def run(data_dir: Path) -> pd.DataFrame:
    source, target, aux = load_all(data_dir)

    rows: List[dict] = []
    for emp_id, pairs, src_only, dst_only in match_all(source, target):
        for s in src_only:
            rows.append(_row(s, None, ChampCible="(affectation)", ChampsSource="TypeAffectation, CodePoste",
                             ValeurAttendue=f"type {s['TypeAffectation']} / poste {s['CodePoste']}",
                             ValeurCible=None, Verdict=ANOMALIE, Regle="R-MATCH", Couche="DETERMINISTE",
                             Justification=f"Affectation {s['TypeAffectation']} (poste {s['CodePoste']}, "
                                           f"entrée {to_date(s['DateEntréePoste'])}) absente de la cible.",
                             Chemin="[R-MATCH] Affectation source appariée à une ligne cible ? → non"))
        for d in dst_only:
            rows.append(_row(None, d, ChampCible="(affectation)", ChampsSource="-",
                             ValeurAttendue=None, ValeurCible=f"emploi {d['positionId']}",
                             Verdict=ANOMALIE, Regle="R-MATCH", Couche="DETERMINISTE",
                             Justification="Affectation présente dans la cible sans équivalent dans la source.",
                             Chemin="[R-MATCH] Affectation cible appariée à une ligne source ? → non"))
        for s, d in pairs:
            for fr in FIELD_RULES:
                out = evaluate(fr.tree, Context(src=s, dst=d, aux=aux))
                rows.append(_row(
                    s, d, ChampCible=fr.target, ChampsSource=fr.sources,
                    ValeurAttendue=to_str(out.vars.get("expected")), ValeurCible=to_str(out.vars.get("actual")),
                    Verdict=out.verdict, Regle=out.rule_id, Couche=out.layer,
                    Justification=out.explanation, Chemin=out.path_str,
                ))
    df = pd.DataFrame(rows)
    df["DescriptionRegle"] = df["Regle"].map(lambda r: RULE_CATALOG.get(r.split("+")[0].split(".")[0], ""))
    return df
