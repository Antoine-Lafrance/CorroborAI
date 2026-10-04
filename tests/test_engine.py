from pathlib import Path

import pandas as pd
import pytest

from corroborai import engine
from corroborai.loaders import Aux
from corroborai.rules import contract_type_tree, situation_tree
from corroborai.tree import AMBIGU, ANOMALIE, CONFORME, ECART_JUSTIFIE, Context, evaluate

DATA = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="module")
def result() -> pd.DataFrame:
    return engine.run(DATA)


def test_known_anomalies(result):
    got = set(map(tuple, result[result.Verdict == ANOMALIE][["Matricule", "ChampCible"]].values))
    assert got == {
        ("1545850", "(affectation)"),
        ("2762457", "contractTypeCode"), ("4625374", "contractTypeCode"),
        ("3712987", "contractTypeCode"), ("7254364", "contractTypeCode"),
        ("3241002", "siteName"), ("6035643", "siteName"),
        ("9989151", "assignmentStartDate"), ("3241002", "assignmentStartDate"), ("4402456", "assignmentStartDate"),
    }


def test_every_verdict_is_traceable(result):
    assert result["Regle"].notna().all()
    assert (result["Chemin"].str.len() > 0).all()
    assert (result["Justification"].str.len() > 0).all()


def test_mojibake_status_is_justified(result):
    rows = result[(result.ChampCible == "detailedStatus") & (result.Verdict == ECART_JUSTIFIE)]
    assert set(rows.Matricule) == {"7603160", "2911996"}


def _ctx(src, dst=None, motif=None):
    aux = Aux(poste=pd.DataFrame(), motif=motif if motif is not None else pd.DataFrame(
        columns=["CodeCatégorieStatut", "CodeStatutSystèmeExterne", "CodeGestionAccès"]), position_names_by_code={},
        codes_by_position_name={})
    return Context(src=src, dst=dst or {}, aux=aux)


@pytest.mark.parametrize("cat,perm,ft,code", [
    ("V", "Oui", "Oui", "JWN"), ("V", "Oui", "Non", "XFLR"), ("O", "Non", "Non", "WHX"), ("Q", "Non", "Oui", "TRSY"),
])
def test_contract_type_branches(cat, perm, ft, code):
    out = evaluate(contract_type_tree, _ctx(
        {"CatégorieEmploi": cat, "EstPermanent": perm, "EstTempsPlein": ft}, {"contractTypeCode": code}))
    assert out.verdict == CONFORME and out.vars["expected"] == code


def test_contract_type_uncovered_branch_is_ambiguous():
    out = evaluate(contract_type_tree, _ctx(
        {"CatégorieEmploi": "V", "EstPermanent": "Non", "EstTempsPlein": "Oui"}, {"contractTypeCode": "JWN"}))
    assert out.verdict == AMBIGU


def test_absence_reason_code_lookup():
    motif = pd.DataFrame({"CodeCatégorieStatut": [807], "CodeStatutSystèmeExterne": [170], "CodeGestionAccès": [2]})
    out = evaluate(situation_tree("statusReasonCode"), _ctx(
        {"CodeSuspensionAccès": 2, "CodeRaisonStatut": 807, "CodeStatutEmploi": 2},
        {"statusReasonCode": 170.0}, motif))
    assert out.verdict == ECART_JUSTIFIE
    assert "807" in out.explanation and "170" in out.explanation
