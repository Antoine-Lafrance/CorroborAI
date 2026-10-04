# CorroborIA — détection intelligente des écarts

Compare l'extraction **Système A – RH** (source) à **Système B – Temps** (cible), applique les règles métier du
mapping, fait trancher par une couche d'IA locale ce que les règles ne peuvent pas trancher, et produit un rapport
qui ne garde que les **vraies erreurs à investiguer**, chacune avec sa règle, sa justification, sa confiance et son
chemin de décision complet.

Sur le jeu fourni : **529 vérifications** (20 employés, 22 affectations × 24 champs mappés + appariement)
→ 441 conformes, 78 écarts justifiés (dont 52 tranchés par l'IA), **10 anomalies à investiguer**, 0 cas laissé ambigu.

| Verdict | Sens |
|---|---|
| `CONFORME` | La cible contient la valeur exigée par le mapping. |
| `ECART_JUSTIFIE` | Diffère de la source brute, mais une validation complémentaire l'explique (table des motifs, historique du poste, accents, encodage, zéro-padding, préfixe d'environnement) ou l'IA établit que c'est la convention appliquée par l'interface. |
| `ANOMALIE` | La cible contredit la règle → à investiguer, avec une priorité. |
| `AMBIGU` | Ni les règles ni l'IA ne tranchent → validation experte. |

## Démarrage rapide

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m corroborai serve        # interface web → http://localhost:8000
```

Autres commandes :

```bash
.venv/bin/python -m corroborai run          # → out/rapport_corroboration.xlsx + out/corroboration.csv
.venv/bin/python -m corroborai demo         # les 3 cas de la démonstration (+ un cas tranché par l'IA) dans le terminal
.venv/bin/python -m corroborai trees contractTypeCode   # affiche l'arbre de décision d'un champ
.venv/bin/python -m corroborai learn        # banc d'essai des méthodes d'apprentissage → out/learning/
.venv/bin/python -m pytest -q               # 24 tests
```

Python 3.9+. Aucune clé d'API n'est nécessaire : toute l'IA tourne en local.

**Données** : les fichiers du défi (à accès restreint) ne sont pas versionnés. Placez `Employe_Source_Anonymise_VF.xlsx`,
`Employe_Destination_Anonymise_VF.xlsx`, `détail_du_poste.xlsx` et `Motif de la situation d'emploi.xlsx` dans `data/raw/`.

## Interface web

| Vue | Contenu |
|---|---|
| **Vue d'ensemble** | Indicateurs, passage du brut au rapport final couche par couche, priorités d'investigation, verdicts par champ (cliquer un champ pour filtrer). |
| **À investiguer** | Le rapport final : anomalies (et ambigus restants) triées par priorité. |
| **Toutes les vérifications** | Les 529 lignes, filtres par verdict, couche (règle métier / IA / expert), champ, recherche libre. |
| **Démonstration** | Un cas conforme, un écart justifié automatiquement, une vraie anomalie (+ un cas ambigu tranché par l'IA), chacun avec son chemin de décision. |
| **Règles & IA** | Pour chaque champ : règle documentée, arbre de décision appliqué, règle apprise par l'IA et ses preuves (couverture, stabilité leave-one-out, invariance à la pseudonymisation, hypothèses concurrentes, vérification ligne par ligne). |
| **Données & décisions** | Chargement de nouvelles extractions, export Excel/CSV, décisions expertes enregistrées, évaluation des méthodes. |

Cliquer une vérification ouvre son **détail** : valeur attendue / valeur cible / valeur brute, justification, chemin
de décision nœud par nœud, **données source et cible ayant servi à la décision** (colonnes utilisées surlignées) et
formulaire de **correction experte**.

Chargement d'autres extractions : déposer les 4 fichiers (`Employe_Source_Anonymise_VF.xlsx`,
`Employe_Destination_Anonymise_VF.xlsx`, `détail_du_poste.xlsx`, `Motif de la situation d'emploi.xlsx`) dans la vue
*Données*. Ils sont copiés dans un dossier temporaire ; les originaux ne sont jamais modifiés.

## Rapport de corroboration exportable

`out/rapport_corroboration.xlsx` (ou bouton *Rapport Excel*) :

| Onglet | Contenu |
|---|---|
| Résumé | Indicateurs, verdicts par couche, verdicts par champ. |
| **À investiguer** | Uniquement les anomalies, triées par priorité : valeurs attendue/cible, règle, justification, récurrence, chemin. |
| Écarts justifiés / Conformes | Ce qui a été écarté, avec la raison. |
| Détail complet | Toutes les vérifications et toutes les colonnes. |
| Règles apprises (IA) | Règle apprise par champ, couverture, stabilité, signatures d'écart. |
| Catalogue des règles | Identifiants `R-*` et leur description. |
| Décisions expertes | Corrections enregistrées (si présentes). |

Colonnes clés : `Verdict`, `Couche` (`DETERMINISTE` / `IA` / `EXPERT`), `Regle`, `Justification`, `Chemin`
(chaque question posée et sa réponse), `Confiance`, `Priorite`, `Impact`, `VerdictDeterministe` (verdict des règles
seules, pour distinguer ce qui relève de la règle et de l'IA), `MethodeIA`, `Signature`, `Recurrence`.
`out/corroboration.csv` contient les mêmes lignes.

## Architecture

```
data/raw/ (lecture seule)
 1. Comparaison brute + normalisation
    loaders.py     lecture (détail du poste = CSV dans une colonne Excel, dates en série Excel)
    normalize.py   vides, dates ISO, nombres, accents, mojibake, préfixe d'environnement
    engine.py      appariement des affectations (matricule + emploi + type + date) → R-MATCH
 2. Règles métier (déterministe)
    rules.py       un arbre de décision par champ cible, identifiants R-*
    tree.py        Compute / Node / Leaf, trace complète du chemin
 3. Couche IA (locale)
    ai.py          tranche les AMBIGU : règle apprise (S1b) puis signatures d'écart (S4) ; confiance ; priorité
    learn/         dsl.py (synthèse de règles), signatures.py, proof.py (preuves), tree_induction.py, evaluate.py
 4. Décisions expertes
    pipeline.py    applique out/expert_overrides.jsonl (par ligne ou par forme d'écart), ordonne le rapport
 5. Sorties
    report.py      Excel multi-onglets + CSV
app/server.py      API FastAPI ; app/static/ interface (HTML/CSS/JS, sans étape de build)
```

## Règles prises en charge

| Règle | Champs | Logique |
|---|---|---|
| R-MATCH | (affectation) | Affectation source sans équivalent cible (ou inverse) → anomalie. |
| R-DIRECT | onboardDate, siteName, siteCode, divisionId, divisionCode, positionId, positionCode, payGradeId | Copie directe, après normalisation. |
| R-NOM | givenName, surname | Copie ; accents retirés → écart justifié. |
| R-EMAIL | contactEmail | Initiale + nom + 3 derniers chiffres du matricule + domaine ; préfixe d'environnement toléré. |
| R-DIVNAME / R-POSNAME | divisionName, positionName | Code + « - » + libellé ; zéro-padding toléré. |
| R-SIT (+R-ENC) | statusReasonCode, expectedReturnDate, detailedStatus | Code d'accès 00/01 → Actif ; 02/03/06/07 → Absence complète, code Remphor via la table des motifs, date de retour ; réparation d'encodage. |
| R-CT | contractTypeCode | V + permanent + temps plein → JWN ; V + permanent + temps partiel → XFLR ; T, O, M, R, J, Z, Q → codes dédiés. |
| R-AFF | isPrimaryAssignment, isTemporaryAssignment | P → primaire ; A → temporaire ; S → aucun des deux. |
| R-ASD / R-AED | assignmentStartDate, assignmentEndDate, termEndDate | Combinaison date d'entrée/sortie et historique d'unité administrative (détail du poste). |
| R-HRS | weeklyHoursOverride, dailyHoursOverride | Norme de l'employé ; si la cible reprend les heures contractuelles du poste → ambigu → IA. |

## Utilisation de l'IA

Les règles déterministes laissent 52 vérifications **ambiguës** (aucune règle documentée ne tranche). Pour chaque
champ concerné, la couche IA (`corroborai/ai.py`) observe toute la population, sans voir la règle documentée :

1. **Synthèse de règles (S1b)** — recherche, dans un petit langage (copie, transformations de texte, concaténation,
   min/max de dates, préfixe/suffixe, condition SI…ALORS…SINON), de la règle qui reproduit le mieux la cible.
   Score = couverture − 0,02 × complexité ; acceptée si couverture ≥ 80 % et si elle lit la source.
   Si la ligne respecte la règle apprise → écart justifié, confiance = couverture × stabilité leave-one-out ;
   sinon → anomalie.
2. **Signatures d'écart (S4)** — chaque écart attendu → cible est réduit à sa forme (lettres → `a`, chiffres → `9`).
   Une forme partagée par ≥ 50 % des affectations est **systémique** (convention de l'interface) → écart justifié ;
   une forme rare est **isolée** → anomalie, d'autant plus prioritaire qu'elle est rare.

Résultat sur le jeu fourni :

| Champ | Constat de l'IA | Verdict |
|---|---|---|
| weekly/dailyHoursOverride | La cible applique `poste.HeuresSemaineContrat` / `HeuresJourContrat` (100 % des lignes, 100 % stable) — cohérent avec « heures semaine **du poste** » dans le mapping. | 8 écarts justifiés |
| contactEmail | Même transformation (préfixe `dev-08-v2_` + identifiant recodé) sur 22/22 affectations. | 22 écarts justifiés |
| positionName | Même forme de recodage `9-a9 → 9-a9` sur 22/22 affectations. | 22 écarts justifiés |

**Priorisation** : `Priorite` (0–100) = impact métier du champ (paie & horaire, affectation, statut, organisation,
identité) × confiance du verdict. L'affectation manquante arrive en tête, suivie des types d'employé erronés.

**Retour expert** : depuis le détail d'une vérification, un expert impose un verdict soit pour **cette ligne**, soit
pour **toutes les lignes du champ ayant la même forme d'écart** ; cette décision devient une règle appliquée à chaque
corroboration suivante (`out/expert_overrides.jsonl`, couche `EXPERT`, traçée dans le chemin).

**Évaluation** (`python -m corroborai learn`, détail dans `out/learning/comparaison.md`) : sans voir les règles
documentées, S1b en retrouve 18 sur 20 ; sur des erreurs injectées (2 par champ × 5 graines), la combinaison
S1b → S4 détecte les erreurs avec une précision de 0,99 et un F1 de 0,92. Les arbres de décision scikit-learn
(S2) sur-apprennent sur 22 lignes (F1 0,43) : c'est pourquoi ils ne sont pas utilisés en production.

**LLM (optionnel, désactivé)** : `python -m corroborai learn --llm` ajoute une méthode S3 où un LLM propose une
règle dans le même langage, vérifiée en local. Les données transmises sont minimisées et pseudonymisées
(`corroborai/learn/privacy.py` : champs ambigus seulement, liste blanche de colonnes, identifiants factices, dates
décalées, 6 lignes) et auditables sans envoi via `python -m corroborai llm-payload`. Ne l'activer que vers un
service autorisé par les organisateurs.

## Confidentialité et contraintes

- Fichiers sources ouverts en lecture seule ; rien n'est écrit dans `data/`.
- Serveur web limité à `127.0.0.1`. L'IA par défaut est entièrement locale : aucune donnée ne quitte le poste.
- Seuls les champs présents dans le mapping sont corroborés.
- Chaque verdict porte sa règle (`Regle`), sa couche (`Couche`) et son chemin (`Chemin`) : on distingue toujours
  ce qui relève d'une règle déterministe de ce qui relève de l'IA.

## Hypothèses

- **R-ASD (assignmentStartDate)** : le mapping dit « date la plus ancienne », mais appliqué littéralement les 22 lignes
  sont en anomalie. La cible retient systématiquement la **plus récente** des deux dates (ce que les méthodes
  d'apprentissage retrouvent aussi à 100 %) ; c'est la politique par défaut (`ASSIGNMENT_START_POLICY` dans `rules.py`).
- La date de référence pour le « détail du poste courant » est la date du jour.
- Les courriels (`PNom10370370`) et libellés d'emploi (`3649-Empl3649`) sont recodés par l'anonymisation : la structure
  est vérifiée de façon déterministe, la cohérence d'ensemble par l'IA.

## Limites

- 22 affectations : les règles apprises sont vérifiées (stabilité, robustesse) mais restent à confirmer par l'équipe
  métier — d'où la validation experte.
- Une erreur qui ressemble à la majorité (ex. un `JWN` systématiquement remplacé par `XFLR`) serait jugée systémique
  par S4 ; les règles documentées restent l'autorité et l'IA ne tranche que ce qu'elles laissent ambigu.
- Le langage de règles couvre les transformations observées dans le mapping ; une règle d'une autre nature
  ne serait pas apprise (le champ resterait jugé par signatures).
- Les extractions chargées et les décisions expertes sont stockées localement (pas de gestion multi-utilisateur).
