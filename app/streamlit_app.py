"""CorroborIA — interface : corroboration, règles apprises et preuves, comparaison des méthodes.

Lancer : .venv/bin/streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corroborai import engine, loaders, report  # noqa: E402
from corroborai.learn import common, dsl, evaluate, proof, signatures, tree_induction  # noqa: E402
from corroborai.rules import RULE_CATALOG  # noqa: E402

DEFAULT_DATA = ROOT / "data" / "raw"
LEARN_OUT = ROOT / "out" / "learning"
FEEDBACK = ROOT / "out" / "feedback.jsonl"
REQUIRED = [loaders.SOURCE_FILE, loaders.TARGET_FILE, loaders.POSTE_FILE, loaders.MOTIF_FILE]

# Reference palette (dataviz skill): status colours for verdicts, one blue hue for single-series bars.
VERDICT_COLORS = {"CONFORME": "#0ca30c", "ECART_JUSTIFIE": "#2a78d6", "AMBIGU": "#fab219", "ANOMALIE": "#d03b3b"}
VERDICT_LABELS = {"CONFORME": "✅ Conforme", "ECART_JUSTIFIE": "🔷 Écart justifié",
                  "AMBIGU": "🟡 Ambigu", "ANOMALIE": "🔴 Anomalie"}
BLUE, GRAY = "#2a78d6", "#b4b2a9"
METRIC_COLORS = {"précision": "#2a78d6", "rappel": "#eb6834", "F1": "#1baf7a"}  # categorical slots 1-3
SHORT = {"S1 DSL": "S1 recherche", "S1b DSL + conditions": "S1b recherche + conditions",
         "S2 Arbre de décision (LOO)": "S2 arbre", "S4 Signatures d'écart": "S4 signatures",
         "H Hybride (S1b, sinon S4)": "Hybride S1b→S4"}

st.set_page_config(page_title="CorroborIA", page_icon="🔎", layout="wide")


# --------------------------------------------------------------------------- data
@st.cache_data(show_spinner="Corroboration en cours…")
def run_engine(data_dir: str) -> pd.DataFrame:
    return engine.run(Path(data_dir))


@st.cache_resource(show_spinner="Préparation du jeu d'apprentissage…")
def learning_set(data_dir: str) -> common.LearningSet:
    return common.build(Path(data_dir))


@st.cache_data(show_spinner="Apprentissage des règles pour ce champ…")
def field_results(data_dir: str, field: str):
    ls = learning_set(data_dir)
    _, expected = evaluate.oracle(ls, field, [d for _, d in ls.pairs])
    y = ls.y(field)
    s1 = dsl.learn(field, ls.X, y)
    s1b = dsl.learn(field, ls.X, y, conditional=True)
    s2 = tree_induction.learn(field, ls.X, y)
    s4 = signatures.judge(field, expected, y)
    return {"S1": s1, "S1b": s1b, "S2": s2, "S4": s4}


@st.cache_data(show_spinner="Test de stabilité (leave-one-out)…")
def stability(data_dir: str, field: str) -> pd.DataFrame:
    return proof.stability(learning_set(data_dir), field)


@st.cache_data(show_spinner="Expérience A : règles documentées…")
def experiment_a(data_dir: str) -> pd.DataFrame:
    return evaluate.experiment_a(learning_set(data_dir), with_llm=False)


@st.cache_data(show_spinner="Expérience B : injection d'erreurs (≈ 40 s)…")
def experiment_b(data_dir: str, seeds: int) -> pd.DataFrame:
    cached = LEARN_OUT / "B_erreurs_synthetiques.csv"
    if Path(data_dir) == DEFAULT_DATA and cached.exists():
        df = pd.read_csv(cached)
        if df["graine"].nunique() == seeds:
            return df
    return evaluate.experiment_b(learning_set(data_dir), seeds=seeds)


def data_source() -> str:
    st.sidebar.header("Données")
    files = st.sidebar.file_uploader("Charger d'autres extractions (4 fichiers .xlsx)", type="xlsx",
                                     accept_multiple_files=True, help="\n".join(REQUIRED))
    if files:
        names = {f.name for f in files}
        missing = [r for r in REQUIRED if r not in names]
        if missing:
            st.sidebar.error("Fichiers manquants : " + ", ".join(missing))
        else:
            d = Path(tempfile.mkdtemp(prefix="corroborai_"))
            for f in files:
                (d / f.name).write_bytes(f.getvalue())
            st.sidebar.success("Extractions chargées.")
            return str(d)
    st.sidebar.caption(f"Jeu par défaut : `{DEFAULT_DATA.relative_to(ROOT)}` (lecture seule)")
    return str(DEFAULT_DATA)


def verdict_badge(v: str) -> str:
    return VERDICT_LABELS.get(v, v)


# --------------------------------------------------------------------------- tab 1
def tab_corroboration(data_dir: str):
    df = run_engine(data_dir)
    counts = df["Verdict"].value_counts()
    cols = st.columns(4)
    for c, v in zip(cols, ["CONFORME", "ECART_JUSTIFIE", "AMBIGU", "ANOMALIE"]):
        c.metric(verdict_badge(v), int(counts.get(v, 0)))

    st.subheader("Verdicts par champ")
    order = ["ANOMALIE", "AMBIGU", "ECART_JUSTIFIE", "CONFORME"]
    agg = df.groupby(["ChampCible", "Verdict"]).size().reset_index(name="n")
    agg["Verdict libellé"] = agg["Verdict"].map(VERDICT_LABELS)
    chart = alt.Chart(agg).mark_bar(stroke="white", strokeWidth=1).encode(
        y=alt.Y("ChampCible:N", sort="-x", title=None),
        x=alt.X("sum(n):Q", title="Nombre de vérifications"),
        color=alt.Color("Verdict libellé:N", title="Verdict",
                        scale=alt.Scale(domain=[VERDICT_LABELS[v] for v in order],
                                        range=[VERDICT_COLORS[v] for v in order])),
        order=alt.Order("Verdict:N", sort="ascending"),
        tooltip=["ChampCible", "Verdict libellé", "n"],
    ).properties(height=520)
    st.altair_chart(chart, use_container_width=True)

    st.subheader("Explorer les verdicts")
    f1, f2, f3 = st.columns([2, 2, 1])
    verdicts = f1.multiselect("Verdict", order, default=["ANOMALIE", "AMBIGU"], format_func=verdict_badge)
    fields = f2.multiselect("Champ", sorted(df["ChampCible"].unique()))
    mat = f3.text_input("Matricule")
    view = df[df["Verdict"].isin(verdicts or order)]
    if fields:
        view = view[view["ChampCible"].isin(fields)]
    if mat:
        view = view[view["Matricule"].astype(str).str.contains(mat.strip())]
    shown = view[["Matricule", "TypeAffectation", "ChampCible", "ValeurAttendue", "ValeurCible", "Verdict", "Regle",
                  "Justification"]].reset_index(drop=True)
    shown["Verdict"] = shown["Verdict"].map(verdict_badge)
    event = st.dataframe(shown, use_container_width=True, hide_index=True, on_select="rerun",
                         selection_mode="single-row", key="verdicts")
    sel = event.selection.rows if event and event.selection else []
    if sel:
        row = view.iloc[sel[0]]
        st.markdown(f"#### {row['Matricule']} · {row['ChampCible']} → {verdict_badge(row['Verdict'])}")
        st.markdown(f"**Règle {row['Regle']}** — {row['DescriptionRegle']}")
        st.info(row["Justification"])
        st.markdown("**Chemin de décision**")
        for i, step in enumerate(str(row["Chemin"]).split(" | "), 1):
            st.markdown(f"{i}. `{step}`")
    else:
        st.caption("Sélectionnez une ligne pour voir le chemin de décision complet.")

    with tempfile.TemporaryDirectory() as tmp:
        path = report.write(df, Path(tmp))
        st.download_button("Télécharger le rapport Excel", path.read_bytes(), file_name="rapport_corroboration.xlsx")


# --------------------------------------------------------------------------- tab 2
def _status_line(field: str, res, ev: pd.DataFrame):
    documented = field not in common.AMBIGUOUS_FIELDS
    if res.abstained:
        st.warning("**Aucune règle fiable** (couverture < 80 %). Le champ est jugé par les signatures d'écart (S4) "
                   "plutôt que par une règle apprise.")
        return
    if documented:
        mism = ev[ev["verdict moteur"] != "ECART_JUSTIFIE"]
        agree = (mism["règle apprise →"] == mism["règle documentée →"]).mean() if len(mism) else 1.0
        if agree == 1:
            st.success("**La règle apprise retrouve la règle documentée** sur toutes les lignes, sans l'avoir vue.")
        else:
            n = int((ev["règle apprise →"] != ev["règle documentée →"]).sum())
            st.warning(f"**La règle apprise diffère de la règle documentée sur {n} ligne(s).** La cible applique de "
                       "façon cohérente une autre règle que celle du mapping : à confirmer avec l'équipe métier.")
    else:
        st.info("**Règle proposée** pour un champ qu'aucune règle documentée ne tranche : à valider par un expert.")


def tab_rules(data_dir: str):
    ls = learning_set(data_dir)
    fields = common.AMBIGUOUS_FIELDS + common.KNOWN_FIELDS
    field = st.selectbox("Champ cible", fields, index=fields.index("weeklyHoursOverride"),
                         format_func=lambda f: f"{f} — {'ambigu' if f in common.AMBIGUOUS_FIELDS else 'règle documentée'}")
    res = field_results(data_dir, field)
    s1b = res["S1b"]
    rid = evaluate.RULES[field].rule_id

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown(f"**Description (Mapping.xlsx)** : {common.FIELD_DESCRIPTIONS.get(field, '–')}")
        st.markdown(f"**Règle documentée {rid}** : {RULE_CATALOG.get(rid, '–')}")
        st.markdown("**Règle apprise (S1b)**")
        st.code(s1b.rule, language=None)
    ev = proof.evidence(ls, field, s1b.expr)
    stab = stability(data_dir, field)
    priv = proof.privacy_check(ls, field, s1b.expr)
    with c2:
        m1, m2 = st.columns(2)
        m1.metric("Couverture", f"{s1b.coverage:.0%}", help="Part des lignes que la règle reproduit exactement.")
        m2.metric("Complexité", f"{s1b.expr.complexity:g}", help="Nombre de nœuds de la règle (plus petit = plus simple).")
        m3, m4 = st.columns(2)
        m3.metric("Stabilité LOO", f"{stab['part'].iloc[0]:.0%}",
                  help="Part des plis leave-one-out qui apprennent exactement la même règle.")
        m4.metric("Pseudonymisé", f"{priv['pseudonymisé']:.0%}",
                  help="Couverture de la règle sur les données pseudonymisées (doit égaler le réel).")
    _status_line(field, s1b, ev)

    st.subheader("1 · Vérification ligne par ligne")
    st.caption("La règle est exécutée sur chaque affectation et comparée à la valeur réellement présente dans la cible.")
    n_ok = int(ev["reproduit"].sum())
    st.markdown(f"**{n_ok} / {len(ev)}** lignes reproduites. Les lignes non reproduites sont les candidates anomalies.")
    shown = ev.copy()
    if field in common.KNOWN_FIELDS:
        shown.insert(shown.columns.get_loc("règle documentée →") + 1, "= documentée",
                     (shown["règle apprise →"] == shown["règle documentée →"]) | (shown["verdict moteur"] == "ECART_JUSTIFIE"))
        shown = shown.sort_values(["reproduit", "= documentée"])
        shown["= documentée"] = shown["= documentée"].map({True: "✅", False: "⚠️"})
        st.caption("« = documentée » : la règle apprise donne la même valeur que la règle du mapping. "
                   "Les écarts sont listés en premier.")
    else:
        shown = shown.sort_values("reproduit").drop(columns=["règle documentée →", "verdict moteur"])
    shown["reproduit"] = shown["reproduit"].map({True: "✅", False: "❌"})
    if "verdict moteur" in shown:
        shown["verdict moteur"] = shown["verdict moteur"].map(lambda v: verdict_badge(v) if v else "")
    st.dataframe(shown, use_container_width=True, hide_index=True)

    st.subheader("2 · Pourquoi cette règle plutôt qu'une autre ?")
    st.caption(f"Score = couverture − {dsl.LAMBDA} × complexité. Une règle doit lire la source "
               "(une constante n'est retenue qu'en dernier recours) et couvrir au moins "
               f"{dsl.TAU:.0%} des lignes.")
    alts = proof.alternatives(ls, field)
    alts["retenue"] = alts["règle"] == s1b.rule
    chart = alt.Chart(alts).mark_bar(cornerRadiusEnd=4).encode(
        y=alt.Y("règle:N", sort=None, title=None, axis=alt.Axis(labelLimit=520)),
        x=alt.X("score:Q", title="Score", scale=alt.Scale(zero=False)),
        color=alt.condition("datum.retenue", alt.value(BLUE), alt.value(GRAY)),
        tooltip=["règle", alt.Tooltip("couverture:Q", format=".0%"), "complexité",
                 alt.Tooltip("score:Q", format=".3f"), "lit la source"],
    ).properties(height=34 * len(alts))
    st.altair_chart(chart, use_container_width=True)
    st.caption("En bleu : la règle retenue. Survolez une barre pour voir couverture et complexité.")

    st.subheader("3 · Stabilité : la règle dépend-elle d'une ligne en particulier ?")
    st.caption("On réapprend la règle 22 fois en cachant à chaque fois une affectation différente.")
    st.dataframe(stab.assign(part=stab["part"].map("{:.0%}".format)), use_container_width=True, hide_index=True)

    st.subheader("4 · Robustesse : détecte-t-elle des erreurs injectées ?")
    if field in common.KNOWN_FIELDS:
        b = experiment_b(data_dir, 5)
        fb = evaluate.prf(b[b["champ"] == field], ["méthode"]).reset_index()
        fb["méthode"] = fb["méthode"].map(SHORT)
        chart = alt.Chart(fb).mark_bar(cornerRadiusEnd=4, color=BLUE).encode(
            y=alt.Y("méthode:N", title=None, sort="-x"), x=alt.X("F1:Q", scale=alt.Scale(domain=[0, 1])),
            tooltip=["méthode", alt.Tooltip("précision:Q", format=".2f"), alt.Tooltip("rappel:Q", format=".2f"),
                     alt.Tooltip("F1:Q", format=".2f")],
        ).properties(height=170)
        st.altair_chart(chart, use_container_width=True)
        st.caption("2 valeurs corrompues par champ × 5 graines ; vérité = moteur déterministe sur les données corrompues.")
    else:
        st.caption("Champ ambigu : pas de règle documentée pour servir de vérité terrain. Voir l'onglet Comparaison.")

    st.subheader("5 · Confidentialité")
    st.markdown(f"Couverture sur données réelles **{priv['réel']:.0%}** · sur données pseudonymisées "
                f"**{priv['pseudonymisé']:.0%}** (identifiants factices, dates décalées par ligne). "
                "La règle dépend de la structure des données, pas des valeurs personnelles.")

    st.subheader("6 · Ce que concluent les autres méthodes")
    others = pd.DataFrame([{
        "méthode": r.method, "conclusion": r.rule.split("\n")[0] if r.method.startswith("S2") and "|" in r.rule else r.rule,
        "couverture": None if r.coverage is None else f"{r.coverage:.0%}",
        "lignes signalées": ", ".join(ls.label(i) for i, f in enumerate(r.flags) if f) or "–",
        "notes": r.notes,
    } for r in res.values()])
    st.dataframe(others, use_container_width=True, hide_index=True)
    if not res["S2"].abstained and "|" in res["S2"].rule:
        with st.expander("Arbre de décision S2 (complet)"):
            st.code(res["S2"].rule, language=None)

    st.subheader("Validation experte")
    with st.form(f"fb_{field}"):
        choice = st.radio("Cette règle est-elle correcte ?", ["Valider", "Rejeter", "À discuter"], horizontal=True)
        comment = st.text_input("Commentaire")
        if st.form_submit_button("Enregistrer"):
            FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
            with FEEDBACK.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"date": dt.datetime.now().isoformat(timespec="seconds"), "champ": field,
                                     "règle": s1b.rule, "décision": choice, "commentaire": comment},
                                    ensure_ascii=False) + "\n")
            st.success(f"Décision enregistrée dans `{FEEDBACK.relative_to(ROOT)}`.")


# --------------------------------------------------------------------------- tab 3
def _bars(df: pd.DataFrame, metric: str, title: str, domain=(0, 1)):
    return alt.Chart(df).mark_bar(cornerRadiusEnd=4, color=BLUE).encode(
        y=alt.Y("méthode:N", title=None, sort="-x", axis=alt.Axis(labelLimit=220)),
        x=alt.X(f"{metric}:Q", title=title, scale=alt.Scale(domain=list(domain))),
        tooltip=["méthode", alt.Tooltip(f"{metric}:Q", format=".2f")],
    ).properties(height=36 * len(df))


def _prf_dots(pb: pd.DataFrame):
    long = pb.melt(id_vars="méthode", value_vars=list(METRIC_COLORS), var_name="mesure", value_name="valeur")
    order = pb.sort_values("F1", ascending=False)["méthode"].tolist()
    base = alt.Chart(long).encode(
        y=alt.Y("méthode:N", sort=order, title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X("valeur:Q", scale=alt.Scale(domain=[0, 1]), title=None),
    )
    dots = base.mark_point(filled=True, size=140, opacity=1, stroke="white", strokeWidth=2).encode(
        color=alt.Color("mesure:N", title=None, scale=alt.Scale(domain=list(METRIC_COLORS),
                                                                 range=list(METRIC_COLORS.values())),
                        legend=alt.Legend(orient="top")),
        shape=alt.Shape("mesure:N", title=None, scale=alt.Scale(domain=list(METRIC_COLORS),
                                                               range=["circle", "square", "diamond"])),
        tooltip=["méthode", "mesure", alt.Tooltip("valeur:Q", format=".3f")],
    )
    return dots.properties(height=48 * len(pb))


def tab_compare(data_dir: str):
    st.markdown("Les méthodes apprennent **sans voir la règle documentée** ; on la leur compare ensuite.")
    a = experiment_a(data_dir)
    a = a[a["méthode"] != "S4 Signatures d'écart"]  # S4 judges differences, it learns no rule
    rec = a.groupby("méthode").apply(
        lambda g: (g["accord avec règle documentée"] == 1).sum()).reset_index(name="règles retrouvées")
    rec["méthode"] = rec["méthode"].map(SHORT)
    st.subheader("A · Combien de règles documentées sont retrouvées ? (sur 20)")
    st.altair_chart(_bars(rec, "règles retrouvées", "Règles retrouvées", (0, 20)), use_container_width=True)
    st.caption("S4 (signatures) n'apprend pas de règle : il juge les écarts, il est donc absent de ce graphique.")

    st.subheader("B · Détection d'erreurs injectées")
    seeds = st.slider("Graines", 1, 10, 5, help="2 erreurs injectées par champ et par graine.")
    b = experiment_b(data_dir, seeds)
    pb = evaluate.prf(b, ["méthode"]).reset_index()
    pb["méthode"] = pb["méthode"].map(SHORT)
    st.altair_chart(_prf_dots(pb), use_container_width=True)
    st.caption("S4 utilise la valeur attendue de la règle documentée : c'est une borne haute, pas un apprenant.")
    with st.expander("Tableau"):
        st.dataframe(pb.round(3), use_container_width=True, hide_index=True)

    st.markdown("**F1 par champ et par méthode**")
    fb = evaluate.prf(b, ["champ", "méthode"]).reset_index()
    fb["méthode"] = fb["méthode"].map(SHORT)
    fb = fb.dropna(subset=["F1"])  # S2 is not applicable to high-cardinality fields
    heat = alt.Chart(fb).mark_rect(stroke="white", strokeWidth=2).encode(
        x=alt.X("méthode:N", title=None, axis=alt.Axis(labelAngle=0, labelLimit=160)),
        y=alt.Y("champ:N", title=None, axis=alt.Axis(labelLimit=220)),
        color=alt.Color("F1:Q", scale=alt.Scale(scheme="blues", domain=[0, 1]), title="F1"),
        tooltip=["champ", "méthode", alt.Tooltip("précision:Q", format=".2f"),
                 alt.Tooltip("rappel:Q", format=".2f"), alt.Tooltip("F1:Q", format=".2f")],
    ).properties(height=560)
    st.altair_chart(heat, use_container_width=True)

    st.subheader("Ce que montre la comparaison")
    st.markdown(
        "- **S1b** (recherche de règles + conditions) retrouve 18 règles documentées sur 20, avec une précision ≈ 99 %.\n"
        "- **Les arbres de décision (S2)** sur-apprennent sur 22 lignes : beaucoup de fausses alertes.\n"
        "- **Aucun apprenant** ne détecte une erreur qui ressemble à la majorité (ex. `JWN` au lieu de `XFLR`) : "
        "les règles documentées restent l'autorité, l'apprentissage comble les trous.\n"
        "- **assignmentStartDate** : toutes les méthodes apprennent `max(DateEntréePoste, poste.max_eff)` à 100 % — "
        "la cible applique une autre règle que celle documentée.\n"
        "- **Heures** : la cible copie les heures contractuelles du poste (`poste.HeuresSemaineContrat`, 100 %, "
        "stable) — cohérent avec la description « heures semaine **du poste** »."
    )


# --------------------------------------------------------------------------- main
def main():
    st.title("CorroborIA")
    st.caption("Corroboration Système A – RH ↔ Système B – Temps : règles métier déterministes + règles apprises.")
    data_dir = data_source()
    t1, t2, t3 = st.tabs(["Corroboration", "Règles apprises & preuves", "Comparaison des méthodes"])
    with t1:
        tab_corroboration(data_dir)
    with t2:
        tab_rules(data_dir)
    with t3:
        tab_compare(data_dir)


main()
