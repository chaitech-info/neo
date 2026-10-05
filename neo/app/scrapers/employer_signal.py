"""İşçilərinə nahar verən işəgötürən siqnalı — LinkedIn və boss.az-ın ortaq məntiqi.

Vakansiya elanında "Nahar şirkət tərəfindən təmin olunur" — işəgötürən artıq
hər gün işçilərini yedizdirir, yəni yemək alır/təşkil edir: təchizatçı dəyişməyə
potensial müştəri. Bax docs/knowledge-base/04-decisions.md → "İşçilərinə nahar
verən şirkətlər".
"""
from __future__ import annotations

import re
from urllib.parse import quote

from app.scrapers.base import fold

EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")

# Yemək imtiyazının aşkarlanması (fold() olunmuş mətndə: "ı" → "i"). 2026-09-27
# boss.az testində köhnə tək regex 49-u tutub ~15-i buraxırdı: "şirkət tərəfindən
# pulsuz nahar" (sıra tərs), "günorta yeməyi" ("yemək" deyil), "nahar verilir",
# "şirkət hesabına", "qidalanma kartı", "əlavə imkanlar: nahar", rusca
# "обед за счёт компании". Bax docs/knowledge-base/04-decisions.md.
_MEAL_WORD = re.compile(r"nahar|yemə[kyg]\w*|qidalanma|обед|питани|lunch|meals?\b")
# Konkret təminatçı — yemək sözündən əvvəl də, sonra da ola bilər.
_PROVIDER_ANY_SIDE = re.compile(
    r"şirkət tərəfindən|şirkətin hesabina|şirkət hesabina|işəgötürən tərəfindən"
    r"|işəgötürənin hesabina|за сч[её]т компании|за сч[её]т работодател|provided by the company"
)
# Ümumi fellər ("təmin", "verilir") yalnız yemək sözündən SONRA sayılır — əks halda
# "xidmətin təmin edilməsi, yemək və içkilərin..." (ofisiant vəzifəsi) uyğun gəlirdi.
_PROVIDER_AFTER = re.compile(r"təmin|verilir|qarşila|pulsuz|ödənişsiz|бесплатн|free|provided")
_BENEFIT = re.compile(
    r"kart|pulu|bonus|imkan|üstünlük|təminat|sosial paket|kompensasiya|gündə \d+ dəfə"
)
_NEGATION = re.compile(r"verilmir|təmin olunmur|təmin edilmir|ödənilmir|не предоставля|not provided")
# "nahar fasiləsi", "nahar vaxti", "nahar: 13:00-14:00" — iş qrafikidir, imtiyaz deyil.
_SCHEDULE_AFTER = re.compile(r"\s*(fasil|vaxt|saat|arasi|:?\s*\(?\d{1,2}[:.]\d{2})")
# Aşpaz/ofisiant vəzifə təsviri: "yeməklərin hazirlanmasi", "menyuya uyğun".
_COOKING = re.compile(r"hazirlan|bişir|menyu|resept|servis standart")

MEAL_WINDOW = 60

FREE_EMAIL_DOMAINS = {
    "gmail.com", "mail.ru", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "yandex.ru", "yandex.com", "inbox.ru", "bk.ru", "list.ru", "rambler.ru",
    "mail.com", "usa.com", "gmx.com", "aol.com", "live.com", "msn.com", "ymail.com",
    "proton.me", "protonmail.com",
}

# İş elanı saytları və işə qəbul agentlikləri — həm müəllif adında, həm email
# domenində yoxlanır. Agentlik (humanpro.az) gizli müştəri üçün elan verir,
# onun domeni işəgötürən deyil.
JOB_BOARDS = ("azvak", "jobsearch", "vakansiya", "hellojob", "boss.az", "azjob", "humanpro")

# Eyni məhsulu satan rəqiblər (canlı testlərdə tapılıb). Yeni rəqib görsəniz
# bura əlavə edin (kiçik hərflə).
KNOWN_COMPETITORS = (
    "plan&eat", "plan & eat", "planandeat", "doydum", "catering 145",
    "lunchbox.az", "tais ikf", "atsfood", "sağlam catering",
)

# İşəgötürənin adı bunlardan birini ehtiva edirsə, işçisini öz mətbəxində
# yedizdirir — alıcı deyil (boss.az testində: Pizza Mizza, Supreme Hotel, EGUN Cafe).
_OWN_KITCHEN_RE = re.compile(
    r"\b(catering|keytering|keytrinq|iaşə|hotel|otel|hostel|restoran|restaurant"
    r"|cafe|kafe|coffee|pizza|burger|bistro|lounge|şaurma|doner|dönər)|\.rest\b"
)


# "Pulsuz nahar", "ödənişsiz yemək" — imtiyaz sözü yemək sözündən ƏVVƏL (2026-10-05,
# jobsearch.az: _PROVIDER_AFTER yalnız sonranı yoxlayırdı).
_FREE_MEAL = re.compile(r"(pulsuz|ödənişsiz|бесплатн\w*|free)\s+(nahar|yemə[kyg]\w*|qidalanma|обед\w*|питани\w*|lunch|meals?)")


def mentions_company_meal(text: str) -> bool:
    """Elan işçilərə yeməyin şirkət tərəfindən verildiyini deyirmi?"""
    t = fold(text)
    for m in _FREE_MEAL.finditer(t):
        window = t[max(0, m.start() - MEAL_WINDOW):m.end() + MEAL_WINDOW]
        if not _NEGATION.search(window) and not _COOKING.search(t[m.end():m.end() + 30]):
            return True
    for m in _MEAL_WORD.finditer(t):
        before = t[max(0, m.start() - MEAL_WINDOW):m.start()]
        after = t[m.end():m.end() + MEAL_WINDOW]
        if _SCHEDULE_AFTER.match(after):
            continue
        window = before + m.group(0) + after
        # Aşpaz təsviri yalnız yemək sözünün lap yanında yoxlanır ("yeməklərin
        # hazırlanması") — geniş pəncərədə logistika elanının "yüklərin
        # hazırlanması" sözü LOADSPHERE-in real nahar imtiyazını silirdi.
        if _NEGATION.search(window) or _COOKING.search(before[-20:] + m.group(0) + after[:30]):
            continue
        if _PROVIDER_ANY_SIDE.search(window) or _PROVIDER_AFTER.search(after) or _BENEFIT.search(window):
            return True
    return False


def is_competitor(*texts: str) -> bool:
    folded = fold(" ".join(texts))
    return any(fold(c) in folded for c in KNOWN_COMPETITORS)


def has_own_kitchen(employer_name: str) -> bool:
    return _OWN_KITCHEN_RE.search(fold(employer_name)) is not None


def is_job_board(text: str) -> bool:
    folded = fold(text)
    return any(j in folded for j in JOB_BOARDS)


def corporate_domain(text: str) -> str | None:
    """Mətndəki ilk korporativ email domeni (pulsuz poçt və iş saytları sayılmır)."""
    for m in EMAIL_RE.finditer(text):
        domain = m.group(1).lower().rstrip(".")
        if domain not in FREE_EMAIL_DOMAINS and not is_job_board(domain):
            return domain
    return None


def employer_url(domain: str | None, name: str) -> str:
    """Şirkətin sabit URL-i — dedup açarı. Bütün mənbələr eyni funksiyanı
    işlətdiyi üçün LinkedIn və boss.az-dan gələn eyni şirkət bir lead olur."""
    if domain:
        return f"https://{domain}"
    return f"https://www.linkedin.com/search/results/companies/?keywords={quote(name)}"


SIGNAL_PREFIX = (
    "[SİQNAL: işəgötürən vakansiya elanında işçilərinə nahar/yemək verdiyini "
    "yazır — şirkət artıq hər gün yemək təşkil edir]"
)
