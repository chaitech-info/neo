# Knowledge Base — Catering AI Ecosystem / Lead Scraping

Layihə haqqında mərkəzləşdirilmiş bilgi bazası. Development komandasına yeni qoşulan hər kəs bu qovluqdan başlamalıdır.

## Naviqasiya

| Sənəd | Nə üçün |
| --- | --- |
| [01-overview.md](01-overview.md) | Layihə nədir, niyə var, hansı problemi həll edir |
| [02-architecture.md](02-architecture.md) | Sistem arxitekturası, komponentlər, data axını |
| [03-site-research.md](03-site-research.md) | Hər scrape mənbəyinin real strukturu, API-lər, seçicilər (canlı yoxlamalardan) |
| [04-decisions.md](04-decisions.md) | Açıq suallar, qəbul edilmiş qərarlar, niyə belə seçildi |
| [05-runbook.md](05-runbook.md) | Qurulum, işə salma, admin panel, testlər — əməli təlimat |
| [06-glossary.md](06-glossary.md) | Termin lüğəti (RawLead, pending_review, NeoAnalyze və s.) |

## Əlaqəli sənədlər (bu qovluqdan kənarda)

- [../lead-scraping-spec.md](../lead-scraping-spec.md) — tam development spesifikasiyası (rəsmi, canlı versiyası da var)
- [../presentation/](../presentation) — müştəri təqdimatının mənbə faylları
- [../../README.md](../../README.md) — layihənin əsas README-si (qurulum qısaca)

## Bu KB-ni necə saxlamalı

- Yeni sayt/API kəşf edəndə → `03-site-research.md`-ə əlavə et, `app/scrapers/*.py`-dəki şərhlərlə üst-üstə düşsün.
- Açıq sual həll olunanda → `04-decisions.md`-də statusu yenilə (Open → Decided), tarixi yaz.
- Yeni komponent/servis əlavə olunanda → `02-architecture.md`-dəki diaqramı yenilə.
- Köhnəlmiş məlumatı silmə — "Decided" və ya "Deprecated" kimi işarələ ki, tarixçə qalsın.
