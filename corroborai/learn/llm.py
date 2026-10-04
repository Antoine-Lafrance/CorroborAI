"""S3 — LLM rule proposer (generate & verify), via the OpenAI API.

The LLM sees the field description, the feature names, a few example rows and the target
values, and must answer with a rule in the S1 JSON language. The proposal is then executed
on every row: coverage, not the LLM's own claim, decides whether it is kept.

Configuration comes from .env (see .env.example): OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL.

Privacy: only fields in LLM_FIELDS (no documented rule decides them) are ever sent; rows go
through privacy.Pseudonymizer (column allowlist, fake identifiers, shifted dates); only
N_EXAMPLES rows are sent; every payload is written to AUDIT_DIR before sending.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List

from dotenv import load_dotenv

from . import dsl, privacy
from .common import AMBIGUOUS_FIELDS, FIELD_DESCRIPTIONS, Result, key

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
SAMPLES = 3
N_EXAMPLES = 6
LLM_FIELDS = set(AMBIGUOUS_FIELDS)
AUDIT_DIR = Path(__file__).resolve().parents[2] / "out" / "llm_payload"

GRAMMAR = """Langage de règle (JSON) :
  {"op":"col","name":"<colonne>"}
  {"op":"const","value":"<valeur>"}
  {"op":"fn","fn":"strip_accents|lower|upper|first_char|last3|zpad5","arg":<règle>}
  {"op":"concat","sep":"<séparateur>","args":[<règle>, ...]}
  {"op":"date_min"|"date_max","args":[<règle>,<règle>]}
  {"op":"affix","prefix":"<texte>","suffix":"<texte>","core":<règle>}
  {"op":"if_eq","col":"<colonne>","value":"<valeur>","then":<règle>,"else":<règle>}"""


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(API_KEY) and not API_KEY.startswith("sk-...")


def _prompt(field: str, X: List[dict], y: List[Any]) -> str:
    """X / y must already be pseudonymised."""
    cols = sorted(c for c in X[0] if any(r.get(c) is not None for r in X))
    rows = [{**{c: key(r.get(c)) for c in cols}, "→ CIBLE": key(t)} for r, t in list(zip(X, y))[:N_EXAMPLES]]
    return (
        f"Champ cible : {field} — « {FIELD_DESCRIPTIONS.get(field, '')} ».\n"
        f"Trouve la règle qui calcule la valeur CIBLE à partir des colonnes disponibles.\n\n{GRAMMAR}\n\n"
        f"Exemples :\n{json.dumps(rows, ensure_ascii=False, indent=1)}\n\n"
        'Réponds UNIQUEMENT en JSON : {"rule": <règle>, "explanation": "<une phrase en français>"}'
    )


def _ask(prompt: str, temperature: float) -> dict:
    body = json.dumps({
        "model": MODEL,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Tu analyses un transfert de données RH vers un système de gestion "
                                          "du temps et tu déduis les règles de transformation des champs."},
            {"role": "user", "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(json.load(r)["choices"][0]["message"]["content"])
    except urllib.error.HTTPError as e:
        raise LLMUnavailable(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMUnavailable(str(e)) from e


def payload(field: str, X: List[dict], y: List[Any]) -> str:
    """Exact text that would be sent for this field, after pseudonymisation; also written to AUDIT_DIR."""
    if field not in LLM_FIELDS:
        raise ValueError(f"{field} : champ couvert par une règle documentée, jamais envoyé au LLM")
    Xp, yp = privacy.Pseudonymizer().rows(X[:N_EXAMPLES], y[:N_EXAMPLES])
    text = _prompt(field, Xp, yp)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    (AUDIT_DIR / f"{field}.txt").write_text(text, encoding="utf-8")
    return text


def learn(field: str, X: List[dict], y: List[Any]) -> Result:
    name = f"S3 LLM ({MODEL})"
    if field not in LLM_FIELDS:
        return Result(name, field, "non envoyé (champ couvert par une règle documentée)", None,
                      [False] * len(y), abstained=True)
    if not available():
        return Result(name, field, "non exécuté (OPENAI_API_KEY absente, voir .env.example)", None,
                      [False] * len(y), abstained=True)
    prompt = payload(field, X, y)
    proposals, errors = [], []
    for i in range(SAMPLES):
        try:
            ans = _ask(prompt, temperature=0.0 if i == 0 else 0.7)
            expr = dsl.from_json(ans["rule"])
            m = dsl._matches(expr, X, y)
            proposals.append((float(m.mean()), expr, m, ans.get("explanation", "")))
        except LLMUnavailable:
            raise
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            errors.append(str(e))
    if not proposals:
        return Result(name, field, "aucune proposition valide", None, [False] * len(y), abstained=True,
                      notes="; ".join(errors)[:200])
    proposals.sort(key=lambda p: -p[0])
    cov, expr, m, why = proposals[0]
    agree = sum(str(p[1]) == str(expr) for p in proposals) / SAMPLES
    if cov < dsl.TAU:
        return Result(name, field, str(expr), cov, [False] * len(y), abstained=True, confidence=agree,
                      notes=f"proposition rejetée à la vérification. {why}")
    return Result(name, field, str(expr), cov, [not x for x in m], [expr(r) for r in X],
                  confidence=agree, notes=why)
