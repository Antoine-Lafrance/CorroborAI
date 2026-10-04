"""CorroborIA — web API + static front end.

Lancer : .venv/bin/python -m corroborai serve      (http://localhost:8000, localhost uniquement)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corroborai import loaders, pipeline, report  # noqa: E402
from corroborai.learn import common, dsl, evaluate, proof  # noqa: E402
from corroborai.rules import FIELD_RULES, RULE_CATALOG  # noqa: E402
from corroborai.tree import VERDICTS  # noqa: E402

DEFAULT_DATA = ROOT / "data" / "raw"
LEARN_OUT = ROOT / "out" / "learning"
PROOF_CACHE = ROOT / "out" / "cache" / "proofs"
STATIC = Path(__file__).resolve().parent / "static"
REQUIRED = [loaders.SOURCE_FILE, loaders.TARGET_FILE, loaders.POSTE_FILE, loaders.MOTIF_FILE]

app = FastAPI(title="CorroborIA")
DATASETS: Dict[str, dict] = {"default": {"id": "default", "name": "Jeu fourni (data/raw)", "path": DEFAULT_DATA}}
_cache: Dict[tuple, pipeline.Result] = {}
_ls: Dict[str, common.LearningSet] = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------- helpers
def _path(ds: str) -> Path:
    if ds not in DATASETS:
        raise HTTPException(404, f"Jeu de données inconnu : {ds}")
    return DATASETS[ds]["path"]


def _overrides_version() -> float:
    return pipeline.OVERRIDES.stat().st_mtime if pipeline.OVERRIDES.exists() else 0.0


def result(ds: str) -> pipeline.Result:
    key = (ds, _overrides_version())
    with _lock:
        if key not in _cache:
            _cache[key] = pipeline.run(_path(ds))
        return _cache[key]


def learning_set(ds: str) -> common.LearningSet:
    with _lock:
        if ds not in _ls:
            res = _cache.get((ds, _overrides_version()))
            _ls[ds] = res.learning_set if res and res.learning_set else common.build(_path(ds))
        return _ls[ds]


def _clean(v):
    if v is None or (not isinstance(v, (list, dict, str)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat() if v == v.normalize() else v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    return v


def records(df: pd.DataFrame) -> List[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict("records")]


# --------------------------------------------------------------------------- API
@app.get("/api/datasets")
def datasets():
    return [{"id": d["id"], "name": d["name"]} for d in DATASETS.values()]


@app.post("/api/upload")
async def upload(files: List[UploadFile] = File(...)):
    names = {f.filename for f in files}
    missing = [r for r in REQUIRED if r not in names]
    if missing:
        raise HTTPException(400, "Fichiers manquants : " + ", ".join(missing))
    d = Path(tempfile.mkdtemp(prefix="corroborai_"))
    for f in files:
        if f.filename in REQUIRED:
            (d / f.filename).write_bytes(await f.read())
    ds = uuid.uuid4().hex[:8]
    DATASETS[ds] = {"id": ds, "name": f"Extraction chargée ({ds})", "path": d}
    try:
        result(ds)
    except Exception as e:  # noqa: BLE001 — surface any parsing problem to the user
        shutil.rmtree(d, ignore_errors=True)
        del DATASETS[ds]
        raise HTTPException(400, f"Impossible de corroborer ces fichiers : {e}")
    return {"id": ds, "name": DATASETS[ds]["name"]}


@app.get("/api/results")
def results(ds: str = "default"):
    res = result(ds)
    df = res.df
    by_field = pd.crosstab(df["ChampCible"], df["Verdict"]).reindex(columns=list(VERDICTS), fill_value=0)
    layers = pd.crosstab(df["VerdictDeterministe"], df["Verdict"]).reindex(
        index=list(VERDICTS), columns=list(VERDICTS), fill_value=0)
    return {
        "dataset": DATASETS[ds]["name"],
        "rows": records(df),
        "counts": {v: int((df["Verdict"] == v).sum()) for v in VERDICTS},
        "deterministic": {v: int((df["VerdictDeterministe"] == v).sum()) for v in VERDICTS},
        "flow": {a: {b: int(layers.at[a, b]) for b in VERDICTS} for a in VERDICTS},
        "layers": {c: int((df["Couche"] == c).sum()) for c in ("DETERMINISTE", "IA", "EXPERT")},
        "byField": [{"field": f, **{v: int(r[v]) for v in VERDICTS}} for f, r in by_field.iterrows()],
        "learned": pipeline.learned_rules(res),
        "demo": {k: r["Id"] for k, r in pipeline.demo_cases(df).items()},
        "overrides": pipeline.load_overrides(),
        "employees": int(df["Matricule"].nunique()),
    }


@app.get("/api/row")
def row(id: str, ds: str = "default"):
    """Source and target records behind one verdict."""
    df = result(ds).df
    hit = df[df["Id"] == id]
    if hit.empty:
        raise HTTPException(404, "Ligne inconnue")
    r = hit.iloc[0]
    ls = learning_set(ds)
    pair = next((p for i, p in enumerate(ls.pairs)
                 if ls.label(i) == f"{r['Matricule']}/{r['TypeAffectation']}"), None)
    used = [c.strip() for c in str(r["ChampsSource"]).replace("+", ",").split(",")]
    src, dst = (pair if pair else ({}, {}))
    return {
        "source": {k: _clean(v) for k, v in src.items()},
        "target": {k: _clean(v) for k, v in dst.items()},
        "sourceUsed": [c for c in used if c in src],
        "targetField": r["ChampCible"],
    }


@app.get("/api/rules")
def rules():
    df = result("default").df
    return [{
        "field": fr.target, "sources": fr.sources, "rule": fr.rule_id,
        "description": common.FIELD_DESCRIPTIONS.get(fr.target, ""),
        "catalog": RULE_CATALOG.get(fr.rule_id, ""),
        "tree": "\n".join(fr.tree.describe("")),
        "ambiguous": fr.target in common.AMBIGUOUS_FIELDS,
        "counts": {v: int(((df["ChampCible"] == fr.target) & (df["Verdict"] == v)).sum()) for v in VERDICTS},
    } for fr in FIELD_RULES] + [{
        "field": "(affectation)", "sources": "Matricule, CodeEmploi, TypeAffectation, DateEntréePoste",
        "rule": "R-MATCH", "description": "Appariement des affectations", "catalog": RULE_CATALOG["R-MATCH"],
        "tree": "? Affectation source appariée à une ligne cible (score : emploi +2, type +1, date +1) ?\n"
                "  ├─ oui: comparer chaque champ mappé\n  ├─ non:\n  │   ⇒ ANOMALIE (R-MATCH)",
        "ambiguous": False,
        "counts": {v: int(((df["ChampCible"] == "(affectation)") & (df["Verdict"] == v)).sum()) for v in VERDICTS},
    }]


_proofs: Dict[tuple, dict] = {}
_proof_lock = threading.Lock()
_proof_locks: Dict[tuple, threading.Lock] = {}


@app.get("/api/field/{field}")
def field_proof(field: str, ds: str = "default"):
    """Evidence for the rule the AI layer learns on one field (cached; precomputed at startup)."""
    if field not in evaluate.RULES:
        raise HTTPException(404, "Champ inconnu")
    key = (ds, field)
    with _proof_lock:
        lock = _proof_locks.setdefault(key, threading.Lock())
    with lock:
        if key not in _proofs:
            cache = PROOF_CACHE / f"{field}.json" if ds == "default" else None
            if cache and cache.exists() and cache.stat().st_mtime > _data_mtime():
                _proofs[key] = json.loads(cache.read_text(encoding="utf-8"))
            else:
                _proofs[key] = _field_proof(field, ds)
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(_proofs[key], ensure_ascii=False), encoding="utf-8")
        return _proofs[key]


def _data_mtime() -> float:
    return max(p.stat().st_mtime for p in [*DEFAULT_DATA.glob("*.xlsx"), *(ROOT / "corroborai").rglob("*.py")])


def _field_proof(field: str, ds: str) -> dict:
    ls = learning_set(ds)
    s1b = dsl.learn(field, ls.X, ls.y(field), conditional=True)
    ev = proof.evidence(ls, field, s1b.expr)
    alts = proof.alternatives(ls, field, k=6)
    # Leave-one-out only matters for an accepted rule (and is the slow part on long strings).
    stab = proof.stability(ls, field) if not s1b.abstained else pd.DataFrame(columns=["règle apprise", "plis", "part"])
    priv = proof.privacy_check(ls, field, s1b.expr)
    return {
        "field": field, "rule": s1b.rule, "coverage": s1b.coverage, "reliable": not s1b.abstained,
        "tau": dsl.TAU, "lambda": dsl.LAMBDA,
        "evidence": records(ev), "alternatives": records(alts), "stability": records(stab), "privacy": priv,
    }


class Override(BaseModel):
    id: Optional[str] = None
    champ: str
    signature: Optional[str] = None
    portee: str
    verdict: str
    commentaire: str = ""


@app.post("/api/override")
def add_override(o: Override):
    if o.verdict not in VERDICTS or o.portee not in ("ligne", "motif"):
        raise HTTPException(400, "Verdict ou portée invalide")
    if o.portee == "ligne" and not o.id:
        raise HTTPException(400, "Identifiant de ligne requis")
    if o.portee == "motif" and not o.signature:
        raise HTTPException(400, "Signature requise pour une règle par motif")
    return pipeline.add_override(o.dict())


@app.delete("/api/override/{index}")
def delete_override(index: int):
    if not 0 <= index < len(pipeline.load_overrides()):
        raise HTTPException(404, "Décision inconnue")
    pipeline.remove_override(index)
    return {"ok": True}


@app.get("/api/evaluation")
def evaluation():
    """Rule-learning benchmark produced by `python -m corroborai learn` (if present)."""
    a, b = LEARN_OUT / "A_regles_connues.csv", LEARN_OUT / "B_erreurs_synthetiques.csv"
    if not (a.exists() and b.exists()):
        return {"available": False}
    da, db = pd.read_csv(a), pd.read_csv(b)
    found = (da[da["méthode"] != "S4 Signatures d'écart"].groupby("méthode")["accord avec règle documentée"]
             .apply(lambda s: int((s == 1).sum())).reset_index(name="règles retrouvées"))
    prf = evaluate.prf(db, ["méthode"]).reset_index()
    return {"available": True, "found": records(found), "prf": records(prf), "nDocumented": len(common.KNOWN_FIELDS),
            "seeds": int(db["graine"].nunique())}


@app.get("/api/export.xlsx")
def export_xlsx(ds: str = "default"):
    res = result(ds)
    with tempfile.TemporaryDirectory() as tmp:
        path = report.write(res.df, Path(tmp), pipeline.learned_rules(res), pipeline.load_overrides())
        data = path.read_bytes()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="rapport_corroboration.xlsx"'})


@app.get("/api/export.csv")
def export_csv(ds: str = "default"):
    data = result(ds).df.to_csv(index=False).encode("utf-8-sig")
    return Response(data, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="corroboration.csv"'})


@app.on_event("startup")
def _warm_up():
    """Run the corroboration, then precompute every field's proof, ambiguous fields first."""
    def work():
        res = result("default")
        for f in [fr.target for fr in FIELD_RULES]:
            field_proof(f)
    threading.Thread(target=work, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
