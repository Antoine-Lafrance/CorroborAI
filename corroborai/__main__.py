"""CLI: python -m corroborai [run|demo|serve|learn|llm-payload|trees] ..."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .rules import FIELD_RULES


def main() -> None:
    p = argparse.ArgumentParser(prog="corroborai")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Corroborer source vs cible (règles + IA) et exporter le rapport")
    r.add_argument("--data", type=Path, default=Path("data/raw"))
    r.add_argument("--out", type=Path, default=Path("out"))
    r.add_argument("--no-ai", action="store_true", help="Règles déterministes seulement (les ambigus restent ambigus)")
    d = sub.add_parser("demo", help="Afficher un cas conforme, un écart justifié, une anomalie et un cas résolu par l'IA")
    d.add_argument("--data", type=Path, default=Path("data/raw"))
    s = sub.add_parser("serve", help="Lancer l'interface web (http://localhost:8000)")
    s.add_argument("--port", type=int, default=8000)
    l = sub.add_parser("learn", help="Comparer les stratégies d'apprentissage de règles")
    l.add_argument("--data", type=Path, default=Path("data/raw"))
    l.add_argument("--out", type=Path, default=Path("out/learning"))
    l.add_argument("--seeds", type=int, default=5)
    l.add_argument("--llm", action="store_true", help="Inclure S3 (LLM via API OpenAI, clé dans .env)")
    sub.add_parser("llm-payload", help="Écrire (sans envoyer) les données qui seraient transmises au LLM")
    t = sub.add_parser("trees", help="Afficher les arbres de décision")
    t.add_argument("field", nargs="?", help="Champ cible (défaut : tous)")
    args = p.parse_args()

    if args.cmd == "trees":
        for fr in FIELD_RULES:
            if args.field in (None, fr.target):
                print(f"\n■ {fr.target}  ←  {fr.sources}  [{fr.rule_id}]")
                print("\n".join(fr.tree.describe("  ")))
        return

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("app.server:app", host="127.0.0.1", port=args.port)
        return

    if args.cmd == "llm-payload":
        from .learn import common, llm
        ls = common.build(Path("data/raw"))
        for f in sorted(llm.LLM_FIELDS):
            llm.payload(f, ls.X, ls.y(f))
        print(f"Charges utiles écrites dans {llm.AUDIT_DIR} (rien n'a été envoyé)")
        return

    if args.cmd == "learn":
        from .learn import common, evaluate
        path = evaluate.write_report(common.build(args.data), args.out, args.seeds, args.llm)
        print(f"Rapport de comparaison : {path}")
        return

    from . import pipeline, report

    res = pipeline.run(args.data, use_ai=not getattr(args, "no_ai", False))
    if args.cmd == "demo":
        titles = {"conforme": "CAS CONFORME", "ecart_justifie": "ÉCART JUSTIFIÉ AUTOMATIQUEMENT",
                  "anomalie": "VRAIE ANOMALIE DÉTECTÉE", "ia": "CAS AMBIGU RÉSOLU PAR L'IA"}
        for k, row in pipeline.demo_cases(res.df).items():
            print(f"\n━━ {titles[k]} ━━  {row['Matricule']} ({row['TypeAffectation']}) · {row['ChampCible']}")
            print(f"  attendu  : {row['ValeurAttendue']}\n  cible    : {row['ValeurCible']}")
            print(f"  verdict  : {row['Verdict']}  [couche {row['Couche']}, règle {row['Regle']}, "
                  f"confiance {row['Confiance']:.0%}]")
            print(f"  pourquoi : {row['Justification']}")
            for i, step in enumerate(str(row["Chemin"]).split(" | "), 1):
                print(f"    {i:>2}. {step}")
        return

    path = report.write(res.df, args.out, pipeline.learned_rules(res), pipeline.load_overrides())
    with pd.option_context("display.width", 200):
        print(report.summary(res.df).to_string())
    print(f"\nRapport : {path}\nCSV     : {args.out / 'corroboration.csv'}")


if __name__ == "__main__":
    main()
