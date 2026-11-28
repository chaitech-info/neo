"""Albom (üfüqi A4) qiymət təklifi — "Albom" dizaynı (offer_pdf.DESIGNS["sheet"]).

Quruluş Askar-ın göndərdiyi nümunə qiymət təklifindəndir (SP-20261021-002), görünüş Seaport brendbukuna görədir
(SeaPort_brand_guideline.pdf):
- rənglər: əsas — tünd göy #15284b, göy #446ca9 (ikisi qradiyent kimi, tünd fonlar) və firuzəyi #0096a9 (vurğu);
  köməkçi çəhrayı #ea1d76 yalnız kiçik yerdə ("NÜMUNƏ" su nişanı);
- loqo: rəngli fonda ağ loqo (rəngli loqodan qurulur — `white_logo`), ətrafında heç bir lövhə / çərçivə yoxdur,
  naxışın üstünə düşmür, ətrafında boş yer saxlanılır; başlıq zolağında ağ yazı loqosu (seaport-mark);
- şrift: brendbukda Cambria-dır; o Microsoft şriftidir və serverə qoyula bilməz, onun açıq əkizi Caladea-da isə
  "ə" yoxdur — ona görə bütün mətn eyni üslublu Noto Serif-dir (adi, qalın, kursiv);
- naxış: brendin "S" dalğa naxışı (`pattern`) — qradiyent fonda firuzəyi, loqodan aralı.

Səhifələr:
1. Üz qabığı — solda qradiyent panel (ağ loqo, sloqan, "Qiymət təklifi", aşağıda naxış), sağda müştəri, salam
   mətni, təklif nömrəsi və tarix.
2. "Menyu paketləri" — hər paket öz kartında (qiymət, menyu / gündəlik seçim), yanda keyfiyyət sertifikatları
   və şirkətin əlaqə məlumatları (yalnız Seaport).
3. "Həftəlik menyu" — nümunədə olmayan hissə: həftəlik paketin hər günü öz kartında (kateqoriya → yeməklər).
4. "Qiymət xülasəsi" — paket / vahid qiymət / miqdar / cəmi cədvəli, yekun kartı, təşəkkür və əlaqə.

Paket modeli, menyu planı və "menyudan çıxart" offer_pdf-dəki kimidir (`offer_pdf._package`, `without`).
Şəkilli təklifdə də yemək şəkilləri bu dizayna düşmür — həftəlik menyu mətnlə qalır.
"""
from __future__ import annotations

import functools
import io
import re
from datetime import date
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from app import offer_pdf as op

W, H = landscape(A4)
M = 40.0                      # sol / sağ kənar
PANEL = 300.0                 # üz qabığında sol panelin eni
HEAD = 58.0                   # məzmun səhifələrinin yuxarı zolağı
TOP, BOTTOM = 84.0, H - 62.0  # məzmunun yeri
GAP = 14.0

# Brendbukun korporativ rəngləri
NAVY = HexColor("#15284b")        # Pantone 2767 C — əsas, qradiyentin tünd ucu, mətn
BLUE = HexColor("#446ca9")        # Pantone 7683 C — əsas, qradiyentin açıq ucu
TEAL = HexColor("#0096a9")        # Pantone 7711 C — əsas, vurğu
PINK = HexColor("#ea1d76")        # Pantone 213 C — köməkçi
# Neytral və tünd fonda açıq tonlar (korporativ rənglərin açıq çalarları)
INK = NAVY
MUTED = HexColor("#5b6680")
LINE = HexColor("#dde3ee")
SOFT = HexColor("#eef2f8")
ON_DARK = HexColor("#b9cbe8")     # qradiyent fonda köməkçi mətn
TEAL_LIGHT = HexColor("#7fd0db")  # qradiyent fonda vurğu
WHITE = op.WHITE

COMPANY = {
    "seaport": ("Seaport Catering MMC",
                ("Zahid Xəlilov küçəsi, 576-cı məhəllə", "Bakı, AZ1141; “Renessans Palace” 17-ci mərtəbə"),
                "(+994) 50 254 51 64", "info@seaport-catering.az · seaport-catering.az"),
}
CERTS = {"seaport": ("HACCP", "ISO 22000:2018", "ISO 9001:2015", "ISO 45001:2018")}

# Sabit mətnlər: (az, en, ru)
T = {
    "offer": ("Qiymət təklifi", "Price offer", "Ценовое предложение"),
    "slogan": ("Dadlı Həyatın Fəlsəfəsi", "The Philosophy of Tasty Life", "Философия вкусной жизни"),
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


@functools.lru_cache(maxsize=4)
def white_logo(name: str) -> ImageReader:
    """Brendbukdakı "ağ loqo": rəngli loqonun (ağ fonda) forması ağ rəngdə, fon şəffaf. Rəngli hissələr ağ olur,
    ağ hissələr (fon, "p"-nin içindəki yarpaq) şəffaf — tünd fonda brendbukdakı kimi görünür."""
    from PIL import Image, ImageChops

    img = Image.open(op.STATIC / "offer" / name).convert("RGBA")
    r, g, b, a = img.split()
    ink = ImageChops.darker(ImageChops.darker(r, g), b).point(lambda v: 255 - v)   # ağdan uzaqlıq
    alpha = ImageChops.multiply(ink, a).point(lambda v: min(255, int(v * 1.35)))
    out = Image.new("RGBA", img.size, (255, 255, 255, 0))
    out.putalpha(alpha)
    return ImageReader(out)


def op_fit(t: op.Text, s: str, size: float, width: float) -> float:
    """Başlıq sığmırsa kiçilir (ən çox yarıya qədər)."""
    w = t.width(s, size)
    return size if w <= width else max(size * width / w, size * 0.5)


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
        # Brendbukun şrifti (Cambria) əvəzinə Noto Serif: adi və qalın
        self.t = op.Text(self.c, False, INK)
        self.b = op.Text(self.c, False, INK, bold=True)
        self.t.page_h = self.b.page_h = H     # üfüqi səhifə: mətnin yeri bu hündürlükdən hesablanır
        self.page_no = 0
        self.y = TOP

    # --- köməkçilər -------------------------------------------------------------------------------
    def tx(self, key: str) -> str:
        return T[key][self.li]

    def pair(self, key: str) -> str:
        """Etiket təklifin dilində, yanında ingiliscə: "Tarix / Date" (ingiliscə təklifdə — yalnız "Date")."""
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

    def clip(self, x: float, top: float, w: float, h: float, r: float = 0) -> None:
        """Sonrakı çəkilişi düzbucaqlının (r — yumru künc) içi ilə məhdudlaşdırır (saveState-dən sonra)."""
        path = self.c.beginPath()
        if r:
            path.roundRect(x, H - top - h, w, h, r)
        else:
            path.rect(x, H - top - h, w, h)
        self.c.clipPath(path, stroke=0, fill=0)

    def gradient(self, x: float, top: float, w: float, h: float, r: float = 0) -> None:
        """Brendbukun qradiyenti: sol-aşağıda tünd göy #15284b → sağ-yuxarıda göy #446ca9."""
        c = self.c
        c.saveState()
        self.clip(x, top, w, h, r)
        c.linearGradient(x, H - top - h, x + w, H - top, (NAVY, BLUE), extend=True)
        c.restoreState()

    def pattern(self, x: float, top: float, w: float, h: float, alpha: float = 0.5, size: float = 11.0) -> None:
        """Brendin "S" dalğa naxışı (brendbuk: "Naxış rəngli fonda") — firuzəyi xətlər, verilən sahəyə kəsilir."""
        c = self.c
        c.saveState()
        self.clip(x, top, w, h)
        c.setStrokeColor(TEAL)
        c.setStrokeAlpha(alpha)
        c.setLineWidth(size * 0.11)
        c.setLineCap(1)
        r = size / 2
        dx, dy = size * 2.2, size * 1.9
        row = 0
        yy = H - top - h - size
        while yy < H - top + size:
            xx = x - size + (dx / 2 if row % 2 else 0)
            while xx < x + w + size:
                # "S": yuxarı halqa (sağa açılır) + aşağı halqa (sola açılır), uclarında kiçik qıvrım
                c.arc(xx - r, yy, xx + r, yy + 2 * r, 0, 270)
                c.arc(xx - r, yy - 2 * r, xx + r, yy, 180, 270)
                c.arc(xx + r, yy + r * 0.5, xx + 2 * r, yy + r * 1.5, 180, 180)
                c.arc(xx - 2 * r, yy - r * 1.5, xx - r, yy - r * 0.5, 0, 180)
                xx += dx
            yy += dy
            row += 1
        c.restoreState()

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

    def logo_white(self, cx: float, top: float, width: float, mark: bool = False) -> float:
        """Ağ loqo tünd fonda (brendbuk: "Ağ qara logo"; `mark` — yalnız yazı hissəsi). Hündürlüyü qaytarır."""
        if self.brand != "seaport":
            self.b.draw(cx, top + width * 0.3, "NAFA", width * 0.32, WHITE, align="center")
            return width * 0.4
        img = white_logo("seaport-mark.png" if mark else "seaport-logo.png")
        iw, ih = img.getSize()
        h = width * ih / iw
        self.c.drawImage(img, cx - width / 2, H - top - h, width, h, mask="auto")
        return h

    def watermark(self) -> None:
        if not self.snap.get("example"):
            return
        c = self.c
        c.saveState()
        c.setFillColor(PINK)
        c.setFillAlpha(0.06)
        c.translate(W / 2, H / 2 - 30)
        c.rotate(28)
        c.setFont("Noto-B", 120)
        c.drawCentredString(0, 0, self.tx("sample"))
        c.restoreState()

    def eyebrow(self, x: float, y: float, text: str, color: Any = TEAL, size: float = 7.5,
                align: str = "left") -> None:
        self.b.draw(x, y, text, size, color, tracking=1.4, align=align)

    # --- səhifələr --------------------------------------------------------------------------------
    def cover(self) -> None:
        t, b = self.t, self.b
        self.fill(WHITE, 0, 0, W, H)
        self.gradient(0, 0, PANEL, H)
        self.pattern(0, H - 150, PANEL, 150)                # naxış panelin aşağısında, loqodan aralı
        # Ağ loqo + sloqan (brendbuk: "Loqo sloqan ilə ağ"), ətrafında boş yer
        lh = self.logo_white(PANEL / 2, 52, 128)
        if self.brand == "seaport":
            t.draw(PANEL / 2, 52 + lh + 26, self.tx("slogan"), 12, WHITE, align="center")
        y = 318.0
        for line in b.wrap(self.tx("offer"), 28, PANEL - 2 * M)[:2]:
            b.draw(M, y, line, 28, WHITE)
            y += 33
        if self.lang != "en":
            t.draw(M, y, T["offer"][1], 12.5, ON_DARK, italic=True)
            y += 18
        self.fill(TEAL, M, y + 12, 52, 3)

        x0 = PANEL + 58
        width = W - M - x0
        self.eyebrow(x0, 112, self.up_pair("for"))
        y = 160.0
        name = self.customer or "—"
        size = 32.0
        lines = b.wrap(name, size, width)
        if len(lines) > 2:
            size = 25.0
            lines = b.wrap(name, size, width)[:3]
        for line in lines:
            b.draw(x0, y, line, size, INK)
            y += size + 7
        self.fill(TEAL, x0, y - 4, 46, 3)
        y += 34
        if self.customer:
            for line in t.wrap(_greeting(self.customer, self.lang), 11.5, min(width, 400))[:5]:
                t.draw(x0, y, line, 11.5, MUTED)
                y += 19
        if self.packages:
            y += 14
            for p in self.packages[:3]:
                if y > 405:
                    break
                num, cur = self.price_parts(p)
                b.draw(x0, y, p.name, 11, INK)
                t.draw(x0 + min(width, 400), y, f"{num} {cur}".strip() + (f" / {p.unit}" if p.unit else ""),
                       11, TEAL, align="right")
                y += 18

        self.rule(H - 140, x0, W - M)
        fields = [("no", self.offer_id or "—"), ("date", _offer_date(self.offer_id, self.snap))]
        spans = {p.days_span for p in self.packages if p.days_span}
        if len(spans) == 1:
            fields.append(("days", spans.pop()[0]))
        col = width / len(fields)
        for i, (key, value) in enumerate(fields):
            x = x0 + i * col
            t.draw(x, H - 112, self.pair(key), 7.5, MUTED, tracking=0.3)
            b.draw(x, H - 92, value, op_fit(b, value, 14, col - 12), INK)
        self.watermark()
        self.c.showPage()

    def header(self) -> None:
        self.page_no += 1
        self.fill(WHITE, 0, 0, W, H)
        self.gradient(0, 0, W, HEAD)
        self.fill(TEAL, 0, HEAD, W, 3)
        self.logo_white(M + 34, 12, 68, mark=True)           # ağ yazı loqosu (brendbuk: "Ağ qara loqo yazı hissəsi")
        head = self.tx("offer") + (f" · {self.offer_id}" if self.offer_id else "")
        self.t.draw(W - M, HEAD / 2 + 4, head, 10, WHITE, align="right")
        self.y = TOP

    def footer(self) -> None:
        t = self.t
        self.rule(H - 46, M, W - M, TEAL, 0.9)
        if self.company:
            name, address, phone, mail = self.company
            t.draw(M, H - 32, f"{name} · {address[0]}; {address[1]}", 7, MUTED)
            t.draw(M, H - 21, f"{phone} · {mail}", 7, MUTED)
        t.draw(W - M, H - 32, f"{self.tx('page')} {self.page_no + 1}", 7, MUTED, align="right")
        if self.offer_id:
            t.draw(W - M, H - 21, self.offer_id, 7, MUTED, align="right")
        self.watermark()
        self.c.showPage()

    def title(self, key: str, suffix: str = "", right: str = "") -> None:
        y = self.y
        self.b.draw(M, y + 22, self.tx(key) + (f" · {suffix}" if suffix else ""), 22, INK)
        if self.lang != "en":
            self.t.draw(M, y + 38, T[key][1], 9, MUTED, italic=True)
        if right:
            self.b.draw(W - M, y + 22, right, 9.5, TEAL, align="right", tracking=0.3)
        self.y = y + 56

    def card(self, x: float, top: float, w: float, h: float, head: float, r: float = 10) -> None:
        """Ağ kart, yuxarıda qradiyent başlıq və firuzəyi xətt (künclər yumru)."""
        c = self.c
        self.fill(LINE, x + 1, top + 2, w, h, r)
        self.fill(WHITE, x, top, w, h, r)
        c.saveState()
        self.clip(x, top, w, h, r)
        c.linearGradient(x, H - top - head, x + w, H - top, (NAVY, BLUE), extend=True)
        c.restoreState()
        c.saveState()
        self.clip(x, top, w, h, r)
        self.fill(WHITE, x, top + head, w, h - head)
        self.fill(TEAL, x, top + head, w, 3)
        c.restoreState()

    # --- "Menyu paketləri" -------------------------------------------------------------------------
    def card_rows(self, p: op.Package, width: float) -> list[tuple[str, str]]:
        """Kartın gövdəsi: [("label", "Şorba") | ("item", "Mərci şorbası") | ("cont", …) | ("note", "...")]."""
        rows: list[tuple[str, str]] = []
        if p.weekly:
            rows.append(("label", self.tx("daily")))
            for n, label in op.daily_selection(p, self.lang):
                rows.append(("item", f"{n} {label}" if n else label))
            rows.append(("note", self.tx("see_week")))
            return rows
        for blk in p.blocks:
            for g in blk.groups:
                if g.label:
                    rows.append(("label", g.label))
                for item in g.items:
                    for k, line in enumerate(self.t.wrap(item, 9.5, width - 14)):
                        rows.append(("item" if k == 0 else "cont", line))
        return rows

    @staticmethod
    def row_height(kind: str) -> float:
        return {"label": 22.0, "item": 15.5, "cont": 13.0, "note": 26.0}[kind]

    def package_card(self, i: int, p: op.Package, x: float, top: float, w: float, h: float,
                     rows: list[tuple[str, str]]) -> None:
        c, t, b = self.c, self.t, self.b
        self.card(x, top, w, h, 74, 12)
        self.eyebrow(x + 20, top + 25, f"{self.up(self.tx('package'))} {i + 1}", TEAL_LIGHT)
        b.draw(x + 20, top + 56, p.name, op_fit(b, p.name, 21, w - 40), WHITE)
        num, cur = self.price_parts(p)
        nw = b.draw(x + 20, top + 124, num, 30, INK)
        if cur:
            b.draw(x + 28 + nw, top + 108, cur, 10, INK)
        if p.unit:
            t.draw(x + 28 + nw, top + 122, f"/ {p.unit}", 8.5, MUTED)
        y = top + 140
        if p.description:
            for line in t.wrap(p.description, 8, w - 40)[:2]:
                t.draw(x + 20, y, line, 8, MUTED)
                y += 11
        y += 4
        self.rule(y, x + 20, x + w - 20)
        y += 8
        limit = top + h - 12
        for kind, text in rows:
            step = self.row_height(kind)
            if y + step > limit:
                t.draw(x + 34, y + 12, "…", 9.5, MUTED)
                break
            if kind == "label":
                self.eyebrow(x + 20, y + 16, self.up(text), size=7)
            elif kind == "note":
                for j, line in enumerate(t.wrap(text, 8, w - 40)[:2]):
                    t.draw(x + 20, y + 16 + j * 10, line, 8, MUTED, italic=True)
            else:
                if kind == "item":
                    c.setFillColor(TEAL)
                    c.circle(x + 23, H - (y + 9), 1.8, stroke=0, fill=1)
                t.draw(x + 33, y + 12, text, 9.5, INK)
            y += step

    def side_column(self, x: float, top: float, w: float) -> None:
        t, b = self.t, self.b
        y = top
        certs = CERTS.get(self.brand)
        if certs:
            h = 150.0
            self.fill(SOFT, x, y, w, h, 12)
            b.draw(x + 18, y + 34, self.tx("quality"), op_fit(b, self.tx("quality"), 14, w - 36), INK)
            cx, cy = x + 18, y + 54
            for cert in certs:
                cw = b.width(cert, 8.5) + 22
                if cx + cw > x + w - 14:
                    cx, cy = x + 18, cy + 32
                self.c.setStrokeColor(NAVY)
                self.c.setLineWidth(0.9)
                self.c.roundRect(cx, H - cy - 22, cw, 22, 11, stroke=1, fill=0)
                b.draw(cx + cw / 2, cy + 14.5, cert, 8.5, NAVY, align="center")
                cx += cw + 8
            y += h + GAP
        self.contact_card(x, y, w)

    def contact_card(self, x: float, top: float, w: float) -> float:
        if not self.company:
            return top
        t, b = self.t, self.b
        name, address, phone, mail = self.company
        h = 112.0
        self.fill(SOFT, x, top, w, h, 12)
        b.draw(x + 18, top + 28, name, 13.5, INK)
        y = top + 46
        for line in address:
            t.draw(x + 18, y, line, 8, MUTED)
            y += 12
        b.draw(x + 18, y + 6, phone, 9, INK)
        t.draw(x + 18, y + 22, mail, 8, MUTED)
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
    DAY_STEP = {"label": 18.0, "item": 13.5, "cont": 11.5, "dnote": 10.0}

    def day_rows(self, blk: op.Block, width: float) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for g in blk.groups:
            if g.label:
                rows.append(("label", g.label))
            for item in g.items:
                for k, line in enumerate(self.t.wrap(item, 8.8, width)):
                    rows.append(("item" if k == 0 else "cont", line))
                note = g.notes.get(item, "")
                if note:
                    for line in self.t.wrap(note, 7, width)[:2]:
                        rows.append(("dnote", line))
        return rows

    def day_card(self, blk: op.Block, x: float, top: float, w: float, h: float, rows: list[tuple[str, str]]) -> None:
        c, t, b = self.c, self.t, self.b
        self.card(x, top, w, h, 40)
        title, _, week = blk.title.partition(" · ")
        b.draw(x + 12, top + 18, title, op_fit(b, title, 10.5, w - 24), WHITE)
        sub = " · ".join(v for v in (blk.accent if self.lang != "en" else "", week) if v)
        if sub:
            t.draw(x + 12, top + 32, sub, 7.5, ON_DARK, italic=True)
        y = top + 48
        for kind, text in rows:
            step = self.DAY_STEP[kind]
            if y + step > top + h - 4:       # səhifəyə sığmayan (çox uzun) gün — kartın içində kəsilir
                t.draw(x + 20, y + 8, "…", 8.8, MUTED)
                break
            if kind == "label":
                self.eyebrow(x + 12, y + 13, self.up(text), size=6.5)
            elif kind == "dnote":
                t.draw(x + 20, y + 7, text, 7, MUTED)
            else:
                if kind == "item":
                    c.setFillColor(TEAL)
                    c.circle(x + 14, H - (y + 7.2), 1.5, stroke=0, fill=1)
                t.draw(x + 20, y + 10, text, 8.8, INK)
            y += step

    def weekly_pages(self) -> None:
        weekly = [p for p in self.packages if p.weekly and p.blocks]
        for p in weekly:
            n = len(p.blocks)
            cols = 5 if n in (5, 10) or n > 7 else min(n, 4) if n != 6 else 3
            w = (W - 2 * M - GAP * (cols - 1)) / cols
            rows = [self.day_rows(blk, w - 32) for blk in p.blocks]
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
        t, b = self.t, self.b
        self.header()
        self.title("summary")
        top = self.y
        tw = 470.0
        x1, x2 = M, M + tw
        cols = (x1 + 18, x1 + 205, x1 + 300, x2 - 18)    # paket | vahid qiymət (sağa) | miqdar (orta) | cəmi (sağa)
        self.fill(SOFT, x1, top, tw, 32, 8)
        hy = top + 20
        self.eyebrow(cols[0], hy, self.up(self.tx("package")), MUTED, 7)
        self.eyebrow(cols[1], hy, self.up(self.tx("unit_price")), MUTED, 7, align="right")
        self.eyebrow(cols[2], hy, self.up(self.tx("qty")), MUTED, 7, align="center")
        self.eyebrow(cols[3], hy, self.up(self.tx("sum")), MUTED, 7, align="right")
        y = top + 32
        for k, (p, line) in enumerate(zip(self.packages, self.lines)):
            if y + 44 > BOTTOM - 170:
                break
            if k % 2 == 1:
                self.fill(HexColor("#f7f9fc"), x1, y, tw, 44)
            num, cur = self.price_parts(p)
            qty, total = self.quantity(line)
            b.draw(cols[0], y + 27, p.name, op_fit(b, p.name, 13, 112), INK)
            t.draw(cols[1], y + 27, f"{num} {cur}".strip(), 10.5, INK, align="right")
            t.draw(cols[2], y + 27, qty, 9.5, MUTED, align="center")
            b.draw(cols[3], y + 27, total, 10.5, INK, align="right")
            y += 44
        self.rule(y, x1, x2)
        vat_in = self.totals.get("vat_included") is True
        key = "vat_in" if vat_in else "vat_out"
        b.draw(x2, y + 22, " / ".join(dict.fromkeys((T[key][self.li], T[key][1]))), 8.5, TEAL, align="right")
        t.draw(x2, y + 36, self.tx("per_person"), 8, MUTED, align="right", italic=True)

        # Yekun kartı: qradiyent, aşağısında naxış
        kx, kw, kh = x2 + 30, W - M - x2 - 30, 230.0
        self.gradient(kx, top, kw, kh, 14)
        self.c.saveState()
        self.clip(kx, top, kw, kh, 14)
        self.pattern(kx, top + kh - 64, kw, 64, alpha=0.4)
        self.c.restoreState()
        self.eyebrow(kx + 24, top + 40, self.up(self.tx("grand")), TEAL_LIGHT)
        total = self.totals.get("total")
        if total not in (None, ""):
            b.draw(kx + 24, top + 86, self.money(total), op_fit(b, self.money(total), 28, kw - 48), WHITE)
            yy = top + 116
            for key, value in (("net", self.totals.get("subtotal")), ("vat", self.totals.get("vat"))):
                if value not in (None, ""):
                    t.draw(kx + 24, yy, self.tx(key), 9.5, ON_DARK)
                    t.draw(kx + kw - 24, yy, self.money(value), 9.5, WHITE, align="right")
                    yy += 18
        else:
            yy = top + 76
            for line in t.wrap(self.tx("later"), 13, kw - 48)[:4]:
                t.draw(kx + 24, yy, line, 13, WHITE)
                yy += 20

        # Təşəkkür + əlaqə
        by = BOTTOM - 118
        cw = 250.0 if self.company else 0.0
        tw2 = W - 2 * M - (cw + 30 if cw else 0)
        yy = by + 26
        for line in b.wrap(self.tx("thanks"), 18, tw2)[:2]:
            b.draw(M, yy, line, 18, INK)
            yy += 24
        for line in t.wrap(self.tx("ask"), 10.5, tw2)[:2]:
            t.draw(M, yy + 4, line, 10.5, MUTED)
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


def render(snapshot: dict, plans: dict[int, op.Package] | None = None, exclude: dict | None = None) -> bytes:
    return Sheet(snapshot, plans=plans, exclude=exclude).build()
