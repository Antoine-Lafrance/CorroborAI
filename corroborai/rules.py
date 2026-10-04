"""Business rules from Mapping.xlsx, encoded as decision trees (one per target field).

Verdict semantics
-----------------
* CONFORME        target == value expected by the mapping (direct copy or codification table).
* ECART_JUSTIFIE  target differs from the raw source value, but a complementary validation
                  (lookup/join, position history, accent/encoding/padding normalisation,
                  environment prefix) explains it.
* ANOMALIE        target contradicts the rule.
* AMBIGU          no deterministic rule settles it -> handed to the AI layer.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from .normalize import (
    clean, fix_mojibake, same, split_env_prefix, strip_accents, to_bool, to_date, to_num, to_str,
)
from .tree import AMBIGU, ANOMALIE, CONFORME, ECART_JUSTIFIE, Compute, Context, Leaf, Node

# Mapping says "date la plus ancienne" for assignmentStartDate, but applied literally it
# contradicts nearly every target row; the target consistently keeps the MOST RECENT of the
# two dates. Switch to "oldest" to apply the wording literally.
ASSIGNMENT_START_POLICY = "latest"
REFERENCE_DATE = dt.date.today()
EMAIL_DOMAIN = "@loto-quebec.com"

RULE_CATALOG = {
    "R-DIRECT": "Copie directe du champ source (mapping « N/A »).",
    "R-NOM": "Prénom/nom copiés ; enlever les accents (Mapping, ligne Email).",
    "R-EMAIL": "Première lettre du prénom + nom + 3 derniers chiffres du code + « @loto-quebec.com », sans accents.",
    "R-DIVNAME": "divisionName = Unité adm. + « - » + description de l'unité adm.",
    "R-POSNAME": "positionName = Emploi + « - » + description de l'emploi.",
    "R-SIT": "Situation d'emploi : code d'accès 00/01 → Actif ; 02/03/06/07 → Absence complète, "
             "code Remphor (table des motifs) et date de retour prévue.",
    "R-CT": "Type d'employé : V+permanent+temps plein → JWN ; V+permanent+temps partiel → XFLR ; "
            "T → KELH ; O → WHX ; M → CEGQ ; R → CNZC ; J → RMQ ; Z → JAW ; Q → TRSY.",
    "R-AFF": "Type d'affectation : P → primaire ; A → temporaire ; S → secondaire (deux indicateurs à faux).",
    "R-ASD": "Date d'effet du poste : combinaison de la date d'entrée et de la date de changement "
             f"d'unité administrative (détail du poste). Politique appliquée : {ASSIGNMENT_START_POLICY}.",
    "R-AED": "Date de fin : plus ancienne entre la date d'expiration et la fin de l'unité courante "
             "(prochain détail du poste − 1 jour si l'unité change), sinon NULL.",
    "R-HRS": "Heures semaine/jour copiées de la norme de l'employé (mapping « N/A »).",
    "R-ENC": "Normalisation : texte UTF-8 mal décodé en Latin-1 (ex. « complÃ¨te »).",
    "R-MATCH": "Appariement des affectations source ↔ cible (matricule, emploi, type, date).",
}


# --------------------------------------------------------------------------- helpers
def src(col: str) -> Callable[[Context], object]:
    return lambda c: clean(c.src.get(col))


def dst(col: str) -> Callable[[Context], object]:
    return lambda c: clean(c.dst.get(col))


def finish(rule_id: str, justified: str, on_mismatch, cmp=same):
    """Shared tail: compare `expected` vs `actual`, then `actual` vs raw source (`raw`)."""
    def raw_check(c: Context):
        if "raw" not in c.vars:
            return "sans objet"
        return cmp(c.vars["raw"], c.vars["actual"])

    return Node(
        f"{rule_id}.cmp", "Valeur cible == valeur attendue par la règle ?",
        lambda c: cmp(c.vars.get("expected"), c.vars.get("actual")),
        {
            True: Node(
                f"{rule_id}.raw", "Valeur cible == valeur brute de la source ?", raw_check,
                {
                    True: Leaf(CONFORME, rule_id, "Valeur cible {actual} conforme à la règle."),
                    "sans objet": Leaf(CONFORME, rule_id, "Valeur cible {actual} conforme à la règle."),
                    False: Leaf(ECART_JUSTIFIE, rule_id, justified),
                },
            ),
            False: on_mismatch,
        },
    )


def anomaly(rule_id: str, msg: str = "Attendu {expected}, trouvé {actual} dans la cible.") -> Leaf:
    return Leaf(ANOMALIE, rule_id, msg)


def date_eq(a, b) -> bool:
    return to_date(a) == to_date(b)


# --------------------------------------------------------------------------- direct copies
def direct_tree(src_col: str, dst_col: str):
    return Compute(
        "R-DIRECT.exp", f"Valeur source {src_col}", "expected", src(src_col),
        Compute("R-DIRECT.act", f"Valeur cible {dst_col}", "actual", dst(dst_col),
                Compute("R-DIRECT.raw0", "Valeur brute source", "raw", src(src_col),
                        finish("R-DIRECT", "", anomaly("R-DIRECT")))),
    )


def date_tree(src_col: str, dst_col: str):
    return Compute(
        "R-DIRECT.exp", f"Date source {src_col} (normalisée)", "expected", lambda c: to_date(c.src.get(src_col)),
        Compute("R-DIRECT.act", f"Date cible {dst_col} (normalisée, ISO → date)", "actual",
                lambda c: to_date(c.dst.get(dst_col)),
                finish("R-DIRECT", "", anomaly("R-DIRECT"), cmp=date_eq)),
    )


# --------------------------------------------------------------------------- hours
def hours_tree(src_col: str, dst_col: str, contract_col: str):
    def contract(c: Context):
        h = c.aux.history(c.src.get("CodePoste"))
        return None if h.empty else to_num(h.iloc[-1][contract_col])

    return Compute(
        "R-HRS.exp", f"Norme de l'employé {src_col}", "expected", lambda c: to_num(c.src.get(src_col)),
        Compute("R-HRS.act", f"Valeur cible {dst_col}", "actual", lambda c: to_num(c.dst.get(dst_col)),
                Compute("R-HRS.ctr", f"Heures contractuelles du poste ({contract_col}, détail du poste)",
                        "contrat", contract,
                        finish("R-HRS", "", Node(
                            "R-HRS.ctrchk", "Valeur cible == heures contractuelles du poste ?",
                            lambda c: same(c.vars["actual"], c.vars["contrat"]),
                            {
                                True: Leaf(AMBIGU, "R-HRS",
                                           "La cible ({actual}) reprend les heures contractuelles du poste "
                                           "({contrat}) au lieu de la norme de l'employé ({expected}). "
                                           "Aucune règle documentée ne tranche."),
                                False: anomaly("R-HRS"),
                            })))),
    )


# --------------------------------------------------------------------------- names
def name_tree(src_col: str, dst_col: str):
    return Compute(
        "R-NOM.exp", f"Valeur source {src_col}", "expected", lambda c: to_str(c.src.get(src_col)),
        Compute("R-NOM.act", f"Valeur cible {dst_col}", "actual", lambda c: to_str(c.dst.get(dst_col)),
                Node("R-NOM.exact", "Identiques ?", lambda c: c.vars["expected"] == c.vars["actual"], {
                    True: Leaf(CONFORME, "R-NOM", "Valeur identique ({actual})."),
                    False: Node("R-NOM.accent", "Identiques une fois les accents retirés ?",
                                lambda c: strip_accents(c.vars["expected"] or "") == (c.vars["actual"] or ""), {
                        True: Leaf(ECART_JUSTIFIE, "R-NOM",
                                   "Accents retirés comme l'exige la règle : {expected} → {actual}."),
                        False: Node("R-NOM.case", "Identiques en ignorant la casse ?",
                                    lambda c: strip_accents(c.vars["expected"] or "").casefold()
                                    == (c.vars["actual"] or "").casefold(), {
                            True: Leaf(AMBIGU, "R-NOM", "Différence de casse uniquement ({expected} / {actual}) ; "
                                                        "non couverte par la règle."),
                            False: anomaly("R-NOM"),
                        }),
                    }),
                })),
    )


# --------------------------------------------------------------------------- email
def _expected_email(c: Context) -> Optional[str]:
    first, last, mat = to_str(c.src.get("PrénomUsuel")), to_str(c.src.get("NomFamille")), to_str(c.src.get("Matricule"))
    if not (first and last and mat):
        return None
    return strip_accents(first[0] + last + mat[-3:]).replace(" ", "") + EMAIL_DOMAIN


def _email_structure_ok(c: Context) -> bool:
    """Initial + alphabetic stem of the surname + trailing 3 digits + domain."""
    local = (c.vars.get("actual_sans_prefixe") or "").lower()
    first, last = to_str(c.src.get("PrénomUsuel")) or "", to_str(c.src.get("NomFamille")) or ""
    stem = re.match(r"[^\d]*", strip_accents(last)).group(0).lower()
    return (
        local.endswith(EMAIL_DOMAIN)
        and local[:1] == strip_accents(first[:1]).lower()
        and local[1:].startswith(stem)
        and re.search(r"\d{3}@", local) is not None
    )


email_tree = Compute(
    "R-EMAIL.exp", "Courriel attendu (initiale + nom + 3 derniers chiffres du matricule)", "expected", _expected_email,
    Compute("R-EMAIL.act", "Courriel cible", "actual", dst("contactEmail"),
            Compute("R-EMAIL.pfx", "Préfixe d'environnement retiré", "prefixe",
                    lambda c: split_env_prefix(c.vars["actual"] or "")[0] or None,
                    Compute("R-EMAIL.loc", "Courriel cible sans préfixe", "actual_sans_prefixe",
                            lambda c: split_env_prefix(c.vars["actual"] or "")[1],
                            Node("R-EMAIL.eq", "Courriel (sans préfixe) == attendu, insensible à la casse ?",
                                 lambda c: (c.vars["expected"] or "").lower() == c.vars["actual_sans_prefixe"].lower(), {
                                     True: Node("R-EMAIL.env", "Préfixe d'environnement présent ?",
                                                lambda c: c.vars["prefixe"] is not None, {
                                         True: Leaf(ECART_JUSTIFIE, "R-EMAIL",
                                                    "Courriel conforme une fois le préfixe d'environnement "
                                                    "« {prefixe} » retiré."),
                                         False: Leaf(CONFORME, "R-EMAIL", "Courriel conforme ({actual})."),
                                     }),
                                     False: Node("R-EMAIL.struct",
                                                 "Structure de la règle respectée (initiale, nom, 3 chiffres, domaine) ?",
                                                 _email_structure_ok, {
                                         True: Leaf(AMBIGU, "R-EMAIL",
                                                    "Structure conforme mais identifiant différent "
                                                    "({actual_sans_prefixe} vs {expected}) : artefact "
                                                    "d'anonymisation probable."),
                                         False: anomaly("R-EMAIL"),
                                     }),
                                 })))),
)


# --------------------------------------------------------------------------- concatenated labels
def _split_label(v: Optional[str]):
    if not v or "-" not in v:
        return None, v
    code, label = v.split("-", 1)
    return code, label


def _self_consistent_label(c: Context) -> bool:
    """'3649-Empl3649': code prefix == digits of the label, and code→label is 1:1 in the target."""
    actual = c.vars["actual"] or ""
    code, label = _split_label(actual)
    digits = re.sub(r"\D", "", label or "")
    if not code or not digits or int(code) != int(digits):
        return False
    tgt_code = to_str(c.dst.get("positionId"))
    return (len(c.aux.position_names_by_code.get(tgt_code, ())) == 1
            and len(c.aux.codes_by_position_name.get(actual, ())) == 1)


def concat_tree(rule_id: str, code_col: str, label_col: str, dst_col: str, anonym_check: bool):
    def padded_equal(c: Context) -> bool:
        ec, el = _split_label(c.vars["expected"])
        ac, al = _split_label(c.vars["actual"])
        return bool(ec and ac and ec.isdigit() and ac.isdigit() and int(ec) == int(ac) and el == al)

    tail = (Node(f"{rule_id}.anon", "Libellé cible auto-cohérent et correspondance code → libellé 1:1 ?",
                 _self_consistent_label, {
                     True: Leaf(AMBIGU, rule_id, "Libellé cible {actual} cohérent en interne mais différent de "
                                                 "{expected} : recodage d'anonymisation probable."),
                     False: anomaly(rule_id),
                 }) if anonym_check else anomaly(rule_id))

    return Compute(
        f"{rule_id}.exp", f"Concaténation {code_col} + '-' + {label_col}", "expected",
        lambda c: f"{to_str(c.src.get(code_col))}-{to_str(c.src.get(label_col))}",
        Compute(f"{rule_id}.act", f"Valeur cible {dst_col}", "actual", dst(dst_col),
                Node(f"{rule_id}.exact", "Identiques ?", lambda c: same(c.vars["expected"], c.vars["actual"]), {
                    True: Leaf(CONFORME, rule_id, "Libellé conforme ({actual})."),
                    False: Node(f"{rule_id}.pad", "Identiques en normalisant le zéro-padding du code ?", padded_equal, {
                        True: Leaf(ECART_JUSTIFIE, rule_id,
                                   "Même code et même libellé ; la cible complète le code par des zéros "
                                   "({expected} → {actual})."),
                        False: tail,
                    }),
                })),
    )


# --------------------------------------------------------------------------- employment situation
def _situation(c: Context) -> str:
    code = to_num(c.src.get("CodeSuspensionAccès"))
    if code in (0, 1):
        return "Actif"
    if code in (2, 3, 6, 7):
        return "Absence complète"
    return f"code {to_str(code)} non défini"


def _motif_ok(c: Context):
    row = c.aux.motif_row(c.src.get("CodeRaisonStatut"))
    if row is None:
        return "motif introuvable"
    return same(row["CodeGestionAccès"], c.src.get("CodeSuspensionAccès"))


def situation_tree(field: str):
    """field in {'statusReasonCode', 'expectedReturnDate', 'detailedStatus'}."""
    cmp = date_eq if field == "expectedReturnDate" else same
    if field == "detailedStatus":
        on_mismatch = Node("R-ENC.chk", "Identiques après réparation d'encodage (UTF-8 lu en Latin-1) ?",
                           lambda c: fix_mojibake(c.vars["actual"]) == c.vars["expected"], {
                               True: Leaf(ECART_JUSTIFIE, "R-SIT+R-ENC",
                                          "Valeur correcte ({expected}) mais mal encodée dans la cible ({actual})."),
                               False: anomaly("R-SIT"),
                           })
    else:
        on_mismatch = anomaly("R-SIT")

    actual = Compute("R-SIT.act", f"Valeur cible {field}", "actual",
                     (lambda c: to_date(c.dst.get(field))) if field == "expectedReturnDate" else dst(field),
                     finish("R-SIT", "Valeur dérivée par la table des motifs : code raison {code_raison} → "
                                     "code Remphor {expected} (source brute : {raw}).", on_mismatch, cmp=cmp))

    if field == "statusReasonCode":
        absent_expected = Compute("R-SIT.remphor", "Code Remphor via table des motifs", "expected",
                                  lambda c: c.aux.motif_row(c.src.get("CodeRaisonStatut"))["CodeStatutSystèmeExterne"],
                                  Compute("R-SIT.raw", "Valeur brute source CodeStatutEmploi", "raw",
                                          src("CodeStatutEmploi"), actual))
    elif field == "expectedReturnDate":
        absent_expected = Compute("R-SIT.cadp", "Date de retour prévue (DateRetourAnticipée)", "expected",
                                  lambda c: to_date(c.src.get("DateRetourAnticipée")), actual)
    else:
        absent_expected = Compute("R-SIT.lbl", "Libellé de situation", "expected",
                                  lambda c: "Absence complète", actual)

    absent = Compute(
        "R-SIT.raison", "Code raison source (CodeRaisonStatut)", "code_raison", src("CodeRaisonStatut"),
        Node("R-SIT.motif", "Code raison trouvé dans la table des motifs et code d'accès cohérent ?", _motif_ok, {
            True: absent_expected,
            False: Leaf(AMBIGU, "R-SIT", "Le code d'accès du motif {code_raison} ne correspond pas au code "
                                         "d'accès source : situation incohérente entre les tables."),
            "motif introuvable": Leaf(AMBIGU, "R-SIT", "Code raison {code_raison} absent de la table des motifs."),
        }),
    )
    active_value = {"statusReasonCode": None, "expectedReturnDate": None, "detailedStatus": "Actif"}[field]
    active = Compute("R-SIT.actif", "Valeur attendue pour un employé actif", "expected",
                     lambda c: active_value, actual)

    return Node("R-SIT.acces", "Situation selon le code de traitement des accès (CodeSuspensionAccès) ?",
                _situation, {"Actif": active, "Absence complète": absent})


# --------------------------------------------------------------------------- contract type
CT_SIMPLE = {"T": "KELH", "O": "WHX", "M": "CEGQ", "R": "CNZC", "J": "RMQ", "Z": "JAW", "Q": "TRSY"}
CT_PROFILE = {"JWN": "V + permanent + temps plein", "XFLR": "V + permanent + temps partiel",
              **{v: f"catégorie {k}" for k, v in CT_SIMPLE.items()}}

_ct_finish = finish(
    "R-CT", "",
    Compute("R-CT.rev", "Profil source qui produirait la valeur cible", "profil_cible",
            lambda c: CT_PROFILE.get(c.vars["actual"], "aucun profil connu"),
            anomaly("R-CT", "Attendu {expected} ({profil_source}), trouvé {actual} qui correspond au profil "
                            "« {profil_cible} »."))
)


def _ct_leaf(code: str, profile: str):
    return Compute("R-CT.exp", "Code attendu", "expected", lambda c: code,
                   Compute("R-CT.prof", "Profil source", "profil_source", lambda c: profile,
                           Compute("R-CT.act", "Valeur cible contractTypeCode", "actual", dst("contractTypeCode"),
                                   _ct_finish)))


contract_type_tree = Node(
    "R-CT.cat", "CatégorieEmploi ?", lambda c: to_str(c.src.get("CatégorieEmploi")),
    {
        "V": Node("R-CT.perm", "EstPermanent ?", lambda c: to_bool(c.src.get("EstPermanent")), {
            True: Node("R-CT.ft", "EstTempsPlein ?", lambda c: to_bool(c.src.get("EstTempsPlein")), {
                True: _ct_leaf("JWN", CT_PROFILE["JWN"]),
                False: _ct_leaf("XFLR", CT_PROFILE["XFLR"]),
            }),
        }),
        **{k: _ct_leaf(v, CT_PROFILE[v]) for k, v in CT_SIMPLE.items()},
    },
)


# --------------------------------------------------------------------------- assignment type
def assignment_flag_tree(dst_col: str):
    want = {"isPrimaryAssignment": {"P": True, "A": False, "S": False},
            "isTemporaryAssignment": {"P": False, "A": True, "S": False}}[dst_col]

    def leaf(t):
        return Compute("R-AFF.exp", f"Indicateur attendu pour le type {t}", "expected", lambda c: want[t],
                       Compute("R-AFF.act", f"Valeur cible {dst_col}", "actual", lambda c: to_bool(c.dst.get(dst_col)),
                               finish("R-AFF", "", anomaly("R-AFF"))))

    return Node("R-AFF.type", "TypeAffectation ?", lambda c: to_str(c.src.get("TypeAffectation")),
                {t: leaf(t) for t in "PAS"})


# --------------------------------------------------------------------------- assignment dates
def _current_index(h) -> int:
    past = [i for i, d in enumerate(h["DateEffet"]) if d <= REFERENCE_DATE]
    return past[-1] if past else 0


def _unit_change_date(c: Context):
    """Date at which the current admin unit became applicable; MIN EFFDT if it never changed."""
    h = c.aux.history(c.src.get("CodePoste")).reset_index(drop=True)
    cur = _current_index(h)
    units = h["CodeDirectionAffectée"].tolist()
    for i in range(cur, 0, -1):
        if units[i] != units[i - 1]:
            return h.loc[i, "DateEffet"]
    return h.loc[0, "DateEffet"]


def _unit_end_date(c: Context):
    h = c.aux.history(c.src.get("CodePoste")).reset_index(drop=True)
    cur = _current_index(h)
    if cur + 1 >= len(h) or h.loc[cur + 1, "CodeDirectionAffectée"] == h.loc[cur, "CodeDirectionAffectée"]:
        return None
    return h.loc[cur + 1, "DateEffet"] - dt.timedelta(days=1)


def _hist_unit(c: Context):
    h = c.aux.history(c.src.get("CodePoste")).reset_index(drop=True)
    return h.loc[_current_index(h), "CodeDirectionAffectée"]


def _with_history(rule_id: str, then):
    return Compute(
        f"{rule_id}.hist", "Nombre d'enregistrements dans le détail du poste (CodePoste)", "nb_details",
        lambda c: len(c.aux.history(c.src.get("CodePoste"))),
        Node(f"{rule_id}.hasHist", "Historique du poste disponible ?", lambda c: c.vars["nb_details"] > 0, {
            False: Leaf(AMBIGU, rule_id, "Aucun détail du poste pour ce CodePoste : la règle ne peut pas être appliquée."),
            True: Compute(f"{rule_id}.unit", "Unité administrative courante selon l'historique", "unite_hist",
                          _hist_unit,
                          Node(f"{rule_id}.unitchk", "Unité de l'historique == CodeDirection source ?",
                               lambda c: same(c.vars["unite_hist"], c.src.get("CodeDirection")), {
                                   True: then,
                                   False: Leaf(AMBIGU, rule_id, "L'unité courante de l'historique ({unite_hist}) "
                                                                "diffère de l'unité source : historique incohérent."),
                               })),
        }),
    )


def _start_expected(c: Context):
    a, b = to_date(c.vars["date_entree"]), to_date(c.vars["date_unite"])
    return max(a, b) if ASSIGNMENT_START_POLICY == "latest" else min(a, b)


_asd_mismatch = Node(
    "R-ASD.diag", "Valeur cible == dernière date d'effet du détail du poste ?",
    lambda c: date_eq(c.vars["actual"], c.vars["derniere_date_effet"]),
    {
        True: anomaly("R-ASD", "Attendu {expected}, trouvé {actual} : la cible a pris la dernière date d'effet "
                               "du détail du poste alors que l'unité administrative n'a pas changé "
                               "(date d'unité {date_unite})."),
        False: anomaly("R-ASD"),
    },
)
_asd_finish = finish("R-ASD", "Date d'unité administrative ({date_unite}) retenue au lieu de la date "
                              "d'entrée ({date_entree}).", _asd_mismatch, cmp=date_eq)
_asd = Compute("R-ASD.act", "Valeur cible assignmentStartDate", "actual",
               lambda c: to_date(c.dst.get("assignmentStartDate")), _asd_finish)
_asd = Compute("R-ASD.exp", f"Date attendue (politique « {ASSIGNMENT_START_POLICY} »)", "expected",
               _start_expected, _asd)
_asd = Compute("R-ASD.last", "Dernière date d'effet du détail du poste", "derniere_date_effet",
               lambda c: c.aux.history(c.src.get("CodePoste"))["DateEffet"].max(), _asd)
_asd = Compute("R-ASD.raw", "Valeur brute source", "raw", lambda c: c.vars["date_entree"], _asd)
_asd = Compute("R-ASD.de", "Date d'entrée dans le poste (DateEntréePoste)", "date_entree",
               lambda c: to_date(c.src.get("DateEntréePoste")), _asd)
_asd = Compute("R-ASD.cu", "Date de changement d'unité administrative (ou MIN EFFDT)", "date_unite",
               _unit_change_date, _asd)
assignment_start_tree = _with_history("R-ASD", _asd)


def assignment_end_tree(dst_col: str):
    def expected(c: Context):
        dates = [d for d in (to_date(c.vars["raw"]), c.vars["fin_unite"]) if d is not None]
        return min(dates) if dates else None

    return _with_history("R-AED", Compute(
        "R-AED.raw", "Date d'expiration du poste (DateSortiePoste)", "raw", lambda c: to_date(c.src.get("DateSortiePoste")),
        Compute("R-AED.fu", "Fin de l'unité courante (prochain détail − 1 j si l'unité change)", "fin_unite", _unit_end_date,
                Compute("R-AED.exp", "Date attendue (plus ancienne des deux)", "expected", expected,
                        Compute("R-AED.act", f"Valeur cible {dst_col}", "actual", lambda c: to_date(c.dst.get(dst_col)),
                                finish("R-AED", "Fin d'unité administrative ({fin_unite}) retenue.",
                                       anomaly("R-AED"), cmp=date_eq)))),
    ))


# --------------------------------------------------------------------------- field catalogue
@dataclass
class FieldRule:
    target: str
    sources: str
    rule_id: str
    tree: object


FIELD_RULES: List[FieldRule] = [
    FieldRule("givenName", "PrénomUsuel", "R-NOM", name_tree("PrénomUsuel", "givenName")),
    FieldRule("surname", "NomFamille", "R-NOM", name_tree("NomFamille", "surname")),
    FieldRule("contactEmail", "PrénomUsuel, NomFamille, Matricule", "R-EMAIL", email_tree),
    FieldRule("onboardDate", "DateEmbaucheRécente", "R-DIRECT", date_tree("DateEmbaucheRécente", "onboardDate")),
    FieldRule("siteName", "LibelléSite", "R-DIRECT", direct_tree("LibelléSite", "siteName")),
    FieldRule("siteCode", "CodeSite", "R-DIRECT", direct_tree("CodeSite", "siteCode")),
    FieldRule("divisionId", "CodeDirection", "R-DIRECT", direct_tree("CodeDirection", "divisionId")),
    FieldRule("divisionName", "CodeDirection, LibelléDirection", "R-DIVNAME",
              concat_tree("R-DIVNAME", "CodeDirection", "LibelléDirection", "divisionName", anonym_check=False)),
    FieldRule("divisionCode", "CodeImputation", "R-DIRECT", direct_tree("CodeImputation", "divisionCode")),
    FieldRule("positionId", "CodeEmploi", "R-DIRECT", direct_tree("CodeEmploi", "positionId")),
    FieldRule("positionName", "CodeEmploi, IntituléEmploi", "R-POSNAME",
              concat_tree("R-POSNAME", "CodeEmploi", "IntituléEmploi", "positionName", anonym_check=True)),
    FieldRule("positionCode", "CodeEmploi", "R-DIRECT", direct_tree("CodeEmploi", "positionCode")),
    FieldRule("statusReasonCode", "CodeStatutEmploi, CodeRaisonStatut, CodeSuspensionAccès", "R-SIT",
              situation_tree("statusReasonCode")),
    FieldRule("expectedReturnDate", "DateRetourAnticipée, CodeSuspensionAccès", "R-SIT",
              situation_tree("expectedReturnDate")),
    FieldRule("detailedStatus", "CodeSuspensionAccès", "R-SIT", situation_tree("detailedStatus")),
    FieldRule("contractTypeCode", "CatégorieEmploi, EstPermanent, EstTempsPlein", "R-CT", contract_type_tree),
    FieldRule("isPrimaryAssignment", "TypeAffectation", "R-AFF", assignment_flag_tree("isPrimaryAssignment")),
    FieldRule("isTemporaryAssignment", "TypeAffectation", "R-AFF", assignment_flag_tree("isTemporaryAssignment")),
    FieldRule("assignmentStartDate", "DateEntréePoste + détail du poste", "R-ASD", assignment_start_tree),
    FieldRule("payGradeId", "ÉchelleSalariale", "R-DIRECT", direct_tree("ÉchelleSalariale", "payGradeId")),
    FieldRule("weeklyHoursOverride", "HeuresNormeHebdo", "R-HRS",
              hours_tree("HeuresNormeHebdo", "weeklyHoursOverride", "HeuresSemaineContrat")),
    FieldRule("dailyHoursOverride", "HeuresNormeQuotidienne", "R-HRS",
              hours_tree("HeuresNormeQuotidienne", "dailyHoursOverride", "HeuresJourContrat")),
    FieldRule("assignmentEndDate", "DateSortiePoste + détail du poste", "R-AED", assignment_end_tree("assignmentEndDate")),
    FieldRule("termEndDate", "DateSortiePoste + détail du poste", "R-AED", assignment_end_tree("termEndDate")),
]
