"""Başlıq triajı: açar sözlərin görmədiyi fürsətləri tutmaq üçün ikinci süzgəc.

Siyahı səhifəsindəki BÜTÜN başlıqlar partiyalarla AI-yə göstərilir; hər başlıq
üçün 1/0 cavab alınır (model hər birini ayrıca nəzərdən keçirməyə məcburdur —
nömrə siyahısı istəyəndə gpt-4o-mini həm artıq seçir, həm də buraxırdı).
"1" deyilənlər açar söz uyğunluğu olmasa da tam qiymətləndirməyə
(app/ai_filter.py) keçir. Qərar "şübhədə daxil et": buraxılmış fürsət səhv
daxil edilmiş başlıqdan bahadır, növbəti addımda AI mətni oxuyub rədd edə bilər.

AI əlçatan deyilsə triage() None qaytarır — scraper yalnız açar sözlərlə davam
edir. Cavab pozuqdursa (say uyğun gəlmir) partiya bölünüb yenidən soruşulur; yalnız
kiçik partiya yenə pozuqdursa o başlıqlar daxil edilir (keşlənmədən).
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

BATCH_SIZE = 20  # 40-lıq partiyada model sıranı itirirdi (2026-10-05 ölçüsü)
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
- yemək, qidalanma, iaşə, catering, nahar, bufet, ərzaq/qida məhsulları, içki,
  içməli su, çay/qəhvə;
- tədbir, konfrans, forum, festival, yarış, turnir, liqa, olimpiada, çempionat,
  mərasim, bayram, yubiley, komandaqurma, təlim/seminar, qəbul, sərgi —
  TƏŞKİLİ, KEÇİRİLMƏSİ, təşkili və keçirilməsi xidməti;
- qonaqların/iştirakçıların qəbulu, yerləşdirilməsi, qonaqlama, nümayəndə heyəti;
- düşərgə, yataqxana, məktəb, uşaq bağçası, xəstəxana, hərbi hissənin QİDALANMASI.
"0": tikinti, təmir, abadlıq, yol, bina quraşdırılması (işçi olsa belə — alıcı
yemək almır), avadanlıq, mebel, nəqliyyat, ehtiyat hissəsi, İT/proqram, çap,
mühafizə, təmizlik, dərman/tibb vasitəsi, xəstəliklə "mübarizə tədbirləri".
Şübhə edəndə "1".
"""

_NEWS_RULES = """\
Sənə nömrələnmiş XƏBƏR/ELAN başlıqları veriləcək.
Başlıq "1"-dir, əgər satış komandası bu xəbərə görə kiməsə zəng edə bilər:
- konkret şirkət/qurum yemək, catering, iaşə xidməti, ərzaq, içki, içməli su alır
  və ya axtarır;
- TENDER xəbəri və tender icmalı ("N yeni tender elan edib", "TENDER ELAN EDİR",
  "... alır – TENDER") — içində yemək/tədbir tenderi ola bilər;
- Azərbaycanda yeni zavod, fabrik, mədən/yataq, istehsal müəssisəsi, sənaye parkı,
  hab, logistika/tədqiqat mərkəzi, ofis, filial açılır, tikilir, yaradılır,
  genişlənir, istehsala başlayır; yeni iş yerləri, kütləvi işə qəbul;
- GƏLƏCƏKDƏ keçiriləcək korporativ/dövlət tədbiri, konfrans, festival, şou,
  banket — xüsusən "N milyon xərclənəcək" kimi büdcəsi açıqlananlar.
"0": keçmiş tədbirin reportajı, dövlət rəsmilərinin görüşləri, siyasət, idman
nəticələri, cinayət, hava, valyuta/birja, ümumi statistika, xarici ölkə xəbəri.
Şübhə edəndə "1".
"""

_FORMAT = ('Yalnız JSON: {"r": {"0": 1, "1": 0, ...}} — HƏR başlıq nömrəsi açar kimi, '
           'dəyəri 1 və ya 0; dəqiq N açar.')

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


DEFAULT_MODEL = "gpt-4.1-mini"  # 2026-10-05: gpt-4o-mini ilə eyni recall, iki dəfə az başlıq seçir


def _model() -> str:
    return os.environ.get("AI_TRIAGE_MODEL") or DEFAULT_MODEL


MIN_SPLIT = 5  # bundan kiçik pozuq partiya artıq bölünmür — hamısı daxil edilir


def _ask(client: OpenAI, kind: str, system: str, titles: list[str]) -> list[bool | None]:
    """Hər başlıq üçün True/False; None = AI cavab verə bilmədi ("şübhədə daxil et").

    Model bəzən 37 başlığa 36 cavab qaytarır (2026-10-05: oxu-da iki dəfə ardıcıl).
    Əvvəllər bütün partiya daxil edilirdi → 37 xəbərin hamısı tam AI qiymətləndirməsinə
    gedirdi. İndi bir dəfə təkrar, sonra partiya yarıya bölünür — kiçik partiyada
    model düzgün sayır. Yalnız ≤5 başlıqlıq partiya yenə pozuqdursa daxil edilir.
    """
    answers = _ask_once(client, kind, system, titles)
    if answers is None:
        answers = _ask_once(client, kind, system, titles)
    if answers is not None:
        return answers
    if len(titles) > MIN_SPLIT:
        mid = len(titles) // 2
        return _ask(client, kind, system, titles[:mid]) + _ask(client, kind, system, titles[mid:])
    log.warning("ai_triage_bad_batch", kind=kind, size=len(titles))
    return [None] * len(titles)


def _ask_once(client: OpenAI, kind: str, system: str, titles: list[str]) -> list[bool] | None:
    numbered = f"N = {len(titles)}\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    model = _model()
    kwargs = {"model": model, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": numbered}]}
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = "minimal"
    else:
        kwargs.update(temperature=0, max_tokens=900)
    resp = client.chat.completions.create(**kwargs)
    answers = json.loads(resp.choices[0].message.content).get("r", [])
    # Nömrə→cavab obyekti: mövqe səhvi olmur (massivdə model 40 elementin sırasını itirirdi).
    if isinstance(answers, dict):
        answers = [answers.get(str(i), answers.get(i)) for i in range(len(titles))]
        if any(a is None for a in answers):
            log.info("ai_triage_count_mismatch", kind=kind, expected=len(titles),
                     got=sum(a is not None for a in answers))
            return None
    elif len(answers) != len(titles):
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
    unsure: set[str] = set()
    if todo:
        try:
            client = client or _client()
            for start in range(0, len(todo), BATCH_SIZE):
                batch = todo[start:start + BATCH_SIZE]
                for title, fit in zip(batch, _ask(client, kind, system, batch)):
                    if fit is None:
                        unsure.add(title)   # daxil et, amma keşləmə — növbəti run yenə soruşsun
                    else:
                        _cache[(kind, version, title)] = fit
        except Exception:
            log.exception("ai_triage_failed", kind=kind, titles=len(todo))
            return None
    result = {i for i, t in enumerate(titles) if t and (t in unsure or _cache.get((kind, version, t)))}
    if len(_cache) > CACHE_MAX:
        _cache.clear()
    return result
