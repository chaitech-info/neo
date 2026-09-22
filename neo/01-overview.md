# 01 — Overview

## Nə üçündür

Catering şirkəti üçün **AI əsaslı şirkət idarəetmə ekosistemi**nin bir hissəsi: internetdə potensial müştəriləri (tədbir, banket, korporativ catering ehtiyacı olan şəxs/şirkət) avtomatik aşkarlayan, AI ilə qiymətləndirən və CRM-ə ötürən **lead scraping modulu**.

Bu modul daha böyük bir sistemin (Executive AI Assistant + CRM + NeoAI + NeoAnalyze) parçasıdır — konseptual olaraq **NeoAnalyze**-ə uyğun gəlir: arxa planda 24/7 işləyən, problemi/fürsəti aşkarlayan, CRM-də avtomatik task yaradan mühərrik.

## Əsas iş axını (bir cümlədə)

```
Scrape → Normalize (dedup) → AI Score → İnsan təsdiqi (pending_review) → CRM
```

## Nəyi HƏLL edir

- Potensial müştərini əl ilə axtarmaq yerinə, sistem 8 mənbəni avtomatik izləyir
- Dublikatları, əlaqəsiz elanları avtomatik ayıqlayır (AI skor + keyword filtri)
- Yekun qərar həmişə insan tərəfindən verilir (human-in-the-loop — səhv/qərəzli AI qərarının CRM-i "zibilləməsinin" qarşısı alınır)

## Nəyi HƏLL ETMİR (scope-dan kənar)

- LinkedIn scraping (hüquqi/texniki qərar gözlənilir — bax `04-decisions.md`)
- Real CRM inteqrasiyası (CRM tərəfin API-si hələ təyin olunmayıb)
- Avtomatik əlaqə saxlama/satış (sistem yalnız "bu potensial müştəridir" deyir, zəng/email atmır)

## İştirakçılar

| Rol | Məsuliyyət |
| --- | --- |
| Development komandası | Scraper-lərin yazılması/saxlanması, admin panel, pipeline |
| CRM komandası | `/opportunities` endpoint-i, autentifikasiya (bax `04-decisions.md`) |
| Hüquq şöbəsi | Hər saytın ToS/robots.txt təsdiqi, LinkedIn qərarı |
| Satış/əməliyyat komandası | Admin paneldə validasiya (Təsdiqlə/Rədd et) |

## Uğur necə ölçülür (hələ təsdiqlənməyib — TODO)

Sənəddə "uydurma rəqəm istifadə etmə" qaydasına görə burada konkret ədəd yoxdur. Production-a keçəndə ölçülməli göstəricilər: gündəlik aşkarlanan lead sayı, `pending_review → sent_to_crm` konversiya faizi, satış komandasının real "qazanılmış" geri bildirimi.
