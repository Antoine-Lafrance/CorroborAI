"""Value normalisation shared by every rule (layer 1 of the pipeline)."""
from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from typing import Any, Optional

import pandas as pd


def is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if v is pd.NaT:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def clean(v: Any) -> Any:
    """Empty-like values (NaN, NaT, '', None) -> None."""
    return None if is_empty(v) else v


def to_date(v: Any) -> Any:
    """Accepts datetime, Timestamp, 'YYYY-MM-DD' or ISO 'YYYY-MM-DDT00:00:00.000Z'."""
    v = clean(v)
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return pd.Timestamp(str(v)[:10]).date()
    except (ValueError, TypeError):
        return str(v)  # unparsable: kept as text so it never compares equal to a real date


def to_num(v: Any) -> Optional[float]:
    v = clean(v)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_str(v: Any) -> Optional[str]:
    v = clean(v)
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def fix_mojibake(s: Optional[str]) -> Optional[str]:
    """Repairs UTF-8 text that was decoded as Latin-1 ('complÃ¨te' -> 'complète')."""
    if s is None:
        return None
    try:
        repaired = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    return repaired


def has_mojibake(s: Optional[str]) -> bool:
    return s is not None and fix_mojibake(s) != s


def to_bool(v: Any) -> Optional[bool]:
    v = clean(v)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"oui", "true", "1", "o", "y", "yes", "vrai"}


ENV_PREFIX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*_", re.IGNORECASE)


def split_env_prefix(email: str):
    """'dev-08-v2_PNom123@x' -> ('dev-08-v2_', 'PNom123@x')."""
    m = ENV_PREFIX.match(email)
    return (m.group(0), email[m.end():]) if m else ("", email)


def same(a: Any, b: Any) -> bool:
    """Equality after generic normalisation (types, empties, numeric formats)."""
    a, b = clean(a), clean(b)
    if a is None or b is None:
        return a is None and b is None
    na, nb = to_num(a), to_num(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 1e-9
    return to_str(a) == to_str(b)
