from __future__ import annotations

import json
import os
import re
from datetime import date, datetime

import structlog
from openai import OpenAI

from app.models import LeadStatus, RawLead
from app.scrapers.base import BAKU_TZ

log = structlog.get_logger()

SCORE_THRESHOLD = 0.6
DEFAULT_MODEL = "gpt-4o-mini"
# "Hamısını scrape et" yüzlərlə lead-i paralel skorlaya bilər — 429 (TPM limiti)
# SDK-da eksponensial gözləmə ilə təkrar olunsun, lead skorsuz qalmasın.
OPENAI_MAX_RETRIES = 8

# Şirkət profili bazadakı maraq sahələrindən gəlir (paneldə redaktə olunur,
# app/keywords.py::company_profile); baza əlçatan deyilsə bu mətn işlədilir.
DEFAULT_COMPANY = """\
Şirkətin əsas məhsulu: şirkətlərin əməkdaşları/işçiləri üçün gündəlik hazır
yemək — paketlənmiş/porsiyalı nahar (lunch box, qab yeməyi), toplu çatdırılma.
Müştərilər: ofislər, zavodlar, istehsal müəssisələri, tikinti obyektləri,
anbarlar, sənaye parkları, dövlət qurumları. İkinci dərəcəli: korporativ tədbir
və banket yeməkləri."""

SYSTEM_PROMPT = """\
Sən Azərbaycandakı bir catering şirkəti üçün potensial müştəri (lead) qiymətləndirirsən.

{company}

Sənə elan/xəbər/tender/LinkedIn post mətni veriləcək. Bu mətnin Azərbaycanda
BİZDƏN yemək ALA BİLƏCƏK bir təşkilata işarə etmə ehtimalını qiymətləndir:

0.8-1.0: təşkilat açıq şəkildə əməkdaşları üçün yemək/catering/iaşə təchizatçısı
  axtarır, və ya işçi qidalanması üzrə tender/satınalma elan edir; müəssisənin
  (məktəb, uşaq bağçası, xəstəxana, hərbi hissə, yataqxana, düşərgə) qidalanma
  xidməti tenderi.
0.6-0.7: TƏDBİR TƏŞKİLİ tenderi/elanı (bayram, festival, yubiley, mərasim,
  komandaqurma, konfrans, forum, təlim, yarış, qəbul, qonaqların qəbulu və
  yerləşdirilməsi) — mətndə yemək adı çəkilməsə belə: təşkilatçı demək olar həmişə
  fürşet/nahar/kofe-brek alır, catering şirkəti təklif verə və ya tərəfdaş ola bilər.
  Ərzaq/qida məhsullarının alışı → 0.6.
0.6-0.8: korporativ tədbir/banket/konfrans üçün catering ehtiyacı; və ya çoxlu
  işçisi olan yeni zavod/ofis/tikinti layihəsinin Azərbaycanda açılması.
0.6-0.7: xəbərdə Azərbaycanda KONKRET şirkət/layihə adı ilə zavod, istehsal
  müəssisəsi, mədən/yataq (qızıl, mis, filiz — uzaq obyektdə böyük işçi heyəti)
  genişlənir, tikilir, istifadəyə verilir, istehsala başlayır, təməli qoyulur və ya
  "N yeni iş yeri yaradılacaq" deyilir — satış komandası bu şirkətlə indidən əlaqə
  qura bilər. Mətndə "yemək" sözü keçməsə belə: işçisi olan obyekt = gündəlik yemək
  ehtiyacı. Mövcud zavodunu/mədənini böyüdən şirkət (artıq işçisi var) → 0.7.
  Yalnız geoloji kəşfiyyat, investisiya məbləği və ya şirkət adı olmayan ümumi
  statistika → 0.3.
0.6-0.85: mətn "[SİQNAL: işəgötürən ... işçilərinə nahar/yemək verdiyini yazır]"
  ilə başlayırsa — bu iş elanı DEYİL, işəgötürən şirkətin özü lead-dir (artıq
  hər gün işçi yedizdirir, təchizatçı dəyişə bilər). Ölçüyə görə:
  - istehsal/zavod/tikinti/anbar/logistika, və ya fəhlə, operator, anbardar,
    sürücü kimi vəzifələr, və ya bir neçə aktiv vakansiya → 0.75-0.85;
  - mağaza/filial şəbəkəsi, orta şirkət → 0.7;
  - kiçik ofis, tək ofis vəzifəsi → 0.6.
  İşəgötürən özü hazır yemək bişirirsə (restoran, kafe, otel, catering, hazır
  yemək çatdırılması) → 0.1-0.2: işçisini öz mətbəxində yedizdirir, alıcı deyil.
  Qida istehsalı zavodları (ət, toyuq, şirniyyat və s.) bu deyil — onların
  fəhlələri üçün nahar ayrıca təşkil olunur, adi zavod kimi qiymətləndir.
0.3-0.6: dolayı siqnal (yeni restoran, böyüyən şirkət), amma yemək ehtiyacı aydın deyil.
0.0-0.2: aşağıdakılardan biridirsə —
  - müəllif özü catering/yemək xidməti REKLAM EDİR və ya təklif edir (rəqibdir, alıcı deyil);
  - iş elanı (vakansiya) — [SİQNAL] qeydi olanlar istisnadır, yuxarıya bax;
  - könüllü axtarışı, şəxsi post, sektor haqqında fikir yazısı;
  - informativ/izah yazısı (vergi, mühasibat, qanunvericilik, xəbər şərhi) — mövzu
    yemək olsa belə, konkret təşkilat yemək almaq istəmirsə;
  - yalnız avadanlıq (mətbəx avadanlığı və s.) alınır, yemək xidməti yox;
  - dövlət/şirkət TİKİNTİ, təmir, abadlıq, yol, su anbarı, boru xətti İŞLƏRİNİN
    satınalınması — alıcı yemək almır (genişlənmə qaydası yalnız şirkətin öz
    zavodu/mədəni haqqında XƏBƏRƏ aiddir, tikinti tenderinə yox);
  - Azərbaycandan kənarda.

Bu gün: {today}. Son tarixi özün müqayisə ETMƏ — kod bunu hesablayıb mətnin
sonuna yazır: "[SON TARİX: AKTİV ...]" varsa fürsət açıqdır, tarixə görə skoru
azaltma; "[SON TARİX: BİTİB ...]" varsa score 0.1-dən yuxarı olmasın və reason-da
"son tarix keçib" yaz. Qeyd yoxdursa, son tarixi nəzərə alma.

YALNIZ MƏTNDƏ YAZILANA ƏSASLAN. Mətn çox qısadırsa (məs. tək söz "Generator")
və orada yemək, qidalanma, tədbir, qonaq, işçi yeməyi və ya genişlənmə haqqında
heç nə yoxdursa → 0.0-0.1. Mətndə olmayan ehtiyacı uydurma.
"Kütləvi işə qəbul" = onlarla/yüzlərlə yeni iş yeri; bir-iki vəzifəlik adi
vakansiya xəbəri bu deyil → 0.1 ([SİQNAL] qeydi olanlar istisna).
Skor ilə reason uyğun olmalıdır: reason "yemək ehtiyacı yoxdur" deyirsə, score 0.2-dən yuxarı olmasın.

"reason" satış komandası üçündür: bu konkret mətndə nə gördüyünü yaz (şirkət
adı, nə alır, niyə uyğundur/deyil). Yuxarıdakı qaydaların mətnini təkrarlama.

Yalnız bu JSON formatında cavab ver, başqa heç nə yazma:
{"score": 0.0-1.0 arası float, "reason": "bir cümləlik qısa əsaslandırma, Azərbaycan dilində"}
"""


# "Son tarix: 06.10.2026" / "Son tarix: 06.10.2026 18:00" (etender, corporate, bp EDP, fed).
_DEADLINE_RE = re.compile(r"Son tarix:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")


def annotate_deadline(text: str, today: date) -> str:
    """Son tarixi kodda müqayisə et, nəticəni AI-yə açıq yaz.

    gpt-4o-mini tarixləri səhv müqayisə edirdi: 06.10.2026 son tarixli açıq
    tenderi 27.09.2026-da "son tarix keçib" deyib rədd etmişdi (2026-09-27 testi).
    """
    m = _DEADLINE_RE.search(text)
    if not m:
        return text
    try:
        deadline = date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return text
    days = (deadline - today).days
    if days >= 0:
        return f"{text}\n\n[SON TARİX: AKTİV — {days} gün qalıb]"
    return f"{text}\n\n[SON TARİX: BİTİB — {-days} gün əvvəl]"


class AIFilter:
    """Sənəd §6 Lead scoring & AI filtering-in tətbiqi."""

    def __init__(self, client: OpenAI | None = None, model: str | None = None) -> None:
        self._client = client
        self.model = model or os.environ.get("AI_MODEL") or DEFAULT_MODEL

    @property
    def client(self) -> OpenAI:
        # Lazy: OpenAI() key olmadıqda konstruktorda xəta atır — bu, score()-un
        # try blokunda baş versin ki, lead pending_review-a düşsün, pipeline çökməsin.
        if self._client is None:
            self._client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), max_retries=OPENAI_MAX_RETRIES)
        return self._client

    @staticmethod
    def today() -> date:
        return datetime.now(BAKU_TZ).date()

    def system_prompt(self) -> str:
        # .replace — prompt-dakı JSON nümunəsinin {} mötərizələri .format-ı sındırardı.
        from app.keywords import company_profile

        return (
            SYSTEM_PROMPT.replace("{company}", company_profile() or DEFAULT_COMPANY)
            .replace("{today}", self.today().strftime("%d.%m.%Y"))
        )

    def score(self, lead: RawLead) -> RawLead:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=200,
                # Eyni post eyni skoru almalıdır — defolt temperature-da vergi
                # izahı postu bir dəfə 0.0, bir dəfə 0.6 (eşik) almışdı.
                temperature=0,
                # JSON rejimi: model cavabı ```json ... ``` blokuna salmır.
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self.system_prompt()},
                    {"role": "user", "content": annotate_deadline(lead.raw_text[:4000], self.today())},
                ],
            )
            parsed = json.loads(response.choices[0].message.content)
            lead.ai_score = min(max(float(parsed["score"]), 0.0), 1.0)
            lead.ai_reason = str(parsed["reason"])
            lead.status = (
                LeadStatus.pending_review
                if lead.ai_score >= SCORE_THRESHOLD
                else LeadStatus.rejected
            )
            log.info(
                "ai_scored",
                source=lead.source.value,
                score=lead.ai_score,
                status=lead.status.value,
                title=lead.title[:90],
            )
        except Exception:
            # AI qiymətləndirməsi alınmadı (API key yoxdur, LLM xətası və s.) —
            # lead SƏSSİZCƏ RƏDD EDİLMİR: insan özü baxsın deyə pending_review-a
            # düşür, ai_score=None qalır ("—" kimi göstərilir admin paneldə).
            log.exception("ai_scoring_failed", source=lead.source, url=lead.source_url)
            lead.ai_score = None
            lead.ai_reason = "AI qiymətləndirməsi alınmadı — əl ilə yoxlanmalıdır"
            lead.status = LeadStatus.pending_review

        return lead

    def score_batch(self, leads: list[RawLead]) -> list[RawLead]:
        return [self.score(lead) for lead in leads]
