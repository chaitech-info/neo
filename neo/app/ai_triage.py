"""Başlıq triajı: açar sözlərin görmədiyi fürsətləri tutmaq üçün ikinci süzgəc.

Siyahı səhifəsindəki BÜTÜN başlıqlar partiyalarla AI-yə göstərilir; hər başlıq
üçün 1/0 cavab alınır (model hər birini ayrıca nəzərdən keçirməyə məcburdur —
nömrə siyahısı istəyəndə gpt-4o-mini həm artıq seçir, həm də buraxırdı).
"1" deyilənlər açar söz uyğunluğu olmasa da tam qiymətləndirməyə
(app/ai_filter.py) keçir. Qərar "şübhədə daxil et": buraxılmış fürsət səhv
daxil edilmiş başlıqdan bahadır, növbəti addımda AI mətni oxuyub rədd edə bilər.

AI əlçatan deyilsə triage() None qaytarır — scraper yalnız açar sözlərlə davam
edir. Cavab pozuqdursa (say uyğun gəlmir) həmin partiyanın hamısı daxil edilir.
Bax docs/knowledge-base/04-decisions.md → "Açar söz + AI triaj".
"""
from __future__ import annotations

import hashlib
import json
import os

import structlog
from openai import OpenAI

from app.ai_filter import OPENAI_MAX_RETRIES

log = structlog.get_logger()

BATCH_SIZE = 40
CACHE_MAX = 20000

_DEFAULT_COMPANY = """\
Sən Azərbaycandakı bir catering şirkəti üçün satış fürsətləri axtarırsan.
Şirkət satır: şirkət işçiləri üçün gündəlik hazır yemək (paket/porsiya nahar,
toplu çatdırılma); müəssisələrin qidalanma xidməti; tədbir, banket, konfrans,
qəbul, bayram yeməkləri; kofe-brek, bufet, fürşet.
"""

_TENDER_RULES = """\
Sənə nömrələnmiş TENDER/SATINALMA başlıqları veriləcək (alıcı təşkilat ilə).
Başlıq "1"-dir, əgər alıcı bu alış çərçivəsində yemək ala bilər:
- yemək, qidalanma, iaşə, catering, nahar, bufet, ərzaq/qida məhsulları, içki;
- tədbir, konfrans, forum, festival, yarış/turnir, mərasim, bayram, yubiley,
  komandaqurma, təlim/seminar, qəbul, sərgi TƏŞKİLİ və ya keçirilməsi;
- qonaqların/iştirakçıların qəbulu, yerləşdirilməsi, qonaqlama;
- düşərgə, yataqxana, məktəb, uşaq bağçası, xəstəxana, hərbi hissənin QİDALANMASI.
"0": tikinti, təmir, abadlıq, yol, bina quraşdırılması (işçi olsa belə — alıcı
yemək almır), avadanlıq, mebel, nəqliyyat, ehtiyat hissəsi, İT/proqram, çap,
mühafizə, təmizlik, dərman/tibb vasitəsi, xəstəliklə "mübarizə tədbirləri".
Şübhə edəndə "1".
"""

_NEWS_RULES = """\
Sənə nömrələnmiş XƏBƏR/ELAN başlıqları veriləcək.
Başlıq "1"-dir, əgər satış komandası bu xəbərə görə kiməsə zəng edə bilər:
- konkret şirkət/qurum yemək, catering, iaşə xidməti axtarır və ya alır;
- Azərbaycanda yeni zavod, fabrik, mədən, istehsal müəssisəsi, sənaye parkı,
  logistika mərkəzi, ofis, filial açılır, tikilir, genişlənir, istehsala başlayır;
  yeni iş yerləri, kütləvi işə qəbul;
- GƏLƏCƏKDƏ keçiriləcək korporativ tədbir, konfrans, festival, banket.
"0": keçmiş tədbirin reportajı, dövlət rəsmilərinin görüşləri, siyasət, idman
nəticələri, cinayət, hava, valyuta/birja, ümumi statistika, xarici ölkə xəbəri.
Şübhə edəndə "1".
"""

_FORMAT = 'Yalnız JSON: {"r": [hər başlıq üçün sıra ilə 1 və ya 0]} — massivdə dəqiq N element.'

_RULES = {"tender": _TENDER_RULES, "news": _NEWS_RULES}


def _company() -> str:
    from app.keywords import company_profile  # gec import: dövri asılılıq olmasın

    profile = company_profile()
    return (_DEFAULT_COMPANY.split("\n")[0] + "\n" + profile + "\n") if profile else _DEFAULT_COMPANY


def prompt(kind: str) -> str:
    """Triaj promptu — şirkət profili bazadakı maraq sahələrindən (app/keywords.py)."""
    return _company() + "\n" + _RULES[kind] + "\n" + _FORMAT

_cache: dict[tuple[str, str, str], bool] = {}


def _client() -> OpenAI:
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), max_retries=OPENAI_MAX_RETRIES)


def _model() -> str:
    return os.environ.get("AI_MODEL") or "gpt-4o-mini"


def _ask(client: OpenAI, kind: str, system: str, titles: list[str]) -> list[bool]:
    # Model bəzən 40 başlığa 39 cavab qaytarır — bir dəfə təkrar soruşulur, yenə
    # pozuqdursa partiyanın hamısı daxil edilir (xəbərdə bu hər başlıq üçün
    # məqalə yükləmək deməkdir, ona görə əvvəlcə təkrar).
    answers = _ask_once(client, kind, system, titles)
    if answers is None:
        answers = _ask_once(client, kind, system, titles)
    if answers is None:
        log.warning("ai_triage_bad_batch", kind=kind, size=len(titles))
        return [True] * len(titles)
    return answers


def _ask_once(client: OpenAI, kind: str, system: str, titles: list[str]) -> list[bool] | None:
    numbered = f"N = {len(titles)}\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    resp = client.chat.completions.create(
        model=_model(),
        temperature=0,
        max_tokens=600,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": numbered},
        ],
    )
    answers = json.loads(resp.choices[0].message.content).get("r", [])
    if len(answers) != len(titles):
        log.info("ai_triage_count_mismatch", kind=kind, expected=len(titles), got=len(answers))
        return None
    return [str(a).strip() in ("1", "true", "True") for a in answers]


def triage(titles: list[str], kind: str = "news", client: OpenAI | None = None) -> set[int] | None:
    """Fürsət ola biləcək başlıqların indeksləri; AI işləmirsə None.

    Nəticələr (növ, prompt, başlıq) üzrə keşlənir — saatlıq işləyən xəbər
    scraper-ləri eyni başlıqları təkrar göndərmir; paneldə maraq sahələri
    dəyişəndə prompt dəyişir və köhnə qərarlar avtomatik keçərsiz olur.
    """
    system = prompt(kind)
    version = hashlib.sha1(system.encode("utf-8")).hexdigest()[:12]
    todo = sorted({t for t in titles if t and (kind, version, t) not in _cache})
    if todo:
        try:
            client = client or _client()
            for start in range(0, len(todo), BATCH_SIZE):
                batch = todo[start:start + BATCH_SIZE]
                for title, fit in zip(batch, _ask(client, kind, system, batch)):
                    _cache[(kind, version, title)] = fit
        except Exception:
            log.exception("ai_triage_failed", kind=kind, titles=len(todo))
            return None
    result = {i for i, t in enumerate(titles) if t and _cache.get((kind, version, t))}
    if len(_cache) > CACHE_MAX:
        _cache.clear()
    return result
