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

Şrift Noto Serif-dir (bax `Text`); Caladea yalnız ehtiyatdır.

Dizaynlar (`DESIGNS`, istifadəçi formda seçir): klassik, gecə, yaşıl, isti, minimal — məzmun və düzüm eynidir,
rənglər, şrift (Noto Serif / Noto Sans) və səhifə bəzəyi fərqlidir. "Təklif məlumatları"nda həmişə yazılır:
qiymət 1 nəfərlik yemək üçündür və ƏDV qiymətə daxil deyil (agent ƏDV-ni daxil sayıbsa — daxildir).
"""
from __future__ import annotations

import functools
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from reportlab import rl_config
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

STATIC = Path(__file__).parent / "static"
# rl_accel (C) quraşdırılmayıb: şəkillərin ASCII85 kodlaması saf Python-da gedir və Vercel-də şəkilli PDF-in
# vaxtının yarısını aparırdı. Binar axın etibarlı PDF-dir, həm də ~25% kiçikdir.
rl_config.useA85 = 0
W, H = A4
LEFT, RIGHT = 56.5, A4[0] - 56.5
COL2, COL3 = 198.0, 391.0           # sağ sütunlar (üz qabığı / bölmə başlıqları; gün blokunda 223 və 391)
DAY_COLS = (LEFT, 223.5, 391.0)
FOOTER_RULE, BODY_BOTTOM = 779.0, 760.0
COVER_FILL_TOP = 292.0              # tünd üz qabığı: bu xətdən aşağı rəngli fon, yuxarıda loqo ağ fonda
CENTER_BLOCK = 250.0                # mərkəzləşmiş üz qabığı: yuxarıdakı rəngli blokun hündürlüyü
SPLIT_PANEL = 214.0                 # panelli üz qabığı: sol panelin eni
SPLIT_X = SPLIT_PANEL + 34          # panelli üz qabığı: sağ hissədə mətnin sol kənarı
LEAD = 15.8                         # menyu maddələrinin sətir aralığı
GAL_W, GAL_H, GAL_GAP = 232.0, 173.0, 18.0   # "Yemək şəkilləri": 2 sütun, şəkil 232×173 pt (nümunədəki kimi)
GAL_ROW = 204.0                              # şəkil + ad + boşluq
NOTE_LEAD = 11.0                             # yeməyin əlavə sahələri sətri

LOGO_NAVY = HexColor("#2c3e86")
WHITE = HexColor("#ffffff")
PANEL_SOFT = HexColor("#c3cde6")    # tünd göy panelin üstündə köməkçi mətn


@dataclass(frozen=True)
class Palette:
    ink: Any       # əsas mətn
    accent: Any    # vurğu (bölmə adları, rəqəmlər)
    muted: Any     # köməkçi mətn
    line: Any      # ayırıcı xətlər


def _pal(ink: str, accent: str, muted: str, line: str) -> Palette:
    return Palette(HexColor(ink), HexColor(accent), HexColor(muted), HexColor(line))


@dataclass(frozen=True)
class Design:
    """Təklif PDF-inin dizaynı (istifadəçi Qiymət təklifi formunda seçir). Məzmun eynidir; dizaynlar rənglə
    yanaşı quruluşla da fərqlənir: üz qabığının düzümü (`cover_style`) və gün bloklarının görünüşü (`day_style`).
    Yeni dizaynlarda yalnız Seaport loqosunun rəngləri işlənir (LOGO_* sabitləri) + ağ və neytral boz."""
    code: str
    name: str                      # formdakı ad
    hint: str                      # formdakı qısa izah
    body: Palette
    sans: bool = False             # Noto Sans (yoxsa Noto Serif)
    cover: Palette | None = None   # üz qabığının rəngləri (tünd fonda açıq mətn); None — body
    cover_fill: Any = None         # "left": üz qabığının aşağı hissəsinin fonu (loqo ağ hissədə qalır)
    foot_fill: Any = None          # menyu səhifələrinin alt zolağı (səhifə nömrəsi onun üstündə)
    cover_style: str = "left"      # "left" — sola düzülmüş (klassik); "center" — mərkəzdə; "split" — sol panel
    cover_block: Any = None        # "center": yuxarıda rəngli blok (loqo ağ lövhədə onun üstündə)
    panel: Any = None              # "split": sol panelin rəngi; menyu səhifələrində nazik yan zolaq
    stripe: tuple = ()             # səhifənin yuxarısında (üz qabığında həm də aşağıda) rəngli zolaq
    day_style: str = "plain"       # gün bloku: "plain" — ad + xətt; "bar" — rəngli zolaqda ad;
    #                                "card" — açıq fonlu yumru kart; "side" — solda rəngli şaquli xətt
    day_colors: tuple = ()         # "side": günlərin növbə ilə rəngləri
    card_fill: Any = None          # "card": kartın fonu
    swatch: tuple[str, str] = ("#ffffff", "#000000")   # formdakı nümunə rənglər (fon, vurğu)


# Seaport loqosunun rəngləri (app/static/offer/seaport-logo.png-dən ölçülüb)
LOGO_DEEP, LOGO_BLUE, LOGO_TEAL, LOGO_PURPLE, LOGO_PINK = "#212c59", "#30417d", "#0999ad", "#7c4182", "#e92478"

DESIGNS: dict[str, Design] = {d.code: d for d in (
    Design("classic", "Klassik", "tünd göy və firuzəyi, sola düzülmüş",
           _pal("#1a365d", "#00a0b2", "#6b7488", "#dfe4eb"), swatch=("#1a365d", "#00a0b2")),
    Design("midnight", "Gecə", "tünd üz qabığı, qızılı vurğu",
           _pal("#16233a", "#a9812f", "#6c7484", "#e3ddd0"),
           cover=_pal("#ffffff", "#d9b56a", "#b4bdcc", "#33435e"), cover_fill=HexColor("#16233a"),
           foot_fill=HexColor("#16233a"), swatch=("#16233a", "#d9b56a")),
    Design("wave", "Firuzəyi", "mərkəzləşmiş üz qabığı, günlər rəngli zolaqda",
           _pal(LOGO_DEEP, LOGO_TEAL, "#5d6883", "#d6e6ea"), sans=True, cover_style="center",
           cover_block=HexColor(LOGO_TEAL), stripe=(HexColor(LOGO_TEAL),), day_style="bar",
           swatch=(LOGO_TEAL, "#ffffff")),
    Design("panel", "Panel", "sol tünd göy panel, günlər kartlarda",
           _pal(LOGO_DEEP, LOGO_TEAL, "#5d6883", "#dbe2ee"), cover_style="split", panel=HexColor(LOGO_BLUE),
           cover=_pal(LOGO_DEEP, LOGO_TEAL, "#5d6883", "#dbe2ee"), day_style="card",
           card_fill=HexColor("#eef3f9"), swatch=(LOGO_BLUE, LOGO_TEAL)),
    Design("color", "Rəngli", "loqonun dörd rəngi, hər gün öz rəngində",
           _pal(LOGO_BLUE, LOGO_PINK, "#6a6f86", "#e4e1ec"), sans=True, cover_style="center",
           stripe=tuple(HexColor(c) for c in (LOGO_BLUE, LOGO_TEAL, LOGO_PURPLE, LOGO_PINK)), day_style="side",
           day_colors=tuple(HexColor(c) for c in (LOGO_BLUE, LOGO_TEAL, LOGO_PURPLE, LOGO_PINK)),
           swatch=(LOGO_PURPLE, LOGO_PINK)),
)}
DEFAULT_DESIGN = "classic"


def design_code(value: Any) -> str:
    """Tanınmayan / boş dəyər → klassik dizayn."""
    return value if isinstance(value, str) and value in DESIGNS else DEFAULT_DESIGN

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
                       ("Noto", "NotoSerif-Regular"), ("Noto-I", "NotoSerif-Italic"),
                       ("Sans", "NotoSans-Regular"), ("Sans-I", "NotoSans-Italic")):
        pdfmetrics.registerFont(TTFont(name, str(STATIC / "fonts" / f"{file}.ttf")))
    _fonts_ready = True


def _covered(font: str) -> set[int]:
    return set(pdfmetrics.getFont(font).face.charWidths)


# --- Mətn: Noto Serif (Caladea — yalnız ehtiyat) -------------------------------------------------


class Text:
    """Bütün mətn Noto Serif-lədir: Azərbaycan əlifbasının hamısı (ə, Ə, ğ, ı, ş …) və kiril bir şriftdədir.
    Əvvəl əsas şrift Caladea idi və "ə" sözün ortasında Noto-ya keçirdi — hərf fərqli görünürdü."""

    def __init__(self, c: Canvas, sans: bool = False, color: Any = None):
        self.c = c
        self.fonts = ("Sans", "Sans-I") if sans else ("Noto", "Noto-I")   # dizayna görə: Noto Sans / Noto Serif
        self.cov = {f: _covered(f) for f in self.fonts}
        self.color = color      # rəng verilməyəndə (Doc palitraya görə dəyişir)

    def runs(self, s: str, italic: bool) -> list[tuple[str, str]]:
        main, alt = (self.fonts[1], "Cal-I") if italic else (self.fonts[0], "Cal")
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

    def draw(self, x: float, y: float, s: str, size: float, color=None, italic: bool = False,
             tracking: float = 0, align: str = "left") -> float:
        """y — yuxarıdan baza xətti. Eni qaytarır."""
        color = color if color is not None else self.color
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


_FOLD = str.maketrans("əıöüğşçâîû", "eiougscaiu")


def _words(name: str) -> list[str]:
    """Adın sözləri, Azərbaycan hərfləri latın qarşılığına çevrilmiş: "Mərcimək şorbası" → ["mercimek", "sorbasi"]."""
    from app.dishes import dish_key

    return [w for w in dish_key(name).translate(_FOLD).split() if len(w) >= 2]


def _same_word(a: str, b: str) -> bool:
    """Eyni söz, şəkilçi fərqi ilə: "sorbasini" ≈ "sorbasi", "langeti" ≈ "langet" (ortaq başlanğıc)."""
    if a == b:
        return True
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return common >= 4 and common >= min(len(a), len(b)) - 1 and abs(len(a) - len(b)) <= 4


def names_dish(request: str, item: str) -> bool:
    """Sorğuda yazılan ad ("mercimek sorbasini", diakritiksiz, şəkilçi ilə) menyudakı yeməkdirmi ("Mərcimək
    şorbası"): sorğudakı hər söz yeməyin adındakı bir sözə uyğun gəlməlidir."""
    wanted, have = _words(request), _words(item)
    return bool(wanted) and all(any(_same_word(w, h) for h in have) for w in wanted)


def without(pkg: Package, exclude: dict | None) -> Package:
    """Paketin surəti: çıxarılması istənən kateqoriyalar ("İçki") və yeməklər olmadan. Boş qalan kateqoriya və
    gün bloku da düşür — menyu, "Gündəlik seçim", üz qabığındakı xülasə və şəkillər hamısı bundan qurulur."""
    if not exclude:
        return pkg
    from dataclasses import replace

    from app.dishes import category_name

    categories = set(exclude.get("categories") or [])
    dishes = [d for d in exclude.get("dishes") or [] if isinstance(d, str) and len(d.strip()) >= 3]

    def dropped(item: str) -> bool:
        return any(names_dish(d, item) for d in dishes)

    blocks = []
    for b in pkg.blocks:
        groups = []
        for g in b.groups:
            if category_name(g.label) in categories:
                continue
            items = [i for i in g.items if not dropped(i)]
            if items:
                groups.append(replace(g, items=items, notes={k: v for k, v in g.notes.items() if k in items}))
        if groups:
            blocks.append(replace(b, groups=groups))
    return replace(pkg, blocks=blocks)


def menu_items(snapshot: dict) -> list[tuple[str, str]]:
    """Təklifdəki bütün menyu maddələri: [(yemək, kateqoriya)] — şəkilləri tapmaq üçün."""
    lang = snapshot.get("language") if snapshot.get("language") in ("az", "en", "ru") else "az"
    out: list[tuple[str, str]] = []
    for line in snapshot.get("lines") or []:
        if isinstance(line, dict):
            for b in _package(line, lang, "AZN").blocks:
                out.extend((item, g.label) for g in b.groups for item in g.items)
    return out


@functools.lru_cache(maxsize=4)
def _logo(name: str) -> ImageReader:
    return ImageReader(str(STATIC / "offer" / name))


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


def az_lower(s: str) -> str:
    """Azərbaycan hərfləri ilə kiçik hərf: "İçki" → "içki" (str.lower() "i̇çki" verir), "I" → "ı"."""
    return s.replace("İ", "i").replace("I", "ı").lower()


def _singular(label: str) -> str:
    low = az_lower(label)
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
        from app.dishes import category_name
        single = category_name(label) == "Çörək" and max(len(i) for i in per_day) == 1   # "Çörək çeşidləri" — mətnlə
        if generic or single or (pkg.weekly and same and len(per_day[0]) == 1):
            for item in per_day[0]:
                out.append((None, item))
        else:  # hər gün eyni çox maddə (məs. 10 içki) də sayla: "10 içki", siyahı üz qabığına sığmır
            out.append((max(len(i) for i in per_day), _singular(label)))
    return out


def summary_line(pkg: Package, daily: bool = True) -> str:
    parts = [f"{n} {label}" if n else az_lower(label[:1]) + label[1:] for n, label in daily_selection(pkg)]
    if not parts:
        return ""
    text = ", ".join(parts) + "."
    return ("Hər gün " + text) if pkg.weekly and daily else text[:1].upper() + text[1:]


# --- Çəkmə ---------------------------------------------------------------------------------------


class Doc:
    def __init__(self, snapshot: dict, total: int = 0, photos: dict[str, bytes] | None = None,
                 plans: dict[int, Package] | None = None, measure: bool = False, design: str = DEFAULT_DESIGN,
                 exclude: dict | None = None):
        """`plans` — {snapshot-dakı sətrin indeksi: menyu planından qurulmuş paket} (app/package_menus.py).
        `measure` — yalnız səhifə sayı üçün keçid: şəkillər çəkilmir (yerləri eynidir, səhifələr eyni bölünür).
        `design` — DESIGNS-dəki kod (rənglər, şrift, səhifə bəzəyi).
        `exclude` — sorğuda menyudan çıxarılması istənənlər: {"categories": ["İçki"], "dishes": ["..."]}."""
        _register_fonts()
        self.snap, self.total, self.measure = snapshot, total, measure
        self.design = DESIGNS[design_code(design)]
        self.pal = self.design.body
        self.photo_mode = False  # şəkilli təklif: həftəlik menyu və günlər heç yerdə yazılmır (build)
        self.crops: dict[tuple[int, float, float], ImageReader | None] = {}
        self.photos = photos or {}
        self.lang = snapshot.get("language") if snapshot.get("language") in ("az", "en", "ru") else "az"
        self.brand = snapshot.get("brand") or "seaport"
        totals = snapshot.get("totals") or {}
        self.currency = totals.get("currency") or "AZN"
        lines = [l for l in snapshot.get("lines") or [] if isinstance(l, dict)]
        self.packages = [without(pkg, exclude) for pkg in
                         ((plans or {}).get(i) or _package(l, self.lang, self.currency) for i, l in enumerate(lines))]
        self.weekly = bool(self.packages) and all(p.weekly for p in self.packages)
        self.buf = io.BytesIO()
        self.c = Canvas(self.buf, pagesize=A4)
        self.c.setTitle(self.title())
        self.c.setAuthor("Seaport Catering" if self.brand == "seaport" else "NAFA")
        self.t = Text(self.c, self.design.sans, self.pal.ink)
        self.pages: list[Callable[[int, int], None]] = []
        self.y = 0.0
        self.block_n = 0        # çəkilmiş gün bloklarının sayı ("side" düzümündə rəng növbəsi)

    # Rənglər: cari palitra (üz qabığında dizaynın `cover` palitrası, qalan səhifələrdə `body`)
    ink = property(lambda self: self.pal.ink)
    accent = property(lambda self: self.pal.accent)
    muted = property(lambda self: self.pal.muted)
    line = property(lambda self: self.pal.line)

    def use(self, pal: Palette) -> None:
        self.pal = pal
        self.t.color = pal.ink

    def fill(self, color: Any, x: float, top: float, w: float, h: float) -> None:
        self.c.setFillColor(color)
        self.c.rect(x, H - top - h, w, h, stroke=0, fill=1)

    def decorate(self, cover: bool = False) -> None:
        """Səhifənin bəzəyi — məzmundan əvvəl çəkilir: üz qabığının fonu / bloku / paneli, zolaqlar, alt zolaq."""
        d = self.design
        if cover and d.cover_fill is not None:
            self.fill(d.cover_fill, 0, COVER_FILL_TOP, W, H - COVER_FILL_TOP)
        if cover and d.cover_block is not None:
            self.fill(d.cover_block, 0, 0, W, CENTER_BLOCK)
        if d.panel is not None:
            self.fill(d.panel, 0, 0, SPLIT_PANEL if cover else 9, H)
        if d.stripe and not (cover and d.cover_block is not None):
            self.stripes(0, 12 if cover else 6)
            if cover:
                self.stripes(H - 12, 12)
        if not cover and d.foot_fill is not None:
            self.fill(d.foot_fill, 0, FOOTER_RULE, W, H - FOOTER_RULE)

    def stripes(self, top: float, height: float) -> None:
        """Səhifə boyu üfüqi zolaq: dizaynın `stripe` rəngləri bərabər hissələrlə yan-yana."""
        colors = self.design.stripe
        for i, color in enumerate(colors):
            self.fill(color, W * i / len(colors), top, W / len(colors) + 0.5, height)

    def logo_card(self, x: float, top: float, size: float) -> None:
        """Loqo ağ yumru künclü lövhədə — rəngli fonun üstündə (loqonun öz fonu ağdır)."""
        self.c.setFillColor(WHITE)
        self.c.roundRect(x, H - top - size, size, size, size * 0.12, stroke=0, fill=1)
        pad = size * 0.11
        if self.brand == "seaport":
            r = _logo("seaport-logo.png")
            iw, ih = r.getSize()
            w = (size - 2 * pad) * min(1.0, iw / ih)
            self.logo(x + (size - w) / 2, top + pad + ((size - 2 * pad) - w * ih / iw) / 2, w, small=False)
        else:
            self.t.draw(x + size / 2, top + size / 2 + 12, "NAFA", 34, self.design.body.ink, align="center")

    def fit(self, s: str, size: float, width: float, italic: bool = False) -> float:
        """Böyük başlıq sətrə sığmırsa (uzun paket adı, enli şrift) — sığana qədər kiçilir."""
        w = self.t.width(s, size, italic)
        return size if w <= width else max(size * width / w, size * 0.55)

    def title(self) -> str:
        if len(self.packages) == 1 and self.packages[0].name_en:
            return f"{self.packages[0].name_en} Corporate Lunch Menu"
        return "Corporate Lunch Menu"

    def running_head(self) -> str:
        return "Həftəlik nahar menyusu" if self.weekly and not self.photo_mode else "Nahar menyusu"

    def span(self) -> tuple[str, str] | None:
        if self.photo_mode:
            return None
        spans = {p.days_span for p in self.packages if p.days_span}
        return spans.pop() if len(spans) == 1 else None

    # Səhifələr
    def finish_page(self, footer: Callable[[int, int], None]) -> None:
        self.pages.append(footer)
        footer(len(self.pages), self.total or len(self.pages))
        self.c.showPage()

    def _crop(self, data: bytes, w: float, h: float) -> ImageReader | None:
        """Eyni şəkil (məs. bir neçə gündə təkrarlanan yemək) bir dəfə kəsilib kiçildilir."""
        key = (id(data), w, h)
        if key not in self.crops:
            self.crops[key] = _cover_crop(data, w, h)
        return self.crops[key]

    def logo(self, x: float, top: float, width: float, small: bool) -> float:
        if self.brand == "seaport":
            r = _logo("seaport-mark.png" if small else "seaport-logo.png")
            iw, ih = r.getSize()
            h = width * ih / iw
            if not self.measure:
                self.c.drawImage(r, x, H - top - h, width, h, mask="auto")
            return h
        size = 15 if small else 34
        self.t.draw(x, top + size, "NAFA", size, self.ink)
        return size + 4

    def rule(self, y: float, color=None, width: float = 0.8, x1: float = LEFT, x2: float = RIGHT) -> None:
        self.c.setStrokeColor(color if color is not None else self.line)
        self.c.setLineWidth(width)
        self.c.line(x1, H - y, x2, H - y)

    def cover(self) -> None:
        """Üz qabığı — dizaynın `cover_style`-ına görə üç düzümdən biri; altdakı sətir (footer) ortaqdır."""
        t, style = self.t, self.design.cover_style
        self.decorate(cover=True)
        {"center": self._cover_center, "split": self._cover_split}.get(style, self._cover_left)()
        span = self.span()
        left, right = self.running_head(), (span[0] if span else (self.snap.get("customer_name") or ""))
        split = style == "split"

        def footer(n: int, total: int) -> None:
            t.draw(32 if split else LEFT, 795, left, 7.5, PANEL_SOFT if split else self.muted, tracking=0.5)
            if right:
                t.draw(RIGHT, 795, right, 7.5, self.muted, tracking=0.5, align="right")
        self.finish_page(footer)
        self.use(self.design.body)

    def _cover_subtitle(self) -> tuple[str, str]:
        weekly = self.weekly and not self.photo_mode
        span = self.span()
        second = span[1] if span else (f"Prepared for {self.snap['customer_name']}" if self.snap.get("customer_name") else "")
        return ("Weekly Executive Dining Proposal" if weekly else "Corporate Dining Proposal"), second

    def _cover_description(self, p: Package) -> str:
        return p.description or (
            "Hər gün üçün ayrıca tərtib olunmuş, sevilən klassik yeməklərdən ibarət balanslı nahar menyusu. "
            "Komandanız üçün sadə, səliqəli və etibarlı gündəlik həll." if p.weekly and not self.photo_mode else
            "Sevilən klassik yeməklərdən ibarət balanslı nahar menyusu. "
            "Komandanız üçün sadə, səliqəli və etibarlı gündəlik həll.")

    def _cover_about(self, x: float, y: float, width: float, align: str) -> None:
        """Üz qabığının aşağısı (mərkəzləşmiş və panelli düzümlər): paketin adı, altında təsvir və xülasə."""
        t = self.t
        if len(self.packages) == 1:
            p = self.packages[0]
            t.draw(x, y, f"{p.name} paket", 14.5, self.accent, align=align)
            yy = y + 24
            for line in t.wrap(self._cover_description(p), 11, width)[:3]:
                t.draw(x, yy, line, 11, self.ink, align=align)
                yy += 16.5
            summary = summary_line(p, daily=not self.photo_mode)
            for line in (t.wrap(summary, 8.5, width)[:2] if summary else []):
                t.draw(x, yy + 6, line, 8.5, self.muted, align=align)
                yy += 12
            return
        t.draw(x, y, "Paketlər", 14.5, self.accent, align=align)
        yy = y + 24
        for p in self.packages[:5]:
            price = f" — {p.price} / {p.unit}" if p.price and p.unit else f" — {p.price}" if p.price else ""
            t.draw(x, yy, p.name + price, 11, self.ink, align=align)
            yy += 16.5

    def _cover_center(self) -> None:
        """Mərkəzləşmiş üz qabığı: loqo yuxarıda ortada (rəngli blok varsa — ağ lövhədə, blokun kənarında)."""
        t, d, cx = self.t, self.design, W / 2
        if d.cover_block is not None:
            self.logo_card(cx - 85, CENTER_BLOCK - 105, 170)
            slogan_y = CENTER_BLOCK + 95
        else:
            self.logo(cx - 75, 62, 150, small=False)
            slogan_y = 248
        if self.brand == "seaport":
            t.draw(cx, slogan_y, "Dadlı Həyatın Fəlsəfəsi", 12.5, LOGO_NAVY, align="center")
        if self.snap.get("example"):
            t.draw(RIGHT, 70, "NÜMUNƏ PAKET", 8, WHITE if d.cover_block is not None else self.muted, tracking=1.2,
                   align="right")
        self.use(d.cover or d.body)
        y = slogan_y + 58
        self.rule(y, self.accent, 1.4, cx - 26, cx + 26)
        y += 52
        for line in t.wrap(self.title().upper(), 34, RIGHT - LEFT):
            t.draw(cx, y, line, 34, self.ink, align="center")
            y += 40
        first, second = self._cover_subtitle()
        t.draw(cx, y + 14, first, 14.5, self.ink, italic=True, align="center")
        if second:
            t.draw(cx, y + 34, second, 14.5, self.muted, italic=True, align="center")
        self.rule(647)
        self._cover_about(cx, 678, 400, "center")

    def _cover_split(self) -> None:
        """Panelli üz qabığı: solda rəngli panel (loqo ağ lövhədə, aşağıda müştəri), sağda başlıq və paket."""
        t, d = self.t, self.design
        self.logo_card((SPLIT_PANEL - 150) / 2, 56, 150)
        if self.brand == "seaport":
            t.draw(SPLIT_PANEL / 2, 234, "Dadlı Həyatın Fəlsəfəsi", 10.5, WHITE, align="center")
        if self.snap.get("example"):
            t.draw(RIGHT, 70, "NÜMUNƏ PAKET", 8, self.muted, tracking=1.2, align="right")
        customer = (self.snap.get("customer_name") or "").strip()
        if customer:
            t.draw(32, 664, "MÜŞTƏRİ", 7.5, PANEL_SOFT, tracking=1.2)
            yy = 684
            for line in t.wrap(customer, 13, SPLIT_PANEL - 56)[:3]:
                t.draw(32, yy, line, 13, WHITE)
                yy += 17
        self.use(d.cover or d.body)
        x, width = SPLIT_X, RIGHT - SPLIT_X
        self.rule(351, self.accent, 1.4, x, x + 51)
        y = 404
        for line in t.wrap(self.title().upper(), 30, width):
            t.draw(x - 1, y, line, 30, self.ink)
            y += 36
        first, second = self._cover_subtitle()
        t.draw(x, y + 12, first, 13.5, self.ink, italic=True)
        if second:
            t.draw(x, y + 31, second, 13.5, self.muted, italic=True)
        self.rule(647, x1=x)
        self._cover_about(x, 678, width, "left")

    def _cover_left(self) -> None:
        t = self.t
        self.logo(LEFT - 1, 59, 136, small=False)
        if self.brand == "seaport":
            t.draw(LEFT + 67, 222, "Dadlı Həyatın Fəlsəfəsi", 12.5, LOGO_NAVY, align="center")
        if self.snap.get("example"):
            t.draw(RIGHT, 70, "NÜMUNƏ PAKET", 8, self.muted, tracking=1.2, align="right")
        self.use(self.design.cover or self.design.body)   # loqodan aşağı: üz qabığının palitrası
        self.rule(351, self.accent, 0.9, LEFT, LEFT + 51)
        y = 410
        for line in t.wrap(self.title().upper(), 38, RIGHT - LEFT - 20):
            t.draw(LEFT - 2, y, line, 38, self.ink)
            y += 42
        y += 22
        first, second = self._cover_subtitle()
        t.draw(LEFT, y, first, 14.5, self.ink, italic=True)
        if second:
            t.draw(LEFT, y + 20, second, 14.5, self.muted, italic=True)

        self.rule(647)
        y = 683
        if len(self.packages) == 1:
            p = self.packages[0]
            t.draw(LEFT, y, f"{p.name} paket", 14.5, self.accent)
            yy = y
            for line in t.wrap(self._cover_description(p), 11, 292)[:4]:
                t.draw(COL2, yy, line, 11, self.ink)
                yy += 16.5
            summary = summary_line(p, daily=not self.photo_mode)
            if summary:
                for line in t.wrap(summary, 8.5, 300)[:2]:
                    t.draw(COL2, yy + 10, line, 8.5, self.muted)
                    yy += 12
        else:
            t.draw(LEFT, y, "Paketlər", 14.5, self.accent)
            yy = y
            for p in self.packages[:5]:
                t.draw(COL2, yy, p.name, 11, self.ink)
                if p.price:
                    t.draw(RIGHT, yy, f"{p.price} / {p.unit}" if p.unit else p.price, 11, self.muted, align="right")
                yy += 16.5

    def page_header(self) -> None:
        self.decorate()
        self.logo(LEFT, 45, 47, small=True)
        self.t.draw(RIGHT, 69, self.running_head(), 8.5, self.muted, italic=True, align="right")
        self.rule(83, self.ink, 0.7)
        self.y = 106.0

    def body_footer(self) -> Callable[[int, int], None]:
        label = self.title()

        def footer(n: int, total: int) -> None:
            # Alt zolaqlı dizaynda (decorate) yazı zolağın üstündədir — üz qabığının açıq rəngləri ilə
            on_fill = self.design.foot_fill is not None and self.design.cover is not None
            pal = self.design.cover if on_fill else self.pal
            if not on_fill:
                self.rule(FOOTER_RULE)
            self.t.draw(LEFT, 801, label, 7.5, pal.muted, tracking=0.4)
            w = self.t.draw(RIGHT, 801, f" / {total:02d}", 7.5, pal.muted, align="right")
            self.t.draw(RIGHT - w, 801, f"{n:02d}", 10, pal.ink, align="right")
        return footer

    def new_body_page(self) -> None:
        self.finish_page(self.body_footer())
        self.page_header()
        self.sep()

    def ensure(self, height: float) -> None:
        if self.y + height > BODY_BOTTOM:
            self.new_body_page()

    def section_title(self, eyebrow: str, title: str, right: str = "") -> None:
        self.t.draw(LEFT, self.y + 17, eyebrow, 11.5, self.accent, italic=True)
        if right:
            self.t.draw(RIGHT, self.y + 17, right, 8, self.muted, tracking=0.6, align="right")
        self.t.draw(LEFT - 2, self.y + 66, title, self.fit(title, 44, RIGHT - LEFT), self.ink)
        self.y += 99
        self.sep()

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

    def sep(self) -> None:
        """Bloklar arasında nazik xətt — zolaqlı və kartlı düzümlərdə lazım deyil (blokun öz sərhədi var)."""
        if self.design.day_style in ("plain", "side"):
            self.rule(self.y)

    def day_block(self, b: Block) -> None:
        height = self.block_height(b)
        self.ensure(height)
        t, d, top0 = self.t, self.design, self.y
        y = self.y + 29
        style = d.day_style
        if style == "bar":       # günün adı rəngli zolaqda, ingiliscəsi sağda
            self.c.setFillColor(self.accent)
            self.c.roundRect(LEFT, H - (top0 + 38), RIGHT - LEFT, 29, 4, stroke=0, fill=1)
            t.draw(LEFT + 11, y, b.title, 14, WHITE)
            if b.accent:
                t.draw(RIGHT - 11, y, b.accent, 10, WHITE, italic=True, align="right")
        elif style == "card":    # bütün blok açıq fonlu yumru kartdadır
            self.c.setFillColor(d.card_fill)
            self.c.roundRect(LEFT - 13, H - (top0 + height - 3), RIGHT - LEFT + 26, height - 9, 9, stroke=0, fill=1)
            w = t.draw(LEFT - 1, y + 2, b.title, 17, self.ink)
            if b.accent:
                t.draw(LEFT + w + 12, y + 2, b.accent, 10.5, self.accent, italic=True)
        elif style == "side":    # solda günün rəngində şaquli xətt, ad da həmin rəngdə
            color = d.day_colors[self.block_n % len(d.day_colors)] if d.day_colors else self.accent
            self.fill(color, LEFT - 15, top0 + 13, 4.5, height - 21)
            w = t.draw(LEFT - 1, y, b.title, 18.5, color)
            if b.accent:
                t.draw(LEFT + w + 12, y, b.accent, 10.5, self.muted, italic=True)
        else:
            w = t.draw(LEFT - 1, y, b.title, 18.5, self.ink)
            if b.accent:
                t.draw(LEFT + w + 12, y, b.accent, 10.5, self.accent, italic=True)
        self.block_n += 1
        top = y + 22
        bottom = top
        for x, col in zip(DAY_COLS, self.columns(b)):
            yy = top
            for g in col:
                if g.label:
                    t.draw(x, yy, g.label, 8.5, self.muted, italic=True)
                    yy += 15
                for lines, notes, _ in self.item_rows(g):
                    for line in lines:
                        t.draw(x, yy, line, 11, self.ink)
                        yy += LEAD
                    for line in notes:
                        t.draw(x, yy - 3, line, 8.5, self.muted)
                        yy += NOTE_LEAD
                yy += 5
            bottom = max(bottom, yy)
        self.y = bottom + 5          # son maddənin bazası + 26pt (nümunədəki kimi)
        self.sep()

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
            self.decorate()
            self.logo(LEFT, 45, 50, small=True)
            t.draw(RIGHT, 66, header, 8.5, self.muted, italic=True, align="right")
            self.rule(83, self.ink, 0.7)
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
            w = t.draw(LEFT, head + 17.5, az, 17.5, self.ink)
            if en:
                t.draw(LEFT + w + 9, head + 17.5, en, 9.5, self.accent, italic=True)
            self.rule(head + 27)
            self.y = head + 46
            first = False
            for i in range(0, len(dishes), 2):
                if self.y + GAL_ROW - 20 > BODY_BOTTOM + 6:
                    self.finish_page(self.body_footer())
                    page()
                for j, (name, data, note) in enumerate(dishes[i:i + 2]):
                    x = LEFT + j * (GAL_W + GAL_GAP)
                    img = None if self.measure else self._crop(data, GAL_W, GAL_H)
                    if img is not None:
                        self.c.drawImage(img, x, H - self.y - GAL_H, GAL_W, GAL_H)
                    line = t.wrap(name, 10.8, GAL_W)[0]
                    t.draw(x, self.y + GAL_H + 15, line, 10.8, self.ink)
                    if note:
                        t.draw(x, self.y + GAL_H + 27, t.wrap(note, 8.5, GAL_W)[0], 8.5, self.muted)
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
        t.draw(LEFT, top, "Gündəlik seçim", 15.5, self.ink)
        t.draw(LEFT, top + 16, "Daily selection", 8, self.muted, tracking=0.5)
        xs = (COL2, COL2 + 114, COL2 + 228)
        for i, (n, label) in enumerate(sel):
            x, y = xs[i % 3], top + (i // 3) * 42
            if n is not None:
                t.draw(x, y + 6, str(n), 26, self.accent)
                for j, line in enumerate(t.wrap(label, 10.5, 75)[:2]):
                    t.draw(x + 30, y + 3 + j * 13, line, 10.5, self.ink)
            else:
                for j, line in enumerate(t.wrap(label, 10.5, 105)[:2]):
                    t.draw(x, y - 2 + j * 13, line, 10.5, self.ink)
        self.y = top + rows * 42 + 18
        self.rule(self.y)

    def field(self, x: float, label_y: float, line_y: float, width: float, label: str, label_en: str,
              value: str = "", note: str = "", value_size: float = 17) -> None:
        """Nümunədəki kimi: "Etiket / Label", altında dəyər və xətt (line_y — xəttin yeri)."""
        t = self.t
        w = t.draw(x, label_y, label, 8.5, self.ink)
        rest = f" / {label_en}" if label_en else ""
        if rest and w + t.width(rest, 8.5) > width:
            t.draw(x, label_y + 10.5, label_en, 8.5, self.muted)       # uzun etiket — ingiliscəsi ikinci sətirdə
        elif rest:
            t.draw(x + w, label_y, rest, 8.5, self.muted)
        if value:
            lines = t.wrap(value, value_size, width - 6)[:3]
            step = value_size + 2
            vy = line_y - 9 - (len(lines) - 1) * step
            vw = 0.0
            for k, line in enumerate(lines):
                vw = t.draw(x, vy + k * step, line, value_size, self.ink)
            if note:
                t.draw(x + vw + 10, vy + (len(lines) - 1) * step, note, 9.5, self.muted, italic=True)
        self.rule(line_y, self.muted, 0.8, x, x + width)

    def proposal(self) -> None:
        snap = self.snap
        totals = snap.get("totals") or {}
        # Qiymət şərti (hər təklifdə yazılır): qiymət 1 nəfərlik yemək üçündür; ƏDV qiymətə daxil deyil
        # (paket qiymətləri ƏDV xaric saxlanılır). Agent ƏDV-ni qiymətə daxil sayıbsa — "daxildir".
        vat_in = totals.get("vat_included") is True
        terms_az = ["Qiymət 1 nəfərlik yemək üçündür.", "Qiymətə ƏDV daxildir." if vat_in else "Qiymətə ƏDV daxil deyil."]
        terms_en = ["The price is for one person's meal.", "VAT is included." if vat_in else "VAT is not included."]
        has_total = totals.get("total") not in (None, "")
        net = totals.get("subtotal") if not vat_in and totals.get("subtotal") not in (None, "") else None
        if has_total and net is not None and _money(net, self.currency) != _money(totals.get("total"), self.currency):
            with_vat = _money(totals.get("total"), self.currency)
            terms_az.append(f"ƏDV ilə cəmi: {with_vat}.")
            terms_en.append(f"Total with VAT: {with_vat}.")
        t = self.t
        self.ensure(175)
        r = self.y
        t.draw(LEFT, r + 38, "Təklif məlumatları", 15.5, self.ink)
        t.draw(LEFT, r + 54, "Proposal details", 8, self.muted, tracking=0.5)
        wl, xr = 155, COL2 + 184
        customer = " ".join(x for x in (snap.get("customer_name"), snap.get("contact_person")) if x)
        self.field(COL2, r + 34, r + 72, wl, "Müştəri", "Client", customer, value_size=13)
        if len(self.packages) == 1:
            self.field(xr, r + 39, r + 72, RIGHT - xr, "Qiymət", "Price", self.packages[0].price, "1 nəfər")
        else:
            self.field(xr, r + 39, r + 72, RIGHT - xr, "Qiymət (1 nəfər)", "Price per person",
                       "; ".join(f"{p.name} {p.price}" for p in self.packages if p.price), value_size=11)
        if has_total:
            if net is not None:
                amount, vat = _money(net, self.currency), "ƏDV daxil deyil"
            else:  # yalnız yekun məbləğ var: ƏDV daxildirsə (və ya ayrıca göstərilibsə) elə də yazılır
                amount = _money(totals.get("total"), self.currency)
                vat = "ƏDV daxil" if vat_in or totals.get("vat") else "ƏDV daxil deyil"
            self.field(COL2, r + 108, r + 156, wl, "Cəmi məbləğ", "Total", amount, vat, 13)
        else:
            self.field(COL2, r + 108, r + 156, wl, "Təklifin qüvvədə olma müddəti", "Proposal validity")
        # "Əlavə şərtlər" boş sətirdir (əl ilə doldurmaq üçün). Agentin qeydi və fərziyyələri müştəriyə gedən
        # PDF-ə yazılmır — onlar daxili mətndir ("Menudan ickini cixart; Seçilmiş paket: «Standart» (id: …)")
        # və yalnız paneldə, "Agentin fərziyyələri" altında görünür.
        self.field(xr, r + 117, r + 156, RIGHT - xr, "Əlavə şərtlər", "Additional terms")
        # Qiymət şərti sol sütunda, başlığın altında (blokun hündürlüyü dəyişmir — səhifə sayı əvvəlki kimi)
        # Hər cümlə öz sətrində (sığmasa ikiyə bölünür): azərbaycanca, altında ingiliscə
        yy, width = r + 80, COL2 - LEFT - 8
        for sentence in terms_az:
            for line in t.wrap(sentence, 8.5, width)[:2]:
                t.draw(LEFT, yy, line, 8.5, self.ink)
                yy += 11
        yy += 3
        for sentence in terms_en:
            for line in t.wrap(sentence, 7.5, width)[:2]:
                t.draw(LEFT, yy, line, 7.5, self.muted)
                yy += 9.5
        self.y = max(r + 170, yy - 4)

    def build(self) -> bytes:
        self.photo_mode = bool(self.photos and self.gallery_sections())
        self.cover()
        if self.photo_mode:
            # Şəkilli təklif: üz qabığından sonra dərhal yemək şəkilləri (kateqoriyalar üzrə), həftəlik menyu və
            # günlər yazılmır; təklif məlumatları (müştəri, qiymət) sonda.
            self.gallery(close_current=False)
            self.proposal()
            self.finish_page(self.body_footer())
            self.c.save()
            return self.buf.getvalue()
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
        self.finish_page(self.body_footer())
        self.c.save()
        return self.buf.getvalue()


def render(snapshot: dict, photos: dict[str, bytes] | None = None, plans: dict[int, Package] | None = None,
           design: str = DEFAULT_DESIGN, exclude: dict | None = None) -> bytes:
    """Agentin təklif snapshot-u → PDF baytları. `photos` — {dish_key: JPEG} (şəkilli təklif; app/dishes.py),
    `plans` — menyu planından qurulmuş paketlər (app/package_menus.py), `design` — DESIGNS-dəki kod,
    `exclude` — menyudan çıxarılanlar (app/offer_price.py). Footer-də "02 / 03" üçün iki keçid."""
    first = Doc(snapshot, photos=photos, plans=plans, measure=True, design=design, exclude=exclude)
    first.build()
    return Doc(snapshot, total=len(first.pages), photos=photos, plans=plans, design=design, exclude=exclude).build()


# --- Paketin menyu kitabçası (Alətlər → Paketlər → "PDF-ə bax") -----------------------------------


class MenuDoc(Doc):
    """Nümunə SeaPort_MediumPlus_Menu_Dish_Photos.pdf: üz qabığı, (istəyə görə) həftəlik menyu, yemək şəkilləri."""

    def __init__(self, pkg: Package, photos: dict[str, bytes], brand: str = "seaport", note: str = "",
                 schedule: bool = True, total: int = 0, measure: bool = False):
        super().__init__({"brand": brand, "language": "az", "lines": [{}]}, total=total, photos=photos, plans={0: pkg},
                         measure=measure)
        self.note, self.schedule = note, schedule
        self.weekly = pkg.weekly

    def title(self) -> str:
        p = self.packages[0]
        return f"{p.name_en or p.name} Corporate Lunch Menu"

    def running_head(self) -> str:
        if not self.schedule:
            return "Yemək şəkilləri"
        return "Həftəlik menyu" if self.weekly else "Nahar menyusu"

    def cover(self) -> None:
        t, p = self.t, self.packages[0]
        self.logo(LEFT, 61, 129, small=False)
        if self.brand == "seaport":
            t.draw(LEFT + 64, 222, "Dadlı Həyatın Fəlsəfəsi", 12.5, LOGO_NAVY, align="center")
        self.rule(351, self.accent, 2, LEFT, LEFT + 51)
        y = 402
        for line in t.wrap(self.title().upper(), 40, RIGHT - LEFT)[:3]:
            t.draw(LEFT, y, line, 40, self.ink)
            y += 44.5
        has_photos = bool(self.gallery_sections())
        menu_en, menu_az = ("Weekly Menu", "Həftəlik menyu") if self.weekly else ("Lunch Menu", "Nahar menyusu")
        en = (f"{menu_en} & Dish Photography" if self.schedule and has_photos else
              menu_en if self.schedule else "Dish Photography")
        az = (f"{menu_az} və yemək şəkilləri" if self.schedule and has_photos else
              menu_az if self.schedule else "Yemək şəkilləri")
        t.draw(LEFT, y - 2, en, 14, self.ink, italic=True)
        t.draw(LEFT, y + 19, az, 14, self.muted, italic=True)
        self.rule(647.5)
        t.draw(LEFT, 677, f"{p.name} paket", 13, self.accent)
        sections = [az_lower(section_name(g.label)[0]) for g in _unique_groups(p)]
        desc = p.description or (f"{p.name} menyusunda təqdim olunan yeməklər" +
                                 (": " + ", ".join(sections[:-1]) + (" və " if len(sections) > 1 else "") + sections[-1] + "."
                                  if sections else "."))
        yy = 678
        for line in t.wrap(desc, 10, 300)[:4]:
            t.draw(COL2, yy, line, 10, self.ink)
            yy += 15
        summary = summary_line(p)
        if summary:
            for line in t.wrap(summary, 8.5, 300)[:2]:
                t.draw(COL2, yy + 6, line, 8.5, self.muted)
                yy += 12
        if self.note:
            t.draw(COL2, max(yy + 18, 731), t.wrap(self.note, 8, 300)[0], 8, self.muted)
        span = p.days_span

        def footer(n: int, total: int) -> None:
            t.draw(LEFT, 795, self.running_head(), 7.5, self.muted)
            if span:
                t.draw(RIGHT, 795, span[0], 7.5, self.muted, align="right")
        self.finish_page(footer)

    def build(self) -> bytes:
        self.cover()
        p = self.packages[0]
        if self.schedule and p.blocks:
            self.page_header()
            self.section_title(*(("Weekly menu", "Həftəlik menyu") if p.weekly else ("Menu", "Menyu")),
                               p.days_span[0] if p.days_span else "")
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
    first = MenuDoc(pkg, photos, brand, note, schedule, measure=True)
    first.build()
    return MenuDoc(pkg, photos, brand, note, schedule, total=len(first.pages)).build()
