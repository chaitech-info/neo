"""Qiymət təklifinin PDF-i — Seaport korporativ nahar menyusu dizaynında (A4, 3 səhifəyə yaxın).

Agentin təklif snapshot-undan (`offer_client.get_offer`) qurulur: üz qabığı (loqo, paket adı,
qısa təsvir), menyu səhifələri (gün-gün və ya bölmə-bölmə), "Gündəlik seçim" və "Təklif məlumatları".

Menyu iki cür ola bilər (Alətlər → Paketlər):
- həftəlik: bölmə adları həftənin günləridir ("Bazar ertəsi", "Çərşənbə axşamı", …), maddələr
  "Kateqoriya: yemək" şəklində ("Şorba: Mərcimək şorbası") — gün bloku kateqoriya sütunlarına bölünür;
- sadə: bölmələr kateqoriyadır ("Şorba", "Əsas yemək", …) — bir blok kimi göstərilir.

Şəkilli təklif (`render(snapshot, photos)`): sonda "Yemək şəkilləri" səhifələri — nümunə
SeaPort_MediumPlus_Menu_Dish_Photos.pdf kimi, kateqoriya-kateqoriya, iki sütunda böyük şəkil və altında ad.
Şəkli olmayan yemək bu səhifələrə düşmür (menyuda mətnlə qalır).

Paketin menyu planı varsa (app/package_menus.py), menyu plandan qurulur: yemək vaxtları, rotasiya həftələri
və yeməyin əlavə sahələri (porsiya, kalori …) adın altında kiçik yazı ilə.

Şrift Caladea-dır (nümunə PDF-dəki kimi); onda olmayan hərflər (ə, Ə, kiril) Noto Serif ilə yazılır.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

STATIC = Path(__file__).parent / "static"
W, H = A4
LEFT, RIGHT = 56.5, A4[0] - 56.5
COL2, COL3 = 198.0, 391.0           # sağ sütunlar (üz qabığı / bölmə başlıqları; gün blokunda 223 və 391)
DAY_COLS = (LEFT, 223.5, 391.0)
FOOTER_RULE, BODY_BOTTOM = 779.0, 760.0
LEAD = 15.8                         # menyu maddələrinin sətir aralığı
GAL_W, GAL_H, GAL_GAP = 232.0, 173.0, 18.0   # "Yemək şəkilləri": 2 sütun, şəkil 232×173 pt (nümunədəki kimi)
GAL_ROW = 204.0                              # şəkil + ad + boşluq
NOTE_LEAD = 11.0                             # yeməyin əlavə sahələri sətri

NAVY = HexColor("#1a365d")
TEAL = HexColor("#00a0b2")
GREY = HexColor("#6b7488")
RULE = HexColor("#dfe4eb")
LOGO_NAVY = HexColor("#2c3e86")

WEEKDAYS = [  # (az, en, ru)
    ("Bazar ertəsi", "Monday", "Понедельник"), ("Çərşənbə axşamı", "Tuesday", "Вторник"),
    ("Çərşənbə", "Wednesday", "Среда"), ("Cümə axşamı", "Thursday", "Четверг"), ("Cümə", "Friday", "Пятница"),
    ("Şənbə", "Saturday", "Суббота"), ("Bazar", "Sunday", "Воскресенье"),
]
GENERIC = {"digər", "diger", "other", "прочее", "əlavə", "elave", "extra"}

_fonts_ready = False


def _register_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    for name, file in (("Cal", "Caladea-Regular"), ("Cal-I", "Caladea-Italic"),
                       ("Noto", "NotoSerif-Regular"), ("Noto-I", "NotoSerif-Italic")):
        pdfmetrics.registerFont(TTFont(name, str(STATIC / "fonts" / f"{file}.ttf")))
    _fonts_ready = True


def _covered(font: str) -> set[int]:
    return set(pdfmetrics.getFont(font).face.charWidths)


# --- Mətn: Caladea + Noto Serif ehtiyat şrifti ---------------------------------------------------


class Text:
    """Caladea-da olmayan simvolları (ə, Ə, kiril) Noto Serif-ə keçirən kiçik mətn köməkçisi."""

    def __init__(self, c: Canvas):
        self.c = c
        self.cov = {"Cal": _covered("Cal"), "Cal-I": _covered("Cal-I")}

    def runs(self, s: str, italic: bool) -> list[tuple[str, str]]:
        main, alt = ("Cal-I", "Noto-I") if italic else ("Cal", "Noto")
        cov, out = self.cov[main], []
        for ch in s:
            f = main if ord(ch) in cov or ch == " " else alt
            if out and out[-1][0] == f:
                out[-1] = (f, out[-1][1] + ch)
            else:
                out.append((f, ch))
        return out

    def width(self, s: str, size: float, italic: bool = False, tracking: float = 0) -> float:
        return sum(pdfmetrics.stringWidth(t, f, size) for f, t in self.runs(s, italic)) + tracking * max(len(s) - 1, 0)

    def draw(self, x: float, y: float, s: str, size: float, color=NAVY, italic: bool = False,
             tracking: float = 0, align: str = "left") -> float:
        """y — yuxarıdan baza xətti. Eni qaytarır."""
        w = self.width(s, size, italic, tracking)
        if align == "right":
            x -= w
        elif align == "center":
            x -= w / 2
        t = self.c.beginText(x, H - y)
        t.setFillColor(color)
        t.setCharSpace(tracking)
        for f, chunk in self.runs(s, italic):
            t.setFont(f, size)
            t.textOut(chunk)
        self.c.drawText(t)
        return w

    def wrap(self, s: str, size: float, width: float, italic: bool = False) -> list[str]:
        lines, cur = [], ""
        for word in s.split():
            trial = f"{cur} {word}".strip()
            if cur and self.width(trial, size, italic) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines or [""]


# --- Məlumat: snapshot → səhifə modeli ------------------------------------------------------------


def _tr(value: Any, lang: str) -> str:
    if isinstance(value, dict):
        return str(value.get(lang) or value.get("az") or value.get("en") or next(iter(value.values()), "") or "").strip()
    return str(value or "").strip()


def _en(value: Any) -> str:
    return str(value.get("en") or "").strip() if isinstance(value, dict) else ""


def _weekday(title: Any) -> int | None:
    """Bölmə adı həftənin günüdürsə — günün indeksi. "Bazar ertəsi · 2-ci həftə" (rotasiya) da gün sayılır."""
    names = {v.split(" · ")[0].strip().casefold()
             for v in (title.values() if isinstance(title, dict) else [title]) if isinstance(v, str)}
    for i, day in enumerate(WEEKDAYS):
        if any(d.casefold() in names for d in day):
            return i
    return None


@dataclass
class Group:
    label: str            # "Şorba"; boş — kateqoriyasız
    items: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)   # yemək → "250 q · 420 kkal" (menyu planından)


@dataclass
class Block:
    title: str            # "Bazar ertəsi" / paket adı
    accent: str           # "Monday" / "Standard"
    groups: list[Group] = field(default_factory=list)


@dataclass
class Package:
    name: str
    name_en: str
    weekly: bool
    blocks: list[Block]
    price: str
    unit: str
    description: str
    days_span: tuple[str, str] | None   # ("Bazar ertəsi – Cümə", "Monday – Friday")
    daily: bool = True                  # "Gündəlik seçim" bloku (bir neçə yemək vaxtında qarışıq olur — göstərilmir)


_CAT_RE = re.compile(r"^\s*([^:]{2,40}):\s*(.+)$")


def _package(line: dict, lang: str, currency: str) -> Package:
    menu = [s for s in (line.get("menu") or []) if isinstance(s, dict)]
    days = [_weekday(s.get("title")) for s in menu]
    weekly = bool(menu) and all(d is not None for d in days)
    name, name_en = _tr(line.get("name"), lang) or str(line.get("package_id") or ""), _en(line.get("name"))
    blocks: list[Block] = []
    if weekly:
        for sec, d in zip(menu, days):
            groups: list[Group] = []
            for it in sec.get("items") or []:
                text = _tr(it, lang)
                m = _CAT_RE.match(text)
                label, dish = (m.group(1).strip(), m.group(2).strip()) if m else ("", text)
                g = next((g for g in groups if g.label == label), None)
                if g is None:
                    groups.append(g := Group(label))
                g.items.append(dish)
            day = WEEKDAYS[d]
            suffix = _tr(sec.get("title"), lang).partition(" · ")[2]
            title = (day[2] if lang == "ru" else day[0]) + (f" · {suffix}" if suffix else "")
            blocks.append(Block(title, day[1], groups))
    else:
        groups = [Group(_tr(s.get("title"), lang), [_tr(it, lang) for it in s.get("items") or [] if _tr(it, lang)])
                  for s in menu]
        blocks.append(Block(name, name_en if name_en.casefold() != name.casefold() else "", [g for g in groups if g.items]))
    span = None
    if weekly:
        first, last = WEEKDAYS[min(days)], WEEKDAYS[max(days)]
        span = (f"{first[0]} – {last[0]}", f"{first[1]} – {last[1]}")
    return Package(name=name, name_en=name_en, weekly=weekly, blocks=blocks,
                   price=_money(line.get("unit_price"), currency), unit=_tr(line.get("unit"), lang),
                   description=_tr(line.get("description"), lang), days_span=span)


def menu_items(snapshot: dict) -> list[tuple[str, str]]:
    """Təklifdəki bütün menyu maddələri: [(yemək, kateqoriya)] — şəkilləri tapmaq üçün."""
    lang = snapshot.get("language") if snapshot.get("language") in ("az", "en", "ru") else "az"
    out: list[tuple[str, str]] = []
    for line in snapshot.get("lines") or []:
        if isinstance(line, dict):
            for b in _package(line, lang, "AZN").blocks:
                out.extend((item, g.label) for g in b.groups for item in g.items)
    return out


def _cover_crop(data: bytes, w: float, h: float) -> ImageReader | None:
    """Şəkli w:h nisbətində mərkəzdən kəsib kiçildir (PDF yüngül qalsın)."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    ratio = w / h
    iw, ih = img.size
    if iw / ih > ratio:
        nw = int(ih * ratio)
        img = img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
    else:
        nh = int(iw / ratio)
        img = img.crop((0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh))
    img = img.resize((int(w * 2.6), int(h * 2.6)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80, optimize=True)
    out.seek(0)
    return ImageReader(out)


# Kateqoriya → "Yemək şəkilləri" bölməsinin adı (cəm) və ingiliscəsi
SECTION_NAMES = {
    "Şorba": ("Şorbalar", "Soups"), "Əsas yemək": ("Əsas yeməklər", "Main courses"), "Qarnir": ("Qarnirlər", "Side dishes"),
    "Salat": ("Salatlar", "Salads"), "Desert": ("Desertlər", "Desserts"), "İçki": ("İçkilər", "Drinks"),
    "Çörək": ("Çörək", "Bread"),
}


def section_name(label: str) -> tuple[str, str]:
    from app.dishes import category_name

    return SECTION_NAMES.get(category_name(label), (label or "Digər", ""))


def _money(value: Any, currency: str = "AZN") -> str:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ""
    s = f"{d:,.2f}".replace(",", " ")
    if s.endswith(".00"):
        s = s[:-3]
    return f"{s} {currency}".strip()


def _singular(label: str) -> str:
    low = label.lower()
    return re.sub(r"(lar|lər)$", "", low) if len(low) > 5 else low


def daily_selection(pkg: Package) -> list[tuple[int | None, str]]:
    """[(say, ad)] — say None: hər gün eyni olan maddə ("Çörək assortisi") mətnlə göstərilir."""
    out: list[tuple[int | None, str]] = []
    labels: list[str] = []
    for b in pkg.blocks:
        for g in b.groups:
            if g.label not in labels:
                labels.append(g.label)
    for label in labels:
        per_day = [next((g.items for g in b.groups if g.label == label), []) for b in pkg.blocks]
        same = all(items == per_day[0] for items in per_day)
        generic = label.casefold() in GENERIC or not label
        if (pkg.weekly and same) or generic:
            for item in per_day[0]:
                out.append((None, item))
        else:
            out.append((max(len(i) for i in per_day), _singular(label)))
    return out


def summary_line(pkg: Package) -> str:
    parts = [f"{n} {label}" if n else label[:1].lower() + label[1:] for n, label in daily_selection(pkg)]
    if not parts:
        return ""
    return ("Hər gün " if pkg.weekly else "") + ", ".join(parts) + "."


# --- Çəkmə ---------------------------------------------------------------------------------------


class Doc:
    def __init__(self, snapshot: dict, total: int = 0, photos: dict[str, bytes] | None = None,
                 plans: dict[int, Package] | None = None):
        """`plans` — {snapshot-dakı sətrin indeksi: menyu planından qurulmuş paket} (app/package_menus.py)."""
        _register_fonts()
        self.snap, self.total = snapshot, total
        self.photos = photos or {}
        self.lang = snapshot.get("language") if snapshot.get("language") in ("az", "en", "ru") else "az"
        self.brand = snapshot.get("brand") or "seaport"
        totals = snapshot.get("totals") or {}
        self.currency = totals.get("currency") or "AZN"
        lines = [l for l in snapshot.get("lines") or [] if isinstance(l, dict)]
        self.packages = [(plans or {}).get(i) or _package(l, self.lang, self.currency) for i, l in enumerate(lines)]
        self.weekly = bool(self.packages) and all(p.weekly for p in self.packages)
        self.buf = io.BytesIO()
        self.c = Canvas(self.buf, pagesize=A4)
        self.c.setTitle(self.title())
        self.c.setAuthor("Seaport Catering" if self.brand == "seaport" else "NAFA")
        self.t = Text(self.c)
        self.pages: list[Callable[[int, int], None]] = []
        self.y = 0.0

    def title(self) -> str:
        if len(self.packages) == 1 and self.packages[0].name_en:
            return f"{self.packages[0].name_en} Corporate Lunch Menu"
        return "Corporate Lunch Menu"

    def running_head(self) -> str:
        return "Həftəlik nahar menyusu" if self.weekly else "Nahar menyusu"

    def span(self) -> tuple[str, str] | None:
        spans = {p.days_span for p in self.packages if p.days_span}
        return spans.pop() if len(spans) == 1 else None

    # Səhifələr
    def finish_page(self, footer: Callable[[int, int], None]) -> None:
        self.pages.append(footer)
        footer(len(self.pages), self.total or len(self.pages))
        self.c.showPage()

    def logo(self, x: float, top: float, width: float, small: bool) -> float:
        if self.brand == "seaport":
            img = STATIC / "offer" / ("seaport-mark.png" if small else "seaport-logo.png")
            r = ImageReader(str(img))
            iw, ih = r.getSize()
            h = width * ih / iw
            self.c.drawImage(r, x, H - top - h, width, h, mask="auto")
            return h
        size = 15 if small else 34
        self.t.draw(x, top + size, "NAFA", size, NAVY)
        return size + 4

    def rule(self, y: float, color=RULE, width: float = 0.8, x1: float = LEFT, x2: float = RIGHT) -> None:
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x1, H - y, x2, H - y)

    def cover(self) -> None:
        t = self.t
        self.logo(LEFT - 1, 59, 136, small=False)
        if self.brand == "seaport":
            t.draw(LEFT + 67, 222, "Dadlı Həyatın Fəlsəfəsi", 12.5, LOGO_NAVY, align="center")
        if self.snap.get("example"):
            t.draw(RIGHT, 70, "NÜMUNƏ PAKET", 8, GREY, tracking=1.2, align="right")
        self.rule(351, TEAL, 0.9, LEFT, LEFT + 51)
        y = 410
        for line in t.wrap(self.title().upper(), 38, RIGHT - LEFT - 20):
            t.draw(LEFT - 2, y, line, 38, NAVY)
            y += 42
        y += 22
        t.draw(LEFT, y, "Weekly Executive Dining Proposal" if self.weekly else "Corporate Dining Proposal", 14.5,
               NAVY, italic=True)
        span = self.span()
        second = span[1] if span else (f"Prepared for {self.snap['customer_name']}" if self.snap.get("customer_name") else "")
        if second:
            t.draw(LEFT, y + 20, second, 14.5, GREY, italic=True)

        self.rule(647)
        y = 683
        if len(self.packages) == 1:
            p = self.packages[0]
            t.draw(LEFT, y, f"{p.name} paket", 14.5, TEAL)
            desc = p.description or (
                "Hər gün üçün ayrıca tərtib olunmuş, sevilən klassik yeməklərdən ibarət balanslı nahar menyusu. "
                "Komandanız üçün sadə, səliqəli və etibarlı gündəlik həll." if p.weekly else
                "Sevilən klassik yeməklərdən ibarət balanslı nahar menyusu. "
                "Komandanız üçün sadə, səliqəli və etibarlı gündəlik həll.")
            yy = y
            for line in t.wrap(desc, 11, 292)[:4]:
                t.draw(COL2, yy, line, 11, NAVY)
                yy += 16.5
            summary = summary_line(p)
            if summary:
                for line in t.wrap(summary, 8.5, 300)[:2]:
                    t.draw(COL2, yy + 10, line, 8.5, GREY)
                    yy += 12
        else:
            t.draw(LEFT, y, "Paketlər", 14.5, TEAL)
            yy = y
            for p in self.packages[:5]:
                t.draw(COL2, yy, p.name, 11, NAVY)
                if p.price:
                    t.draw(RIGHT, yy, f"{p.price} / {p.unit}" if p.unit else p.price, 11, GREY, align="right")
                yy += 16.5

        left, right = self.running_head(), (span[0] if span else (self.snap.get("customer_name") or ""))

        def footer(n: int, total: int) -> None:
            t.draw(LEFT, 795, left, 7.5, GREY, tracking=0.5)
            if right:
                t.draw(RIGHT, 795, right, 7.5, GREY, tracking=0.5, align="right")
        self.finish_page(footer)

    def page_header(self) -> None:
        self.logo(LEFT, 45, 47, small=True)
        self.t.draw(RIGHT, 69, self.running_head(), 8.5, GREY, italic=True, align="right")
        self.rule(83, NAVY, 0.7)
        self.y = 106.0

    def body_footer(self) -> Callable[[int, int], None]:
        label = self.title()

        def footer(n: int, total: int) -> None:
            self.rule(FOOTER_RULE)
            self.t.draw(LEFT, 801, label, 7.5, GREY, tracking=0.4)
            w = self.t.draw(RIGHT, 801, f" / {total:02d}", 7.5, GREY, align="right")
            self.t.draw(RIGHT - w, 801, f"{n:02d}", 10, NAVY, align="right")
        return footer

    def new_body_page(self) -> None:
        self.finish_page(self.body_footer())
        self.page_header()
        self.rule(self.y)

    def ensure(self, height: float) -> None:
        if self.y + height > BODY_BOTTOM:
            self.new_body_page()

    def section_title(self, eyebrow: str, title: str, right: str = "") -> None:
        self.t.draw(LEFT, self.y + 17, eyebrow, 11.5, TEAL, italic=True)
        if right:
            self.t.draw(RIGHT, self.y + 17, right, 8, GREY, tracking=0.6, align="right")
        self.t.draw(LEFT - 2, self.y + 66, title, 44, NAVY)
        self.y += 99
        self.rule(self.y)

    # Gün bloku: ad + 3 sütun kateqoriya
    def block_height(self, b: Block) -> float:
        cols = self.columns(b)
        return 51 + max((sum(self.group_height(g) for g in col) for col in cols), default=0) + 5

    def columns(self, b: Block) -> list[list[Group]]:
        cols: list[list[Group]] = [[], [], []]
        groups = b.groups
        if len(groups) <= 3:
            for i, g in enumerate(groups):
                cols[i].append(g)
            return cols
        # Nümunədəki kimi: 1-ci sütun — ilk iki qrup, 2-ci — növbəti iki, 3-cü — qalanı
        per = [2, 2, len(groups) - 4] if len(groups) <= 6 else [(len(groups) + 2) // 3] * 3
        i = 0
        for ci, n in enumerate(per):
            cols[ci] = groups[i:i + n]
            i += n
        cols[2].extend(groups[i:])
        return cols

    def item_rows(self, g: Group) -> list[tuple[list[str], list[str], float]]:
        """Hər maddə: (ad sətirləri, əlavə sahə sətirləri, hündürlük)."""
        rows = []
        for item in g.items:
            lines = self.t.wrap(item, 11, 150)
            note = g.notes.get(item, "")
            notes = self.t.wrap(note, 8.5, 150)[:2] if note else []
            rows.append((lines, notes, LEAD * len(lines) + NOTE_LEAD * len(notes)))
        return rows

    def group_height(self, g: Group) -> float:
        return (15 if g.label else 0) + sum(h for _, _, h in self.item_rows(g)) + 5

    def day_block(self, b: Block) -> None:
        self.ensure(self.block_height(b))
        t = self.t
        y = self.y + 29
        w = t.draw(LEFT - 1, y, b.title, 18.5, NAVY)
        if b.accent:
            t.draw(LEFT + w + 12, y, b.accent, 10.5, TEAL, italic=True)
        top = y + 22
        bottom = top
        for x, col in zip(DAY_COLS, self.columns(b)):
            yy = top
            for g in col:
                if g.label:
                    t.draw(x, yy, g.label, 8.5, GREY, italic=True)
                    yy += 15
                for lines, notes, _ in self.item_rows(g):
                    for line in lines:
                        t.draw(x, yy, line, 11, NAVY)
                        yy += LEAD
                    for line in notes:
                        t.draw(x, yy - 3, line, 8.5, GREY)
                        yy += NOTE_LEAD
                yy += 5
            bottom = max(bottom, yy)
        self.y = bottom + 5          # son maddənin bazası + 26pt (nümunədəki kimi)
        self.rule(self.y)

    # "Yemək şəkilləri" (nümunə: SeaPort_MediumPlus_Menu_Dish_Photos.pdf)
    def gallery_sections(self) -> list[tuple[str, str, list[tuple[str, bytes, str]]]]:
        """[(bölmə, ingiliscəsi, [(yemək, şəkil, əlavə sahələr)])] — yalnız şəkli olan yeməklər, menyudakı sırada."""
        from app.dishes import dish_key

        out: dict[str, tuple[str, str, list]] = {}
        seen: set[str] = set()
        for p in self.packages:
            for b in p.blocks:
                for g in b.groups:
                    az, en = section_name(g.label)
                    for item in g.items:
                        k = dish_key(item)
                        if k in seen or k not in self.photos:
                            continue
                        seen.add(k)
                        out.setdefault(az, (az, en, []))[2].append((item, self.photos[k], g.notes.get(item, "")))
        # Menyudakı sıra: şorba, əsas yemək, qarnir, salat …; tanınmayan kateqoriyalar sonda
        order = [v[0] for v in SECTION_NAMES.values()]
        return sorted(out.values(), key=lambda sec: order.index(sec[0]) if sec[0] in order else len(order))

    def gallery(self, header: str = "Yemək şəkilləri", close_current: bool = True) -> None:
        sections = self.gallery_sections()
        if not sections:
            return
        t = self.t
        running = self.running_head

        def page() -> None:
            self.logo(LEFT, 45, 50, small=True)
            t.draw(RIGHT, 66, header, 8.5, GREY, italic=True, align="right")
            self.rule(83, NAVY, 0.7)
            self.y = 100.0

        self.running_head = lambda: header     # type: ignore[method-assign]
        if close_current:
            self.finish_page(self.body_footer())
        page()
        first = True
        for az, en, dishes in sections:
            if not first and self.y + 46 + GAL_ROW > BODY_BOTTOM:
                self.finish_page(self.body_footer())
                page()
            head = self.y + (6 if first or self.y <= 100 else 10)
            w = t.draw(LEFT, head + 17.5, az, 17.5, NAVY)
            if en:
                t.draw(LEFT + w + 9, head + 17.5, en, 9.5, TEAL, italic=True)
            self.rule(head + 27)
            self.y = head + 46
            first = False
            for i in range(0, len(dishes), 2):
                if self.y + GAL_ROW - 20 > BODY_BOTTOM + 6:
                    self.finish_page(self.body_footer())
                    page()
                for j, (name, data, note) in enumerate(dishes[i:i + 2]):
                    x = LEFT + j * (GAL_W + GAL_GAP)
                    img = _cover_crop(data, GAL_W, GAL_H)
                    if img is not None:
                        self.c.drawImage(img, x, H - self.y - GAL_H, GAL_W, GAL_H)
                    line = t.wrap(name, 10.8, GAL_W)[0]
                    t.draw(x, self.y + GAL_H + 15, line, 10.8, NAVY)
                    if note:
                        t.draw(x, self.y + GAL_H + 27, t.wrap(note, 8.5, GAL_W)[0], 8.5, GREY)
                self.y += GAL_ROW
            self.y -= 4
        self.running_head = running             # type: ignore[method-assign]

    def daily(self, pkg: Package) -> None:
        sel = daily_selection(pkg) if pkg.daily else []
        if not sel:
            return
        rows = (len(sel) + 2) // 3
        self.ensure(40 + rows * 42)
        t = self.t
        top = self.y + 36
        t.draw(LEFT, top, "Gündəlik seçim", 15.5, NAVY)
        t.draw(LEFT, top + 16, "Daily selection", 8, GREY, tracking=0.5)
        xs = (COL2, COL2 + 114, COL2 + 228)
        for i, (n, label) in enumerate(sel):
            x, y = xs[i % 3], top + (i // 3) * 42
            if n is not None:
                t.draw(x, y + 6, str(n), 26, TEAL)
                for j, line in enumerate(t.wrap(label, 10.5, 75)[:2]):
                    t.draw(x + 30, y + 3 + j * 13, line, 10.5, NAVY)
            else:
                for j, line in enumerate(t.wrap(label, 10.5, 105)[:2]):
                    t.draw(x, y - 2 + j * 13, line, 10.5, NAVY)
        self.y = top + rows * 42 + 18
        self.rule(self.y)

    def field(self, x: float, label_y: float, line_y: float, width: float, label: str, label_en: str,
              value: str = "", note: str = "", value_size: float = 17) -> None:
        """Nümunədəki kimi: "Etiket / Label", altında dəyər və xətt (line_y — xəttin yeri)."""
        t = self.t
        w = t.draw(x, label_y, label, 8.5, NAVY)
        rest = f" / {label_en}" if label_en else ""
        if rest and w + t.width(rest, 8.5) > width:
            t.draw(x, label_y + 10.5, label_en, 8.5, GREY)       # uzun etiket — ingiliscəsi ikinci sətirdə
        elif rest:
            t.draw(x + w, label_y, rest, 8.5, GREY)
        if value:
            lines = t.wrap(value, value_size, width - 6)[:3]
            step = value_size + 2
            vy = line_y - 9 - (len(lines) - 1) * step
            vw = 0.0
            for k, line in enumerate(lines):
                vw = t.draw(x, vy + k * step, line, value_size, NAVY)
            if note:
                t.draw(x + vw + 10, vy + (len(lines) - 1) * step, note, 9.5, GREY, italic=True)
        self.rule(line_y, GREY, 0.8, x, x + width)

    def proposal(self) -> None:
        snap = self.snap
        totals = snap.get("totals") or {}
        notes = [n for n in ([snap.get("notes")] + list(snap.get("assumptions") or [])) if isinstance(n, str) and n.strip()]
        self.ensure(175)
        t = self.t
        r = self.y
        t.draw(LEFT, r + 38, "Təklif məlumatları", 15.5, NAVY)
        t.draw(LEFT, r + 54, "Proposal details", 8, GREY, tracking=0.5)
        wl, xr = 155, COL2 + 184
        customer = " ".join(x for x in (snap.get("customer_name"), snap.get("contact_person")) if x)
        self.field(COL2, r + 34, r + 72, wl, "Müştəri", "Client", customer, value_size=13)
        if len(self.packages) == 1:
            p = self.packages[0]
            unit_note = "gündəlik" if p.weekly or not p.unit else p.unit
            self.field(xr, r + 39, r + 72, RIGHT - xr, "Qiymət", "Price", p.price, unit_note)
        else:
            self.field(xr, r + 39, r + 72, RIGHT - xr, "Qiymət", "Price",
                       "; ".join(f"{p.name} {p.price}" for p in self.packages if p.price), value_size=11)
        if totals.get("total") not in (None, ""):
            vat = "ƏDV daxil" if totals.get("vat_included", True) or totals.get("vat") else "ƏDV-siz"
            self.field(COL2, r + 108, r + 156, wl, "Cəmi məbləğ", "Total", _money(totals.get("total"), self.currency),
                       vat, 13)
        else:
            self.field(COL2, r + 108, r + 156, wl, "Təklifin qüvvədə olma müddəti", "Proposal validity")
        self.field(xr, r + 117, r + 156, RIGHT - xr, "Əlavə şərtlər", "Additional terms", "; ".join(notes),
                   value_size=9.5)
        self.y = r + 170

    def build(self) -> bytes:
        self.cover()
        self.page_header()
        span = self.span()
        for i, p in enumerate(self.packages):
            if i:
                self.new_body_page()
            if self.weekly and len(self.packages) == 1:
                self.section_title("Weekly menu", "Həftəlik menyu", span[0] if span else "")
            elif p.weekly:
                self.section_title(p.name_en or "Weekly menu", f"{p.name} menyusu", p.days_span[0] if p.days_span else "")
            else:
                self.section_title("Menu", "Menyu" if len(self.packages) == 1 else f"{p.name} menyusu")
            for b in p.blocks:
                self.day_block(b)
            self.daily(p)
        self.proposal()
        if self.photos:
            self.gallery()
        self.finish_page(self.body_footer())
        self.c.save()
        return self.buf.getvalue()


def render(snapshot: dict, photos: dict[str, bytes] | None = None, plans: dict[int, Package] | None = None) -> bytes:
    """Agentin təklif snapshot-u → PDF baytları. `photos` — {dish_key: JPEG} (şəkilli təklif; app/dishes.py),
    `plans` — menyu planından qurulmuş paketlər (app/package_menus.py). Footer-də "02 / 03" üçün iki keçid."""
    first = Doc(snapshot, photos=photos, plans=plans)
    first.build()
    return Doc(snapshot, total=len(first.pages), photos=photos, plans=plans).build()


# --- Paketin menyu kitabçası (Alətlər → Paketlər → "PDF-ə bax") -----------------------------------


class MenuDoc(Doc):
    """Nümunə SeaPort_MediumPlus_Menu_Dish_Photos.pdf: üz qabığı, (istəyə görə) həftəlik menyu, yemək şəkilləri."""

    def __init__(self, pkg: Package, photos: dict[str, bytes], brand: str = "seaport", note: str = "",
                 schedule: bool = True, total: int = 0):
        super().__init__({"brand": brand, "language": "az", "lines": [{}]}, total=total, photos=photos, plans={0: pkg})
        self.note, self.schedule = note, schedule
        self.weekly = True

    def title(self) -> str:
        p = self.packages[0]
        return f"{p.name_en or p.name} Corporate Lunch Menu"

    def running_head(self) -> str:
        return "Həftəlik menyu" if self.schedule else "Yemək şəkilləri"

    def cover(self) -> None:
        t, p = self.t, self.packages[0]
        self.logo(LEFT, 61, 129, small=False)
        if self.brand == "seaport":
            t.draw(LEFT + 64, 222, "Dadlı Həyatın Fəlsəfəsi", 12.5, LOGO_NAVY, align="center")
        self.rule(351, TEAL, 2, LEFT, LEFT + 51)
        y = 402
        for line in t.wrap(self.title().upper(), 40, RIGHT - LEFT)[:3]:
            t.draw(LEFT, y, line, 40, NAVY)
            y += 44.5
        has_photos = bool(self.gallery_sections())
        en = ("Weekly Menu & Dish Photography" if self.schedule and has_photos else
              "Weekly Menu" if self.schedule else "Dish Photography")
        az = ("Həftəlik menyu və yemək şəkilləri" if self.schedule and has_photos else
              "Həftəlik menyu" if self.schedule else "Yemək şəkilləri")
        t.draw(LEFT, y - 2, en, 14, NAVY, italic=True)
        t.draw(LEFT, y + 19, az, 14, GREY, italic=True)
        self.rule(647.5)
        t.draw(LEFT, 677, f"{p.name} paket", 13, TEAL)
        sections = [section_name(g.label)[0].lower() for g in _unique_groups(p)]
        desc = p.description or (f"{p.name} menyusunda təqdim olunan yeməklər" +
                                 (": " + ", ".join(sections[:-1]) + (" və " if len(sections) > 1 else "") + sections[-1] + "."
                                  if sections else "."))
        yy = 678
        for line in t.wrap(desc, 10, 300)[:4]:
            t.draw(COL2, yy, line, 10, NAVY)
            yy += 15
        summary = summary_line(p)
        if summary:
            for line in t.wrap(summary, 8.5, 300)[:2]:
                t.draw(COL2, yy + 6, line, 8.5, GREY)
                yy += 12
        if self.note:
            t.draw(COL2, max(yy + 18, 731), t.wrap(self.note, 8, 300)[0], 8, GREY)
        span = p.days_span

        def footer(n: int, total: int) -> None:
            t.draw(LEFT, 795, self.running_head(), 7.5, GREY)
            if span:
                t.draw(RIGHT, 795, span[0], 7.5, GREY, align="right")
        self.finish_page(footer)

    def build(self) -> bytes:
        self.cover()
        p = self.packages[0]
        if self.schedule and p.blocks:
            self.page_header()
            self.section_title("Weekly menu", "Həftəlik menyu", p.days_span[0] if p.days_span else "")
            for b in p.blocks:
                self.day_block(b)
            self.daily(p)
            self.gallery()
            self.finish_page(self.body_footer())
        elif self.gallery_sections():
            self.gallery(close_current=False)
            self.finish_page(self.body_footer())
        self.c.save()
        return self.buf.getvalue()


def _unique_groups(p: Package) -> list[Group]:
    out: list[Group] = []
    for b in p.blocks:
        for g in b.groups:
            if all(section_name(x.label) != section_name(g.label) for x in out):
                out.append(g)
    return out


def render_menu(pkg: Package, photos: dict[str, bytes], brand: str = "seaport", note: str = "",
                schedule: bool = True) -> bytes:
    first = MenuDoc(pkg, photos, brand, note, schedule)
    first.build()
    return MenuDoc(pkg, photos, brand, note, schedule, total=len(first.pages)).build()
