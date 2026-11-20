"""Qiymət təklifi: sorğu mətnində yazılmış qiymət ("qiyməti 1 nəfər üçün 8 yox 5 azn et").

Agent qiyməti həmişə paketdən götürür — sorğuda istənən qiyməti nəzərə almır. Burada sorğudan 1 nəfərlik
qiymət çıxarılır (`requested_price`: AI, alınmasa sadə qayda) və təklifə tətbiq olunur (`apply_price`:
vahid qiymət, sətrin cəmi və yekunlar eyni nisbətdə dəyişir). Qiymət təklifdə saxlanılır
(offers.unit_price_override) — PDF tarixçədən açılanda da həmin qiymətlə qurulur.
"""
from __future__ import annotations

import os
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import structlog

log = structlog.get_logger()

MAX_PRICE = Decimal("1000")
TIMEOUT_S = 12.0
_NUM = r"(\d{1,4}(?:[.,]\d{1,2})?)"
_CUR = r"(?:azn|manat\w*|₼|man\.?)"
# Mətndə qiymətdən söhbət gedirmi (yoxdursa AI çağırılmır)
_HINT_RE = re.compile(rf"qiym[əe]t|{_CUR}|price|цен|стоим", re.I)
# "8 yox 5 azn", "8 deyil 5 manat", "8 əvəzinə 5 azn" → 5
_INSTEAD_RE = re.compile(rf"{_NUM}\s*(?:{_CUR})?\s*(?:yox|deyil|əvəzinə|evezine|yerinə|yerine)\W+{_NUM}\s*{_CUR}", re.I)
# "qiymət(i) ... 5 azn et / olsun / qoy / yaz / elə"
_SET_RE = re.compile(rf"qiym[əe]t\w*[^.\n]{{0,60}}?{_NUM}\s*{_CUR}\s*(?:et|ele|elə|olsun|qoy|yaz|götür|gotur|hesabla)", re.I)

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["unit_price"],
    "properties": {"unit_price": {"type": ["number", "null"]}},
}
PROMPT = """\
You read a salesperson's request for a catering price offer (Azerbaijani, sometimes Russian or English).
Return the price PER PERSON (per portion) that the request explicitly asks to USE in the offer, in AZN.

Return a number only when the text clearly sets or changes the unit price, e.g.
"qiyməti 1 nəfər üçün 8 yox 5 azn et" -> 5, "bir nəfərə 6.50 manat olsun" -> 6.5, "цена 7 манат за человека" -> 7.
If several are given, the latest instruction wins. Return null when no unit price is requested: a total
budget ("büdcə 3000 azn"), a number of people or days, a discount in percent, a question about the price,
or a price that is only mentioned as the current/old one.
"""


def _decimal(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d <= 0 or d > MAX_PRICE:
        return None
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _by_rule(text: str) -> Decimal | None:
    """Aydın yazılışlar üçün sadə qayda (AI olmayanda / cavab verməyəndə). Şübhəli hallarda None."""
    found = None
    for m in _INSTEAD_RE.finditer(text):
        found = m.group(2)
    if found is None:
        for m in _SET_RE.finditer(text):
            found = m.group(1)
    return _decimal(found) if found else None


def _by_ai(text: str) -> tuple[bool, Decimal | None]:
    """(cavab alındı?, qiymət). Açar yoxdursa və ya sorğu alınmasa (False, None) — çağıran qaydaya keçir."""
    if not os.environ.get("OPENAI_API_KEY"):
        return False, None
    try:
        from openai import OpenAI

        from app.ai_scoring import DEFAULT_MODEL, chat_json

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=TIMEOUT_S)
        model = os.environ.get("OFFER_PRICE_MODEL") or os.environ.get("AI_ANALYSIS_MODEL") or DEFAULT_MODEL
        out, _ = chat_json(client, model, PROMPT, text[:4000], SCHEMA, "price", max_tokens=30)
    except Exception as exc:  # noqa: BLE001 — təklif qiymət düzəlişi olmadan da yaranmalıdır
        log.warning("offer_price_ai_failed", error=type(exc).__name__)
        return False, None
    value = out.get("unit_price") if isinstance(out, dict) else None
    return True, (_decimal(value) if value is not None else None)


def requested_price(text: str) -> Decimal | None:
    """Sorğu mətnində istənən 1 nəfərlik qiymət (AZN); yoxdursa None."""
    text = (text or "").strip()
    if not text or not re.search(r"\d", text) or not _HINT_RE.search(text):
        return None
    answered, price = _by_ai(text)
    return price if answered else _by_rule(text)


def _scaled(value: Any, factor: Decimal) -> Any:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    return str((d * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def apply_price(snapshot: dict, price: Decimal | None) -> dict:
    """Təklifin surəti: tək paketin vahid qiyməti `price` olur, sətrin cəmi və yekunlar (ƏDV-siz, ƏDV, cəmi)
    eyni nisbətdə dəyişir. Bir neçə paketli təklifdə hansına aid olduğu bilinmir — toxunulmur."""
    if price is None:
        return snapshot
    lines = snapshot.get("lines") or []
    real = [l for l in lines if isinstance(l, dict)]
    if len(real) != 1:
        return snapshot
    line = real[0]
    try:
        old = Decimal(str(line.get("unit_price")))
    except (InvalidOperation, ValueError):
        old = None
    new_line = {**line, "unit_price": str(price)}
    out = {**snapshot, "lines": [new_line if l is line else l for l in lines]}
    if old is None or old <= 0 or old == price:
        return out
    factor = price / old
    if line.get("line_total") not in (None, ""):
        new_line["line_total"] = _scaled(line["line_total"], factor)
    totals = snapshot.get("totals")
    if isinstance(totals, dict):
        out["totals"] = {k: (_scaled(v, factor) if k in ("subtotal", "vat", "total") and v not in (None, "") else v)
                         for k, v in totals.items()}
    return out
