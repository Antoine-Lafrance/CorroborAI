"""Pseudonymisation + minimisation applied to every row before it leaves the machine.

* Allowlist: only the columns in ALLOWED are sent; anything else (absence data, manager,
  reason labels, new columns) is dropped by default.
* Identifiers (employee number, names) are replaced by consistent fakes; any occurrence of the
  real value inside a target string (e.g. an email) is replaced too, and long digit runs left in
  target strings are re-drawn.
* Dates are shifted by a random offset drawn per row, so relations inside a row (equal, min,
  max) survive but real dates do not.

The mapping lives only in memory for the duration of a run; rules returned by the LLM refer to
column names and are verified locally on the real data.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
from random import Random
from typing import Any, Dict, List, Tuple

from .common import key

IDENTIFIERS = {"src.Matricule": "id", "src.NomFamille": "surname", "src.PrénomUsuel": "given"}

ALLOWED = set(IDENTIFIERS) | {
    "src.DateEmbaucheRécente", "src.TypeAffectation", "src.DateEntréePoste", "src.DateSortiePoste",
    "src.CodeEmploi", "src.IntituléEmploi", "src.ÉchelleSalariale", "src.LibelléÉchelleSalariale",
    "src.CodeImputation", "src.CodeDirection", "src.LibelléDirection", "src.CodeSite", "src.LibelléSite",
    "src.CatégorieEmploi", "src.EstPermanent", "src.EstTempsPlein",
    "src.HeuresNormeHebdo", "src.HeuresNormeQuotidienne",
    "poste.IdentifiantEmploi", "poste.CodeDirectionAffectée", "poste.CodeBudget", "poste.IndicateurGestion",
    "poste.HeuresSemaineContrat", "poste.HeuresJourContrat", "poste.JoursTravailléesSemaine",
    "poste.min_eff", "poste.max_eff", "poste.date_chg_unite", "poste.fin_unite",
}

SURNAMES = ["Tremblay", "Gagnon", "Roy", "Côté", "Bouchard", "Gauthier", "Morin", "Lavoie", "Fortin", "Gagné",
            "Ouellet", "Pelletier", "Bélanger", "Lévesque", "Bergeron", "Leblanc", "Paquette", "Girard",
            "Simard", "Boucher", "Caron", "Beaulieu", "Cloutier", "Dubé", "Poirier", "Fournier"]
GIVEN = ["Marie", "Jean", "Sophie", "Luc", "Julie", "Marc", "Isabelle", "Louis", "Nathalie", "Pierre",
         "Chantal", "Alain", "Sylvie", "Michel", "Annie", "Denis", "Josée", "Éric", "Lucie", "Yves",
         "Karine", "Martin", "Mélanie", "Benoît", "Valérie", "Daniel"]
MAX_SHIFT_DAYS = 3650
DIGIT_RUN = re.compile(r"\d{5,}")


class Pseudonymizer:
    def __init__(self, seed: int = None):
        self.rng = Random(secrets.randbits(64) if seed is None else seed)
        self.map: Dict[str, str] = {}          # real string -> fake string
        self.digits: Dict[str, str] = {}       # real digit run -> fake digit run
        self.shift: Dict[int, int] = {}        # row index -> day offset
        self._used = set()
        self._pools = {"surname": self.rng.sample(SURNAMES, len(SURNAMES)), "given": self.rng.sample(GIVEN, len(GIVEN))}
        self._count = {"surname": 0, "given": 0}

    # -- identifiers
    def _fake(self, kind: str, real: str) -> str:
        if real in self.map:
            return self.map[real]
        pool = self._pools.get(kind)
        while True:
            if pool:
                n = self._count[kind]
                self._count[kind] += 1
                fake = pool[n % len(pool)] + (str(n // len(pool) + 1) if n >= len(pool) else "")
            else:
                fake = str(self.rng.randint(10 ** (len(real) - 1), 10 ** len(real) - 1)) if real.isdigit() else f"ID{self.rng.randint(1000, 9999)}"
            if fake not in self._used and fake != real:
                break
        self._used.add(fake)
        self.map[real] = fake
        return fake

    def _digits(self, run: str) -> str:
        if run not in self.digits:
            self.digits[run] = "".join(str(self.rng.randint(0, 9)) for _ in run)
        return self.digits[run]

    def scrub_text(self, s: str) -> str:
        """One pass: known identifiers → their fake, other long digit runs → random digits."""
        lower = {k.casefold(): v for k, v in self.map.items()}
        alts = "|".join(re.escape(k) for k in sorted(self.map, key=len, reverse=True))
        pattern = re.compile(f"({alts})|({DIGIT_RUN.pattern})" if alts else f"()({DIGIT_RUN.pattern})", re.IGNORECASE)
        return pattern.sub(lambda m: lower[m.group(1).casefold()] if m.group(1) else self._digits(m.group(2)), s)

    # -- dates
    def _shift(self, v: Any, row: int) -> Any:
        while not self.shift.get(row):  # never 0: a real date must not survive
            self.shift[row] = self.rng.randint(-MAX_SHIFT_DAYS, MAX_SHIFT_DAYS)
        return v + dt.timedelta(days=self.shift[row]) if isinstance(v, dt.date) else v

    # -- rows
    def row(self, features: Dict[str, Any], target: Any, i: int) -> Tuple[Dict[str, Any], Any]:
        out = {}
        for c, kind in IDENTIFIERS.items():
            v = key(features.get(c))
            if v is not None:
                out[c] = self._fake(kind, v)
        for c in sorted(ALLOWED - set(IDENTIFIERS)):
            if c in features:
                out[c] = self._shift(features[c], i)
        t = self._shift(target, i)
        if isinstance(t, str):
            t = self.scrub_text(t)
        return out, t

    def rows(self, X: List[dict], y: List[Any]) -> Tuple[List[dict], List[Any]]:
        # identifiers of every row first, so a target string can mention another row's identifier
        for x in X:
            for c, kind in IDENTIFIERS.items():
                v = key(x.get(c))
                if v is not None:
                    self._fake(kind, v)
        pairs = [self.row(x, t, i) for i, (x, t) in enumerate(zip(X, y))]
        return [p[0] for p in pairs], [p[1] for p in pairs]
