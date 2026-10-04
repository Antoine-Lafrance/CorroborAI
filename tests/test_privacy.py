from pathlib import Path

import pytest

from corroborai.learn import common, dsl, llm, privacy

DATA = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="module")
def ls():
    return common.build(DATA)


def _secrets(ls):
    """Every real value that must never appear in a payload."""
    out = set()
    for s, d in ls.pairs:
        out |= {str(s["Matricule"]), s["NomFamille"], s["PrénomUsuel"], str(s["IdentifiantResponsable"]),
                s["NomResponsable"], s["LibelléRaisonStatut"]}
    return {v for v in out if v}


@pytest.mark.parametrize("field", sorted(llm.LLM_FIELDS))
def test_payload_leaks_nothing(ls, field):
    text = llm.payload(field, ls.X, ls.y(field))
    leaked = [v for v in _secrets(ls) if v in text]
    assert not leaked, leaked
    for col in ("IdentifiantResponsable", "NomResponsable", "RaisonStatut", "DateRetourAnticipée",
                "CodeSuspensionAccès", "CodeStatutEmploi", "motif.", "CodeQuart", "DateEffetRaison"):
        assert col not in text


def test_no_row_keeps_its_real_dates(ls):
    Xp, _ = privacy.Pseudonymizer().rows(ls.X, [None] * len(ls.X))
    for x, xp in zip(ls.X, Xp):
        for c, v in xp.items():
            if hasattr(v, "isoformat"):
                assert v != x[c], c


def test_documented_fields_never_sent(ls):
    with pytest.raises(ValueError):
        llm.payload("contractTypeCode", ls.X, ls.y("contractTypeCode"))
    assert llm.learn("contractTypeCode", ls.X, ls.y("contractTypeCode")).abstained


def test_rules_still_learnable_after_pseudonymisation(ls):
    """Pseudonymised data keeps the structure the rules depend on."""
    y = ls.y("assignmentStartDate")
    Xp, yp = privacy.Pseudonymizer(seed=3).rows(ls.X, y)
    rule = dsl.DateAgg("max", dsl.Col("src.DateEntréePoste"), dsl.Col("poste.max_eff"))
    assert dsl._matches(rule, Xp, yp).mean() == dsl._matches(rule, ls.X, y).mean() == 1.0
    yh = ls.y("weeklyHoursOverride")
    Xp, yp = privacy.Pseudonymizer(seed=3).rows(ls.X, yh)
    assert dsl._matches(dsl.Col("poste.HeuresSemaineContrat"), Xp, yp).all()


def test_identifiers_consistent_within_run(ls):
    p = privacy.Pseudonymizer(seed=5)
    Xp, _ = p.rows(ls.X, [None] * len(ls.X))
    by_real = {}
    for x, xp in zip(ls.X, Xp):
        by_real.setdefault(common.key(x["src.Matricule"]), set()).add(xp["src.Matricule"])
    assert all(len(v) == 1 for v in by_real.values())
    assert len({next(iter(v)) for v in by_real.values()}) == len(by_real)
