"""Müştəriyə ilk müraciət: AI ilə yazılan email (insan yoxlayıb göndərir, app/outreach/mail.py)
və LinkedIn postuna avtomatik qısa şərh (app/outreach/linkedin_comment.py).

Hər göndəriş `outreach` cədvəlinə yazılır (uğurlu da, uğursuz da) — fürsət səhifəsində tarixçə.
"""
from __future__ import annotations

import os
import re

LANGUAGES = {"az": "Azərbaycan", "ru": "Rus", "en": "İngilis"}
LANGUAGE_NAMES_EN = {"az": "Azerbaijani", "ru": "Russian", "en": "English"}

_CYRILLIC = re.compile(r"[а-яё]", re.I)
# re.I olmadan: case-insensitive rejimdə "ı" adi "i" ilə uyğun gəlir (hər ingilis mətni "az" olurdu).
_AZ_LETTERS = re.compile(r"[əğıöşüçƏĞÖŞÜÇİ]")
_LATIN = re.compile(r"[a-z]", re.I)


class OutreachError(Exception):
    """İstifadəçiyə göstərilən xəta (konfiqurasiya yoxdur, göndərilmədi və s.)."""


def detect_language(text: str) -> str:
    """Lead mətninin dili: rus (kiril üstündür), Azərbaycan (ə, ğ, ı… var), əks halda ingilis."""
    sample = (text or "")[:3000]
    cyr, latin = len(_CYRILLIC.findall(sample)), len(_LATIN.findall(sample))
    if cyr > latin:
        return "ru"
    if _AZ_LETTERS.search(sample) or not latin:
        return "az"
    return "en"


def company() -> dict[str, str]:
    """Bizim şirkət — email imzası və LinkedIn şərhi üçün (.env / Vercel / Modal secret)."""
    return {
        "name": os.environ.get("COMPANY_NAME", "").strip(),
        "phone": os.environ.get("COMPANY_PHONE", "").strip(),
        "signature": os.environ.get("COMPANY_SIGNATURE", "").replace("\\n", "\n").strip(),
    }


def company_description() -> str:
    """Nə satırıq — maraq sahələrindən (paneldə redaktə olunur), baza əlçatan deyilsə koddakı mətn."""
    from app.ai_filter import DEFAULT_COMPANY
    from app.keywords import company_profile

    try:
        return company_profile() or DEFAULT_COMPANY
    except Exception:
        return DEFAULT_COMPANY
