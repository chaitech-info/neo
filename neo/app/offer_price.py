"""Qiymət təklifi: sorğu mətnində yazılmış dəyişikliklər — agent bunları nəzərə almır, Neo tətbiq edir.

- qiymət: "qiyməti 1 nəfər üçün 8 yox 5 azn et" → 1 nəfərlik qiymət 5 AZN (`apply_price`: vahid qiymət, sətrin
  cəmi və yekunlar eyni nisbətdə dəyişir);
- menyudan çıxarmaq: "menyudan içkini çıxart", "desert olmasın", "lobya şorbasını çıxar" → həmin kateqoriya /
  yemək PDF-də göstərilmir (app/offer_pdf.py — menyu, "Gündəlik seçim", üz qabığındakı xülasə, şəkillər).

`requested_changes` mətni oxuyur (AI, alınmasa sadə qayda). Nəticə təklifdə saxlanılır
(offers.unit_price_override, offers.menu_exclude) — PDF tarixçədən açılanda da eyni qurulur.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import structlog

from app.dishes import CATEGORIES

log = structlog.get_logger()

MAX_PRICE = Decimal("1000")
MAX_DISHES = 20
TIMEOUT_S = 12.0
_NUM = r"(\d{1,4}(?:[.,]\d{1,2})?)"
_CUR = r"(?:azn|manat\w*|₼|man\.?)"
# Mətndə qiymətdən söhbət gedirmi
_PRICE_HINT_RE = re.compile(rf"qiym[əe]t|{_CUR}|price|цен|стоим", re.I)
# "8 yox 5 azn", "8 deyil 5 manat", "8 əvəzinə 5 azn" → 5
_INSTEAD_RE = re.compile(rf"{_NUM}\s*(?:{_CUR})?\s*(?:yox|deyil|əvəzinə|evezine|yerinə|yerine)\W+{_NUM}\s*{_CUR}", re.I)
# "qiymət(i) ... 5 azn et / olsun / qoy / yaz / elə"
_SET_RE = re.compile(rf"qiym[əe]t\w*[^.\n]{{0,60}}?{_NUM}\s*{_CUR}\s*(?:et|ele|elə|olsun|qoy|yaz|götür|gotur|hesabla)", re.I)

# Menyudan çıxarmaq: kateqoriyanın yazılışları (az, latın hərfləri ilə, ru, en)
_CATEGORY_WORDS = {
    "Şorba": r"[şs]orba\w*|суп\w*|soup\w*",
    "Əsas yemək": r"[əe]sas\s+yem[əe]\w*|основн\w+\s+блюд\w*|main\s+course\w*",
    "Qarnir": r"qarnir\w*|гарнир\w*|side\s+dish\w*",
    "Salat": r"salat\w*|салат\w*|salad\w*",
    "Desert": r"desert\w*|[şs]irniyyat\w*|десерт\w*|dessert\w*",
    "İçki": r"[iİ][çc]ki\w*|напит\w*|drink\w*|beverage\w*",
    "Çörək": r"[çc][öo]r[əe][kyğg]\w*|хлеб\w*|bread\w*",
}
_REMOVE_VERB = (r"[çc][ıi]xart?\w*|[çc][ıi]xar\w*|sil\w*|olmas[ıi]n|laz[ıi]m\s+deyil|ist[əe]mir\w*|yox\s+olsun|"
                r"убер\w*|убрать|без|исключ\w*|remove|without|exclude|no")
_REMOVE_HINT_RE = re.compile(_REMOVE_VERB, re.I)

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["unit_price", "remove_categories", "remove_dishes"],
    "properties": {
        "unit_price": {"type": ["number", "null"]},
        "remove_categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}},
        "remove_dishes": {"type": "array", "items": {"type": "string"}},
    },
}
PROMPT = """\
You read a salesperson's request for a catering price offer (Azerbaijani, often typed without diacritics;
sometimes Russian or English). Extract the changes the request explicitly asks to make to the offer.

unit_price — the price PER PERSON (per portion) in AZN that the request asks to USE:
"qiyməti 1 nəfər üçün 8 yox 5 azn et" -> 5, "bir nəfərə 6.50 manat olsun" -> 6.5, "цена 7 манат за человека" -> 7.
If several are given, the latest instruction wins. null when no unit price is requested: a total budget
("büdcə 3000 azn"), a number of people or days, a percent discount, a question about the price, or a price
that is only mentioned as the current/old one.

remove_categories — menu categories the request asks to REMOVE from the menu, from this list only:
Şorba (soup), Əsas yemək (main course), Qarnir (side dish), Salat (salad), Desert (dessert), İçki (drinks),
Çörək (bread). "menudan ickini cixart" -> ["İçki"], "desert və çörək olmasın" -> ["Desert", "Çörək"].
remove_dishes — single dishes the request asks to remove, in dictionary form with Azerbaijani letters
restored: "lobya şorbasını çıxar" -> ["lobya şorbası"], "menudan mercimek sorbasini cixart" ->
["mərcimək şorbası"], "toyuq tabaka olmasin" -> ["toyuq tabaka"]. A named dish is NOT its category:
"mercimek sorbasini cixart" removes one soup, so remove_categories stays [] (only "şorbanı çıxart" /
"şorba olmasın" removes the whole Şorba category). Do not list a whole category here.

Return [] for both when nothing is to be removed. Requests to ADD or replace dishes are not removals.
"""


@dataclass(frozen=True)
class Changes:
    price: Decimal | None = None            # 1 nəfərlik qiymət (AZN)
    categories: tuple[str, ...] = ()        # menyudan çıxarılan kateqoriyalar (app/dishes.CATEGORIES)
    dishes: tuple[str, ...] = ()            # menyudan çıxarılan yeməklər (yazıldığı kimi)

    def __bool__(self) -> bool:
        return self.price is not None or bool(self.categories or self.dishes)

    @property
    def exclude(self) -> dict | None:
        """PDF üçün: {"categories": [...], "dishes": [...]}; heç nə çıxarılmırsa None."""
        if not (self.categories or self.dishes):
            return None
        return {"categories": list(self.categories), "dishes": list(self.dishes)}


def dump_exclude(exclude: dict | None) -> str | None:
    return json.dumps(exclude, ensure_ascii=False) if exclude else None


def load_exclude(value: str | None) -> dict | None:
    """Bazadakı offers.menu_exclude → {"categories": [...], "dishes": [...]} (xarab / boşdursa None)."""
    if not value:
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out = {"categories": [c for c in data.get("categories") or [] if c in CATEGORIES],
           "dishes": [str(d)[:200] for d in data.get("dishes") or [] if isinstance(d, str) and d.strip()][:MAX_DISHES]}
    return out if out["categories"] or out["dishes"] else None


def _decimal(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d <= 0 or d > MAX_PRICE:
        return None
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _price_by_rule(text: str) -> Decimal | None:
    """Aydın yazılışlar üçün sadə qayda (AI olmayanda / cavab verməyəndə). Şübhəli hallarda None."""
    found = None
    for m in _INSTEAD_RE.finditer(text):
        found = m.group(2)
    if found is None:
        for m in _SET_RE.finditer(text):
            found = m.group(1)
    return _decimal(found) if found else None


# Konkret yemək: "<ad> şorbasını / salatını / kompotunu … çıxart" — mənsubiyyət şəkilçili söz kateqoriya deyil,
# yeməyin adının ikinci sözüdür ("mercimek sorbasini cixart" bütün şorbaları yox, bir şorbanı çıxarır).
_DISH_RE = re.compile(
    rf"(\w{{3,}})\s+((?:[şs]orbas[ıi]|salat[ıi]|kompotu|plovu|p[üu]resi|sotesi|dolmas[ıi]|kabab[ıi]|kotleti|sousu)\w*)"
    rf"\W+(?:\w+\W+){{0,1}}?(?:{_REMOVE_VERB})", re.I)


def _dishes_by_rule(text: str) -> tuple[str, ...]:
    skip = {"menyudan", "menudan", "menyuda", "menuda"}
    return tuple(dict.fromkeys(f"{m.group(1)} {m.group(2)}" for m in _DISH_RE.finditer(text)
                               if m.group(1).lower() not in skip))


def _categories_by_rule(text: str) -> tuple[str, ...]:
    """"içkini çıxart", "desert olmasın", "без напитков" — kateqoriya sözü ilə çıxarma feli yanaşıdır."""
    out = []
    text = _DISH_RE.sub(lambda m: " " if m.group(1).lower() not in ("menyudan", "menudan", "menyuda", "menuda") else m.group(0), text)
    for category, words in _CATEGORY_WORDS.items():
        near = rf"(?:(?:{words})\W+(?:\w+\W+){{0,2}}?(?:{_REMOVE_VERB})\b|\b(?:{_REMOVE_VERB})\W+(?:\w+\W+){{0,1}}?(?:{words}))"
        if re.search(near, text, re.I):
            out.append(category)
    return tuple(out)


def _by_ai(text: str) -> tuple[bool, dict | None]:
    """(cavab alındı?, {"unit_price", "remove_categories", "remove_dishes"}). Açar yoxdursa və ya sorğu
    alınmasa (False, None) — çağıran sadə qaydaya keçir."""
    if not os.environ.get("OPENAI_API_KEY"):
        return False, None
    try:
        from openai import OpenAI

        from app.ai_scoring import DEFAULT_MODEL, chat_json

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=TIMEOUT_S)
        model = os.environ.get("OFFER_PRICE_MODEL") or os.environ.get("AI_ANALYSIS_MODEL") or DEFAULT_MODEL
        out, _ = chat_json(client, model, PROMPT, text[:4000], SCHEMA, "changes", max_tokens=200)
    except Exception as exc:  # noqa: BLE001 — təklif bu düzəlişlər olmadan da yaranmalıdır
        log.warning("offer_changes_ai_failed", error=type(exc).__name__)
        return False, None
    return (True, out) if isinstance(out, dict) else (False, None)


def requested_changes(text: str) -> Changes:
    """Sorğu mətnində istənən qiymət və menyudan çıxarılacaqlar; heç nə istənməyibsə boş `Changes`."""
    text = (text or "").strip()
    wants_price = bool(text and re.search(r"\d", text) and _PRICE_HINT_RE.search(text))
    wants_removal = bool(text and _REMOVE_HINT_RE.search(text))
    if not (wants_price or wants_removal):
        return Changes()
    answered, out = _by_ai(text)
    if not answered:
        return Changes(price=_price_by_rule(text) if wants_price else None,
                       categories=_categories_by_rule(text) if wants_removal else (),
                       dishes=_dishes_by_rule(text) if wants_removal else ())
    price = out.get("unit_price")
    categories = tuple(c for c in CATEGORIES if c in (out.get("remove_categories") or []))
    dishes = tuple(dict.fromkeys(" ".join(str(d).split())[:200] for d in out.get("remove_dishes") or []
                                 if isinstance(d, str) and len(d.strip()) >= 3))[:MAX_DISHES]
    return Changes(price=_decimal(price) if price is not None else None, categories=categories, dishes=dishes)


def requested_price(text: str) -> Decimal | None:
    """Sorğu mətnində istənən 1 nəfərlik qiymət (AZN); yoxdursa None."""
    return requested_changes(text).price


def apply_price(snapshot: dict, price: Decimal | None) -> dict:
    """Təklifin surəti: tək paketin vahid qiyməti `price` olur; sətrin cəmi və yekunlar yeni qiymətdən yenidən
    hesablanır: qiymət × miqdar × gün (miqdar və ya gün bilinmirsə — 1 nəfərlik qiymətin özü), ƏDV agentin
    faizi ilə. Agentin cəmini nisbətlə dəyişmək olmur: "8 yox 5 azn" sorğusunda agent bəzən 5-i miqdar kimi
    oxuyub 8 × 5 = 40 qaytarır və nisbətlə 25 çıxırdı. Bir neçə paketli təklifdə qiymətin hansına aid olduğu
    bilinmir — toxunulmur."""
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
    quantity, days = _number(line.get("quantity")), _number(line.get("days"))
    net = price * quantity * days if quantity is not None and days is not None else price
    if line.get("line_total") not in (None, ""):
        new_line["line_total"] = _money(net)
    totals = snapshot.get("totals")
    if isinstance(totals, dict):
        new = dict(totals)
        old_net = _number(totals.get("subtotal"))
        vat = _number(totals.get("vat"))
        if old_net is not None and old_net > 0:
            new["subtotal"] = _money(net)
            new_vat = net * vat / old_net if vat is not None else None
            if new_vat is not None:
                new["vat"] = _money(new_vat)
            if totals.get("total") not in (None, ""):
                # ƏDV ayrıca göstərilibsə cəmi = ƏDV-siz + ƏDV; yoxsa cəmi ilə ƏDV-siz arasındakı nisbət saxlanılır
                total = _number(totals.get("total"))
                new["total"] = _money(net + new_vat if new_vat is not None else
                                      net * total / old_net if total is not None else net)
        elif totals.get("total") not in (None, ""):
            new["total"] = _money(net)      # yalnız yekun var (ƏDV qiymətin içindədir)
        out["totals"] = new
    return out


def _number(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
