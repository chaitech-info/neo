"""Albom (üfüqi A4) qiymət təklifi — "Albom" dizaynı (offer_pdf.DESIGNS["sheet"]).

Quruluş Askar-ın göndərdiyi nümunə qiymət təklifindəndir (SP-20261021-002), rənglər fərqlidir (tünd firuzəyi
və loqonun firuzəyisi, nümunədəki tünd göy + çəhrayı əvəzinə):
1. Üz qabığı — solda tünd panel (loqo, "Qiymət təklifi", konsentrik dairələr), sağda müştəri, salam mətni,
   təklif nömrəsi və tarix.
2. "Menyu paketləri" — hər paket öz kartında (qiymət, menyu / gündəlik seçim), yanda keyfiyyət sertifikatları
   və şirkətin əlaqə məlumatları (yalnız Seaport).
3. "Həftəlik menyu" — nümunədə olmayan hissə: həftəlik paketin hər günü öz kartında (kateqoriya → yeməklər).
4. "Qiymət xülasəsi" — paket / vahid qiymət / miqdar / cəmi cədvəli, yekun kartı, təşəkkür və əlaqə.

Paket modeli, menyu planı və "menyudan çıxart" offer_pdf-dəki kimidir (`offer_pdf._package`, `without`).
Şəkilli təklifdə də yemək şəkilləri bu dizayna düşmür — həftəlik menyu mətnlə qalır.
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen.canvas import Canvas

from app import offer_pdf as op

W, H = landscape(A4)
M = 40.0                      # sol / sağ kənar
PANEL = 300.0                 # üz qabığında sol panelin eni
HEAD = 58.0                   # məzmun səhifələrinin yuxarı zolağı
TOP, BOTTOM = 84.0, H - 62.0  # məzmunun yeri
GAP = 14.0

# Rənglər — nümunənin quruluşu, öz rəngləri: tünd firuzəyi panel, loqonun firuzəyisi vurğu, açıq boz-mavi fon
DEEP = HexColor("#0f3a44")
DEEP_SOFT = HexColor("#1d5662")
ACCENT = HexColor(op.LOGO_TEAL)
ACCENT_LIGHT = HexColor("#86d5df")
BG = HexColor("#f4f8f8")
SOFT = HexColor("#e1eef0")
INK = HexColor("#15303a")
MUTED = HexColor("#667b82")
LINE = HexColor("#d3e1e3")
WHITE = op.WHITE
MARK = HexColor("#0999ad")    # "NÜMUNƏ" su nişanı (şəffaf)

COMPANY = {
    "seaport": ("Seaport Catering MMC",
                ("Zahid Xəlilov küçəsi, 576-cı məhəllə", "Bakı, AZ1141; “Renessans Palace” 17-ci mərtəbə"),
                "(+994) 50 254 51 64", "info@seaport-catering.az · seaport-catering.az"),
}
CERTS = {"seaport": ("HACCP", "ISO 22000:2018", "ISO 9001:2015", "ISO 45001:2018")}

# Sabit mətnlər: (az, en, ru)
T = {
    "offer": ("Qiymət təklifi", "Price offer", "Ценовое предложение"),
    "for": ("Hazırlanıb", "Prepared for", "Подготовлено для"),
    "no": ("Təklif №", "Offer no.", "Предложение №"),
    "date": ("Tarix", "Date", "Дата"),
    "days": ("Günlər", "Days", "Дни"),
    "packages": ("Menyu paketləri", "Menu packages", "Пакеты меню"),
    "package": ("Paket", "Package", "Пакет"),
    "weekly": ("Həftəlik menyu", "Weekly menu", "Меню на неделю"),
    "more": ("davamı", "continued", "продолжение"),
    "daily": ("Gündəlik seçim", "Daily selection", "Ежедневный выбор"),
    "see_week": ("Günlər üzrə menyu növbəti səhifədədir.", "The day-by-day menu is on the next page.",
                 "Меню по дням — на следующей странице."),
    "quality": ("Keyfiyyət və təhlükəsizlik", "Quality and safety", "Качество и безопасность"),
    "summary": ("Qiymət xülasəsi", "Price summary", "Сводка цен"),
    "unit_price": ("Vahid qiymət", "Unit price", "Цена за ед."),
    "qty": ("Miqdar", "Quantity", "Количество"),
    "sum": ("Cəmi", "Total", "Итого"),
    "vat_out": ("18% ƏDV xaric", "18% VAT excluded", "18% НДС не включён"),
    "vat_in": ("ƏDV daxil", "VAT included", "НДС включён"),
    "grand": ("Yekun (ƏDV daxil)", "Total (incl. VAT)", "Итого (с НДС)"),
    "later": ("Yekun məbləğ miqdar təsdiqləndikdən sonra hesablanır.",
              "The total is calculated once the quantity is confirmed.",
              "Итоговая сумма рассчитывается после подтверждения количества."),
    "net": ("ƏDV xaric", "Excl. VAT", "Без НДС"),
    "vat": ("ƏDV", "VAT", "НДС"),
    "per_person": ("Qiymət 1 nəfərlik yemək üçündür.", "The price is for one person's meal.",
                   "Цена указана за питание одного человека."),
    "thanks": ("Sizinlə əməkdaşlıq etməkdən məmnun olarıq.", "We would be glad to work with you.",
               "Будем рады сотрудничеству с вами."),
    "ask": ("Sualınız olarsa, istənilən vaxt bizimlə əlaqə saxlaya bilərsiniz.",
            "If you have any questions, feel free to contact us at any time.",
            "Если у вас есть вопросы, свяжитесь с нами в любое время."),
    "page": ("səh.", "p.", "стр."),
    "sample": ("NÜMUNƏ", "SAMPLE", "ОБРАЗЕЦ"),
    "person": ("nəfər", "people", "чел."),
    "day_unit": ("gün", "days", "дн."),
}


def _greeting(customer: str, lang: str) -> str:
    if lang == "en":
        return (f"Dear {customer}, thank you for your trust. Based on your request, here are the menu packages "
                "we have selected for you.")
    if lang == "ru":
        return (f"Уважаемый {customer}, благодарим вас за доверие. По вашему запросу представляем подобранные "
                "для вас пакеты меню.")
    return (f"Hörmətli {customer}, bizə göstərdiyiniz etimada görə təşəkkür edirik. Sorğunuza əsasən sizin üçün "
            "seçdiyimiz menyu paketlərini təqdim edirik.")


def _offer_date(offer_id: str, snapshot: dict) -> str:
    """Təklifin tarixi: snapshot-da varsa ondan, yoxsa nömrədən (SP-20261021-002 → 21.10.2026), yoxsa bu gün."""
    for key in ("date", "created_at", "created"):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(snapshot.get(key) or ""))
        if m:
            return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", offer_id)
    if m:
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    return date.today().strftime("%d.%m.%Y")


def az_upper(s: str) -> str:
    """Azərbaycan hərfləri ilə böyük hərf: "Digər" → "DİGƏR" (str.upper() "DIGƏR" verir), "ı" → "I"."""
    return s.replace("i", "İ").upper()


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Sheet:
    def __init__(self, snapshot: dict, plans: dict[int, op.Package] | None = None, exclude: dict | None = None):
        op._register_fonts()
        self.snap = snapshot
        self.lang = snapshot.get("language") if snapshot.get("language") in ("az", "en", "ru") else "az"
        self.li = {"en": 1, "ru": 2}.get(self.lang, 0)
        self.brand = snapshot.get("brand") or "seaport"
        totals = snapshot.get("totals") or {}
        self.totals = totals
        self.currency = totals.get("currency") or "AZN"
        self.lines = [l for l in snapshot.get("lines") or [] if isinstance(l, dict)]
        self.packages = [op.without(pkg, exclude) for pkg in
                         ((plans or {}).get(i) or op._package(l, self.lang, self.currency)
                          for i, l in enumerate(self.lines))]
        self.offer_id = str(snapshot.get("offer_id") or snapshot.get("id") or "")
        self.customer = (snapshot.get("customer_name") or "").strip()
        self.company = COMPANY.get(self.brand)
        self.buf = io.BytesIO()
        self.c = Canvas(self.buf, pagesize=(W, H))
        self.c.setTitle(f"{T['offer'][self.li]} · {self.offer_id}" if self.offer_id else T["offer"][self.li])
        self.c.setAuthor("Seaport Catering" if self.brand == "seaport" else "NAFA")
        self.sans = op.Text(self.c, True, INK)
        self.serif = op.Text(self.c, False, INK)
        self.sans.page_h = self.serif.page_h = H     # üfüqi səhifə: mətnin yeri bu hündürlükdən hesablanır
        self.page_no = 0
        self.y = TOP

    # --- köməkçilər -------------------------------------------------------------------------------
    def tx(self, key: str) -> str:
        return T[key][self.li]

    def pair(self, key: str) -> str:
        """Etiket təklifin dilində, altında / yanında ingiliscə: "Tarix / Date" (ingiliscə təklifdə — yalnız "Date")."""
        main = T[key][self.li]
        return main if self.lang == "en" else f"{main} / {T[key][1]}"

    def up(self, s: str) -> str:
        return az_upper(s) if self.lang == "az" else s.upper()

    def up_pair(self, key: str) -> str:
        main = self.up(T[key][self.li])
        return main if self.lang == "en" else f"{main} / {T[key][1].upper()}"

    def fill(self, color: Any, x: float, top: float, w: float, h: float, r: float = 0) -> None:
        self.c.setFillColor(color)
        if r:
            self.c.roundRect(x, H - top - h, w, h, r, stroke=0, fill=1)
        else:
            self.c.rect(x, H - top - h, w, h, stroke=0, fill=1)

    def rule(self, y: float, x1: float, x2: float, color: Any = LINE, width: float = 0.8) -> None:
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x1, H - y, x2, H - y)

    def money(self, value: Any) -> str:
        s = op._money(value, self.currency)
        return s.replace(".", ",") if self.lang == "az" else s

    def price_parts(self, p: op.Package) -> tuple[str, str]:
        """"8.90 AZN" → ("8,90", "AZN")."""
        if not p.price:
            return "—", ""
        num, _, cur = p.price.rpartition(" ")
        num = num or p.price
        return (num.replace(".", ",") if self.lang == "az" else num), cur

    def logo_disc(self, cx: float, cy: float, r: float) -> None:
        """Loqo (dairəvi, ağ fonlu) ağ dairənin içində — tünd fonda."""
        self.c.setFillColor(WHITE)
        self.c.circle(cx, H - cy, r, stroke=0, fill=1)
        if self.brand == "seaport":
            img = op._logo("seaport-logo.png")
            iw, ih = img.getSize()
            h = r * 1.62
            w = h * iw / ih
            c = self.c
            c.saveState()                    # loqonun ağ fonunun küncləri dairədən çıxmasın
            path = c.beginPath()
            path.circle(cx, H - cy, r)
            c.clipPath(path, stroke=0, fill=0)
            c.drawImage(img, cx - w / 2, H - cy - h / 2, w, h, mask="auto")
            c.restoreState()
        else:
            self.serif.draw(cx, cy + r * 0.2, "NAFA", r * 0.55, DEEP, align="center")

    def watermark(self) -> None:
        if not self.snap.get("example"):
            return
        c = self.c
        c.saveState()
        c.setFillColor(MARK)
        c.setFillAlpha(0.08)
        c.translate(W / 2, H / 2 - 30)
        c.rotate(28)
        word = self.tx("sample")
        c.setFont("Sans", 130)
        c.drawCentredString(0, 0, word)
        c.restoreState()

    def circles(self, cx: float, cy: float, clip: tuple[float, float, float, float]) -> None:
        """Nümunədəki kimi konsentrik dairələr (panelin kənarında kəsilir)."""
        c = self.c
        c.saveState()
        path = c.beginPath()
        x, top, w, h = clip
        path.rect(x, H - top - h, w, h)
        c.clipPath(path, stroke=0, fill=0)
        c.setFillColor(ACCENT)
        c.setFillAlpha(0.16)
        c.circle(cx, H - cy, 78, stroke=0, fill=1)
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1)
        for r, alpha in ((112, 0.55), (160, 0.4), (208, 0.28), (256, 0.18)):
            c.setStrokeAlpha(alpha)
            c.circle(cx, H - cy, r, stroke=1, fill=0)
        c.restoreState()

    # --- səhifələr --------------------------------------------------------------------------------
    def cover(self) -> None:
        s, serif = self.sans, self.serif
        self.fill(BG, 0, 0, W, H)
        self.fill(DEEP, 0, 0, PANEL, H)
        self.circles(PANEL, H * 0.53, (0, 0, PANEL, H))
        self.logo_disc(M + 46, 92, 48)
        # Başlıq: təklifin dilində, altında digər dil(lər)
        y = 382.0
        for line in serif.wrap(self.tx("offer"), 31, PANEL - 2 * M)[:2]:
            serif.draw(M, y, line, 31, WHITE)
            y += 36
        if self.lang != "en":
            s.draw(M, y, T["offer"][1], 12.5, ACCENT_LIGHT)
            y += 18
        self.fill(ACCENT, M, y + 12, 52, 3.5)

        x0 = PANEL + 58
        width = W - M - x0
        s.draw(x0, 112, self.up_pair("for"), 7.5, ACCENT, tracking=1.6)
        y = 160.0
        name = self.customer or "—"
        size = 34.0
        lines = serif.wrap(name, size, width)
        if len(lines) > 2:
            size = 26.0
            lines = serif.wrap(name, size, width)[:3]
        for line in lines:
            serif.draw(x0, y, line, size, INK)
            y += size + 6
        self.fill(ACCENT, x0, y - 4, 46, 3.5)
        y += 34
        if self.customer:
            for line in s.wrap(_greeting(self.customer, self.lang), 11.5, min(width, 400))[:5]:
                s.draw(x0, y, line, 11.5, MUTED)
                y += 19
        # Paket(lər) qısaca — üz qabığında
        if self.packages:
            y += 14
            for p in self.packages[:3]:
                if y > 405:
                    break
                num, cur = self.price_parts(p)
                s.draw(x0, y, p.name, 11, INK)
                s.draw(x0 + min(width, 400), y, f"{num} {cur}".strip() + (f" / {p.unit}" if p.unit else ""),
                       11, ACCENT, align="right")
                y += 18

        self.rule(H - 140, x0, W - M)
        fields = [("no", self.offer_id or "—"), ("date", _offer_date(self.offer_id, self.snap))]
        spans = {p.days_span for p in self.packages if p.days_span}
        if len(spans) == 1:
            fields.append(("days", spans.pop()[0]))
        col = width / len(fields)
        for i, (key, value) in enumerate(fields):
            x = x0 + i * col
            s.draw(x, H - 112, self.pair(key), 7, MUTED, tracking=0.3)
            serif.draw(x, H - 92, value, op_fit(serif, value, 14.5, col - 12), INK)
        self.watermark()
        self.c.showPage()

    def header(self) -> None:
        self.page_no += 1
        s = self.sans
        self.fill(BG, 0, 0, W, H)
        self.fill(DEEP, 0, 0, W, HEAD)
        self.fill(ACCENT, 0, HEAD, W, 3.5)
        self.logo_disc(M + 19, HEAD / 2, 21)
        brand = "Seaport Catering" if self.brand == "seaport" else "NAFA"
        s.draw(M + 50, HEAD / 2 + 4, brand, 10, WHITE, tracking=0.6)
        head = self.tx("offer") + (f" · {self.offer_id}" if self.offer_id else "")
        s.draw(W - M, HEAD / 2 + 4, head, 10, WHITE, align="right")
        self.y = TOP

    def footer(self) -> None:
        s = self.sans
        self.rule(H - 46, M, W - M, ACCENT, 0.9)
        if self.company:
            name, address, phone, mail = self.company
            s.draw(M, H - 32, f"{name} · {address[0]}; {address[1]}", 7, MUTED)
            s.draw(M, H - 21, f"{phone} · {mail}", 7, MUTED)
        s.draw(W - M, H - 32, f"{self.tx('page')} {self.page_no + 1}", 7, MUTED, align="right")
        if self.offer_id:
            s.draw(W - M, H - 21, self.offer_id, 7, MUTED, align="right")
        self.watermark()
        self.c.showPage()

    def title(self, key: str, suffix: str = "", right: str = "") -> None:
        y = self.y
        self.serif.draw(M, y + 22, self.tx(key) + (f" · {suffix}" if suffix else ""), 23, INK)
        sub = "" if self.lang == "en" else T[key][1]
        if sub:
            self.sans.draw(M, y + 38, sub, 8.5, MUTED)
        if right:
            self.sans.draw(W - M, y + 22, right, 9, ACCENT, align="right", tracking=0.4)
        self.y = y + 56

    # --- "Menyu paketləri" -------------------------------------------------------------------------
    def card_rows(self, p: op.Package, width: float) -> list[tuple[str, str]]:
        """Kartın gövdəsi: [("label", "ŞORBA") | ("item", "Mərci şorbası") | ("note", "...")]."""
        rows: list[tuple[str, str]] = []
        if p.weekly:
            rows.append(("label", self.tx("daily")))
            for n, label in op.daily_selection(p, self.lang):
                rows.append(("item", f"{n} {label}" if n else label))
            rows.append(("note", self.tx("see_week")))
            return rows
        for b in p.blocks:
            for g in b.groups:
                if g.label:
                    rows.append(("label", g.label))
                for item in g.items:
                    for k, line in enumerate(self.sans.wrap(item, 9.5, width - 14)):
                        rows.append(("item" if k == 0 else "cont", line))
        return rows

    @staticmethod
    def row_height(kind: str) -> float:
        return {"label": 22.0, "item": 15.5, "cont": 13.0, "note": 26.0}[kind]

    def package_card(self, i: int, p: op.Package, x: float, top: float, w: float, h: float,
                     rows: list[tuple[str, str]]) -> None:
        c, s, serif = self.c, self.sans, self.serif
        # kölgə + kart
        self.fill(LINE, x + 1, top + 2, w, h, 12)
        self.fill(WHITE, x, top, w, h, 12)
        # tünd başlıq (yuxarı künclər yumru)
        c.saveState()
        path = c.beginPath()
        path.roundRect(x, H - top - h, w, h, 12)
        c.clipPath(path, stroke=0, fill=0)
        self.fill(DEEP, x, top, w, 74)
        c.setFillColor(DEEP_SOFT)
        c.circle(x + w - 8, H - top - 6, 46, stroke=0, fill=1)
        self.fill(ACCENT, x, top + 74, w, 3.5)
        c.restoreState()
        s.draw(x + 20, top + 25, f"{self.up(self.tx('package'))} {i + 1}", 7.5, ACCENT_LIGHT, tracking=1.6)
        serif.draw(x + 20, top + 56, p.name, op_fit(serif, p.name, 21, w - 40), WHITE)
        # qiymət
        num, cur = self.price_parts(p)
        nw = serif.draw(x + 20, top + 124, num, 32, INK)
        if cur:
            s.draw(x + 28 + nw, top + 108, cur, 10, INK)
        if p.unit:
            s.draw(x + 28 + nw, top + 122, f"/ {p.unit}", 8, MUTED)
        y = top + 140
        if p.description:
            for line in s.wrap(p.description, 8, w - 40)[:2]:
                s.draw(x + 20, y, line, 8, MUTED)
                y += 11
        y += 4
        self.rule(y, x + 20, x + w - 20)
        y += 8
        limit = top + h - 12
        for kind, text in rows:
            step = self.row_height(kind)
            if y + step > limit:
                s.draw(x + 34, y + 12, "…", 9.5, MUTED)
                break
            if kind == "label":
                s.draw(x + 20, y + 16, self.up(text), 7, ACCENT, tracking=1.4)
            elif kind == "note":
                for j, line in enumerate(s.wrap(text, 7.5, w - 40)[:2]):
                    s.draw(x + 20, y + 16 + j * 10, line, 7.5, MUTED, italic=True)
            else:
                if kind == "item":
                    c.setFillColor(ACCENT)
                    c.circle(x + 23, H - (y + 9), 1.8, stroke=0, fill=1)
                s.draw(x + 33, y + 12, text, 9.5, INK)
            y += step

    def side_column(self, x: float, top: float, w: float) -> None:
        s, serif = self.sans, self.serif
        y = top
        certs = CERTS.get(self.brand)
        if certs:
            h = 150.0
            self.fill(SOFT, x, y, w, h, 12)
            serif.draw(x + 18, y + 34, self.tx("quality"), op_fit(serif, self.tx("quality"), 14.5, w - 36), INK)
            cx, cy = x + 18, y + 54
            for cert in certs:
                cw = s.width(cert, 8.5) + 22
                if cx + cw > x + w - 14:
                    cx, cy = x + 18, cy + 32
                self.c.setStrokeColor(DEEP)
                self.c.setLineWidth(0.9)
                self.c.roundRect(cx, H - cy - 22, cw, 22, 11, stroke=1, fill=0)
                s.draw(cx + cw / 2, cy + 14.5, cert, 8.5, DEEP, align="center")
                cx += cw + 8
            y += h + GAP
        self.contact_card(x, y, w)

    def contact_card(self, x: float, top: float, w: float) -> float:
        if not self.company:
            return top
        s, serif = self.sans, self.serif
        name, address, phone, mail = self.company
        h = 112.0
        self.fill(SOFT, x, top, w, h, 12)
        serif.draw(x + 18, top + 28, name, 13.5, INK)
        y = top + 46
        for line in address:
            s.draw(x + 18, y, line, 8, MUTED)
            y += 12
        s.draw(x + 18, y + 6, phone, 9, INK)
        s.draw(x + 18, y + 22, mail, 8, MUTED)
        return top + h

    def packages_pages(self) -> None:
        if not self.packages:
            return
        side_w = 230.0 if self.company else 0.0
        queue = list(enumerate(self.packages))
        first = True
        while queue:
            self.header()
            self.title("packages")
            per = 2 if (first and side_w) else 3
            chunk, queue = queue[:per], queue[per:]
            area = W - 2 * M - (side_w + GAP if first and side_w else 0)
            cols = max(len(chunk), 2) if per == 2 else 3
            w = (area - GAP * (cols - 1)) / cols
            rows = [self.card_rows(p, w - 40) for _, p in chunk]
            body = max(sum(self.row_height(k) for k, _ in r) for r in rows)
            desc = 24 if any(p.description for _, p in chunk) else 0
            h = min(max(156 + desc + body + 14, 250), BOTTOM - self.y)
            for j, ((i, p), r) in enumerate(zip(chunk, rows)):
                self.package_card(i, p, M + j * (w + GAP), self.y, w, h, r)
            if first and side_w:
                self.side_column(W - M - side_w, self.y, side_w)
            first = False
            self.footer()

    # --- "Həftəlik menyu" ----------------------------------------------------------------------------
    def day_rows(self, b: op.Block, width: float) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for g in b.groups:
            if g.label:
                rows.append(("label", g.label))
            for item in g.items:
                for k, line in enumerate(self.sans.wrap(item, 8.8, width)):
                    rows.append(("item" if k == 0 else "cont", line))
                note = g.notes.get(item, "")
                if note:
                    for line in self.sans.wrap(note, 7, width)[:2]:
                        rows.append(("dnote", line))
        return rows

    DAY_STEP = {"label": 18.0, "item": 13.5, "cont": 11.5, "dnote": 10.0}

    def day_card(self, b: op.Block, x: float, top: float, w: float, h: float, rows: list[tuple[str, str]]) -> None:
        c, s = self.c, self.sans
        self.fill(LINE, x + 1, top + 2, w, h, 10)
        self.fill(WHITE, x, top, w, h, 10)
        c.saveState()
        path = c.beginPath()
        path.roundRect(x, H - top - h, w, h, 10)
        c.clipPath(path, stroke=0, fill=0)
        self.fill(DEEP, x, top, w, 40)
        self.fill(ACCENT, x, top + 40, w, 2.5)
        c.restoreState()
        title, _, week = b.title.partition(" · ")
        s.draw(x + 12, top + 18, s.wrap(title, 10.5, w - 24)[0], 10.5, WHITE)
        sub = " · ".join(v for v in (b.accent if self.lang != "en" else "", week) if v)
        if sub:
            s.draw(x + 12, top + 32, sub, 7, ACCENT_LIGHT, tracking=0.4)
        y = top + 48
        for kind, text in rows:
            step = self.DAY_STEP[kind]
            if y + step > top + h - 4:       # səhifəyə sığmayan (çox uzun) gün — kartın içində kəsilir
                s.draw(x + 20, y + 8, "…", 8.8, MUTED)
                break
            if kind == "label":
                s.draw(x + 12, y + 13, self.up(text), 6.5, ACCENT, tracking=1.2)
            elif kind == "dnote":
                s.draw(x + 20, y + 7, text, 7, MUTED)
            else:
                if kind == "item":
                    c.setFillColor(ACCENT)
                    c.circle(x + 14, H - (y + 7.2), 1.5, stroke=0, fill=1)
                s.draw(x + 20, y + 10, text, 8.8, INK)
            y += step

    def weekly_pages(self) -> None:
        weekly = [p for p in self.packages if p.weekly and p.blocks]
        for p in weekly:
            n = len(p.blocks)
            cols = 5 if n in (5, 10) or n > 7 else min(n, 4) if n != 6 else 3
            w = (W - 2 * M - GAP * (cols - 1)) / cols
            inner = w - 32
            rows = [self.day_rows(b, inner) for b in p.blocks]
            heights = [48 + sum(self.DAY_STEP[k] for k, _ in r) + 10 for r in rows]
            suffix = p.name if len(weekly) > 1 or len(self.packages) > 1 else ""
            span = p.days_span[0] if p.days_span else ""
            self.header()
            self.title("weekly", suffix, span)
            for start in range(0, n, cols):
                idx = list(range(start, min(start + cols, n)))
                h = max(heights[i] for i in idx)
                if self.y + h > BOTTOM and self.y > TOP + 60:
                    self.footer()
                    self.header()
                    self.title("weekly", (suffix + " · " if suffix else "") + self.tx("more"), span)
                h = min(h, BOTTOM - self.y)
                for j, i in enumerate(idx):
                    self.day_card(p.blocks[i], M + j * (w + GAP), self.y, w, h, rows[i])
                self.y += h + GAP
            self.footer()

    # --- "Qiymət xülasəsi" ---------------------------------------------------------------------------
    def quantity(self, line: dict) -> tuple[str, str]:
        q, d = _number(line.get("quantity")), _number(line.get("days"))
        total = line.get("line_total")
        if q is None:
            return "—", "—"
        qs = f"{q:g} {self.tx('person')}" + (f" × {d:g} {self.tx('day_unit')}" if d is not None else "")
        return qs, (self.money(total) if total not in (None, "") else "—")

    def summary_page(self) -> None:
        s, serif = self.sans, self.serif
        self.header()
        self.title("summary")
        top = self.y
        tw = 470.0
        x1, x2 = M, M + tw
        cols = (x1 + 18, x1 + 205, x1 + 300, x2 - 18)    # paket | vahid qiymət (sağa) | miqdar (orta) | cəmi (sağa)
        self.fill(SOFT, x1, top, tw, 32, 8)
        hy = top + 20
        s.draw(cols[0], hy, self.up(self.tx("package")), 7, MUTED, tracking=1.3)
        s.draw(cols[1], hy, self.up(self.tx("unit_price")), 7, MUTED, tracking=1.3, align="right")
        s.draw(cols[2], hy, self.up(self.tx("qty")), 7, MUTED, tracking=1.3, align="center")
        s.draw(cols[3], hy, self.up(self.tx("sum")), 7, MUTED, tracking=1.3, align="right")
        y = top + 32
        for k, (p, line) in enumerate(zip(self.packages, self.lines)):
            if y + 44 > BOTTOM - 170:
                break
            if k % 2 == 0:
                self.fill(HexColor("#fbfdfd"), x1, y, tw, 44)
            num, cur = self.price_parts(p)
            qty, total = self.quantity(line)
            serif.draw(cols[0], y + 27, p.name, op_fit(serif, p.name, 13, 112), INK)
            s.draw(cols[1], y + 27, f"{num} {cur}".strip(), 10, INK, align="right")
            s.draw(cols[2], y + 27, qty, 9.5, MUTED, align="center")
            s.draw(cols[3], y + 27, total, 10.5, INK, align="right")
            y += 44
        self.rule(y, x1, x2)
        vat_in = self.totals.get("vat_included") is True
        s.draw(x2, y + 22, " / ".join(dict.fromkeys(
            (T["vat_in" if vat_in else "vat_out"][self.li], T["vat_in" if vat_in else "vat_out"][1]))),
            8.5, ACCENT, align="right")
        s.draw(x2, y + 36, self.tx("per_person"), 8, MUTED, align="right")

        # Yekun kartı
        kx, kw, kh = x2 + 30, W - M - x2 - 30, 230.0
        c = self.c
        c.saveState()
        path = c.beginPath()
        path.roundRect(kx, H - top - kh, kw, kh, 14)
        c.clipPath(path, stroke=0, fill=0)
        self.fill(DEEP, kx, top, kw, kh)
        c.setFillColor(DEEP_SOFT)
        c.circle(kx + kw - 10, H - top - 12, 64, stroke=0, fill=1)
        c.restoreState()
        s.draw(kx + 24, top + 40, self.up(self.tx("grand")), 7.5, ACCENT_LIGHT, tracking=1.6)
        total = self.totals.get("total")
        if total not in (None, ""):
            serif.draw(kx + 24, top + 86, self.money(total), op_fit(serif, self.money(total), 30, kw - 48), WHITE)
            yy = top + 116
            for key, value in (("net", self.totals.get("subtotal")), ("vat", self.totals.get("vat"))):
                if value not in (None, ""):
                    s.draw(kx + 24, yy, self.tx(key), 9, ACCENT_LIGHT)
                    s.draw(kx + kw - 24, yy, self.money(value), 9, WHITE, align="right")
                    yy += 18
        else:
            yy = top + 76
            for line in serif.wrap(self.tx("later"), 13, kw - 48)[:4]:
                serif.draw(kx + 24, yy, line, 13, WHITE)
                yy += 20

        # Təşəkkür + əlaqə
        by = BOTTOM - 118
        cw = 250.0 if self.company else 0.0
        tw2 = W - 2 * M - (cw + 30 if cw else 0)
        yy = by + 26
        for line in serif.wrap(self.tx("thanks"), 18, tw2)[:2]:
            serif.draw(M, yy, line, 18, INK)
            yy += 24
        for line in s.wrap(self.tx("ask"), 10, tw2)[:2]:
            s.draw(M, yy + 4, line, 10, MUTED)
            yy += 14
        if cw:
            self.contact_card(W - M - cw, by, cw)
        self.footer()

    def build(self) -> bytes:
        self.cover()
        self.packages_pages()
        self.weekly_pages()
        self.summary_page()
        self.c.save()
        return self.buf.getvalue()


def op_fit(t: op.Text, s: str, size: float, width: float) -> float:
    """Başlıq sığmırsa kiçilir (ən çox yarıya qədər)."""
    w = t.width(s, size)
    return size if w <= width else max(size * width / w, size * 0.5)


def render(snapshot: dict, plans: dict[int, op.Package] | None = None, exclude: dict | None = None) -> bytes:
    return Sheet(snapshot, plans=plans, exclude=exclude).build()
