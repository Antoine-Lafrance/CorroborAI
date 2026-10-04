# CorroborIA — texte pour Devpost

**Défi / prix à sélectionner (un seul)** : CorroborIA – Détection intelligente des écarts (Loto-Québec)
**Équipe** : <même nom d'équipe que sur HxBuddy> — <mêmes membres que sur HxBuddy>

## En une phrase
Un corroborateur hybride règles + IA qui compare Système A – RH et Système B – Temps, explique chaque verdict et ne
présente à l'équipe que les 10 vraies anomalies sur 529 vérifications.

## Ce que ça fait
- **Règles métier** du mapping codées en arbres de décision traçables (chaque question posée et sa réponse) :
  441 conformes, 26 écarts justifiés (table des motifs, historique du poste, accents, encodage, zéro-padding,
  préfixe d'environnement), 10 anomalies, 52 cas ambigus.
- **Couche IA locale** sur les 52 ambigus : synthèse de règles (retrouve que la cible copie les heures
  contractuelles du poste, 100 % de couverture et de stabilité) et signatures d'écart (transformation systémique
  sur 22/22 courriels et libellés). 52/52 tranchés, avec confiance et justification.
- **Priorisation** des anomalies (impact métier × confiance) et **retour expert** qui se généralise en règle.
- **Interface web** : vue d'ensemble, liste à investiguer, détail avec données source/cible et chemin de décision,
  démonstration des 3 types de cas, preuves des règles apprises, chargement d'extractions.
- **Rapport Excel/CSV** exportable : onglet « À investiguer » trié par priorité, règle, justification, chemin.

## IA et confidentialité
Aucune donnée n'est envoyée à un service externe : l'apprentissage de règles et les signatures tournent en local.
Évaluation : sans voir les règles documentées, la méthode retrouve 18 règles sur 20 ; sur erreurs injectées,
précision 0,99, F1 0,92.

## Technologies
Python, pandas, scikit-learn (comparaison), FastAPI, HTML/CSS/JS, openpyxl.

## Lancer
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m corroborai serve   # http://localhost:8000
