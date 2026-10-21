"""Validasiyada hər istifadəçinin öz süzgəci və kateqoriyaları.

- Şəxsi A/B/C süzgəci (`users.lead_filters`): hər kateqoriyada hansı lead növlərini görür —
  məs. C-də yalnız xəbərlər. Süzgəc qoyulmayan kateqoriyada hamısı görünür.
- Öz kateqoriyaları (`user_categories`): ad + A/B/C seçimi + lead növləri + açar sözlər; Validasiyada ayrıca tab.
"""
from __future__ import annotations

import json

from app import keywords as kw_store
from app.db.models import UserCategory

TIERS = ("A", "B", "C")
KINDS = ("tender", "employer", "news")
MAX_CATEGORIES = 20


def parse_filters(raw: str | None) -> dict[str, list[str]]:
    """{"A": [...], ...} — yalnız tanınan kateqoriya və növlər; pozuq JSON → süzgəc yoxdur."""
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for tier in TIERS:
        kinds = data.get(tier)
        if isinstance(kinds, list):
            clean = [k for k in KINDS if k in kinds]
            if clean and len(clean) < len(KINDS):  # hamısı seçilibsə — süzgəc deyil
                out[tier] = clean
    return out


def dump_filters(filters: dict[str, list[str]]) -> str | None:
    clean = parse_filters(json.dumps(filters))
    return json.dumps(clean, sort_keys=True) if clean else None


def passes(filters: dict[str, list[str]], tier: str | None, kind: str) -> bool:
    allowed = filters.get(tier or "")
    return allowed is None or kind in allowed


def category_tiers(cat: UserCategory) -> list[str]:
    return [t for t in TIERS if t in (cat.tiers or "")]


def category_kinds(cat: UserCategory) -> list[str]:
    return [k for k in KINDS if k in (cat.kinds or "").split(",")]


def category_terms(cat: UserCategory) -> list[kw_store.Term]:
    return [kw_store.Term(t.strip()) for t in (cat.terms or "").splitlines() if t.strip()]


def in_category(cat: UserCategory, lead, kind: str) -> bool:
    tiers, kinds, terms = category_tiers(cat), category_kinds(cat), category_terms(cat)
    if tiers and lead.tier not in tiers:
        return False
    if kinds and kind not in kinds:
        return False
    if terms and not kw_store.matched_terms(f"{lead.title}\n{lead.raw_text}", terms):
        return False
    return True
