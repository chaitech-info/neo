"""Hazır (kodda saxlanan) məktub şablonları — "AI ilə məktub yaz"-da hər kəsə görünür, ilk seçimdir.

İstifadəçinin öz şablonlarından (LetterTemplate, "Şablonlarım") fərqi: mətn olduğu kimi qalır — AI-ya getmir,
yalnız müraciət sətri ("Hörmətli Rauf bəy,") kontaktın adından qurulur və imza şablonun öz imzasıdır
(göndərən əməkdaşın adı ilə adi imza yox). Azərbaycan dilindən başqa dil seçiləndə və "Yenidən yarat"-da
şablon AI-ya şablon kimi verilir (app/outreach/mail.py), imza yenə bu şablonunku olur.

Formda dəyəri "builtin:<açar>" kimidir (istifadəçi şablonları — rəqəmli id).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = "builtin:"


@dataclass(frozen=True)
class Builtin:
    key: str
    name: str          # formdakı ad
    subject: str
    body: str          # {salutation} — müraciət sətri
    signature: str     # "Hörmətlə," + şirkətin əlaqə bloku

    @property
    def ref(self) -> str:
        return PREFIX + self.key


_SEAPORT_SUBJECT = "“Seaport Catering” — korporativ qidalanma və keyterinq xidmətləri"
_SEAPORT_TEXT = (
    "İctimai iaşə sahəsində ixtisaslaşmış şirkət olaraq müxtəlif sektorlarda fəaliyyət göstərən müəssisələrə "
    "beynəlxalq standartlara uyğun korporativ qidalanma və peşəkar keyterinq xidmətləri təqdim edirik.\n\n"
    "Keyfiyyət, qida təhlükəsizliyi və etibarlılıq fəaliyyətimizin əsas prinsipləridir. Qidalanmanın təşkilini "
    "öz üzərimizə götürərək vaxtınıza və resurslarınıza qənaət etməyinizə, əsas fəaliyyətinizə fokuslanmağınıza "
    "imkan yaradırıq.\n\n"
    "Sizinlə əməkdaşlıq imkanlarını müzakirə etməkdən və xidmətlərimizi təqdim etməkdən məmnun olarıq."
)
_SEAPORT_SIGNATURE = (
    "Hörmətlə,\n"
    "Sales Support Team\n"
    "“Seaport Catering” MMC\n\n"
    "M: (+994) 102395025\n"
    "E: sales@seaport-catering.az;corporate@seaport-catering.az\n"
    "A: Çıraq Plaza, Bakı şəhəri, Tbilisi prospekti 49C, AZ1065.6-cı mərtəbə"
)

# Şəxsə: "Hörmətli Rauf bəy," + "Sizi “Seaport Catering” şirkəti adından salamlayırıq!"
SEAPORT_INTRO = Builtin(
    key="seaport-intro",
    name="Seaport Catering — şəxsə (Hörmətli … bəy / xanım)",
    subject=_SEAPORT_SUBJECT,
    body="{salutation}\n\nSizi “Seaport Catering” şirkəti adından salamlayırıq!\n\n" + _SEAPORT_TEXT,
    signature=_SEAPORT_SIGNATURE,
)
# Şirkətə (adsız): müraciət sətri yoxdur, şirkət adından salam
SEAPORT_COMPANY = Builtin(
    key="seaport-company",
    name="Seaport Catering — şirkət adından (şəxsin adı olmadan)",
    subject=_SEAPORT_SUBJECT,
    body="“Seaport Catering” şirkəti Sizi salamlayır!\n\n" + _SEAPORT_TEXT,
    signature=_SEAPORT_SIGNATURE,
)

BUILTINS: dict[str, Builtin] = {b.ref: b for b in (SEAPORT_INTRO, SEAPORT_COMPANY)}
DEFAULT = SEAPORT_INTRO.ref
# Şəxsə müraciətli şablon seçilib, amma alıcının adı yoxdur (şirkətin / elanın emaili) — şirkət adından olanı
WITHOUT_NAME = {SEAPORT_INTRO.ref: SEAPORT_COMPANY}


def get(ref: str | None) -> Builtin | None:
    return BUILTINS.get(ref or "")


def is_ref(value: str | None) -> bool:
    """Formdan gələn şablon dəyəri: hazır şablon və ya istifadəçi şablonunun id-si."""
    return bool(value) and (value in BUILTINS or value.isdigit())


_SURNAME = re.compile(r"(ova|yeva|eva|ov|yev|ev|zadə|zade)$")      # Əliyev, Əliyeva, Həsənzadə
_FEMALE_SURNAME = re.compile(r"(ova|yeva|eva)$")
_MALE_SURNAME = re.compile(r"(ov|yev|ev)$")
_TITLES = {"dr", "dr.", "prof", "prof.", "cənab", "xanım", "bəy", "mr", "mr.", "mrs", "mrs.", "ms", "ms."}


def salutation(name: str | None) -> str:
    """Müraciət: "Hörmətli Rauf bəy," / "Hörmətli Aygün xanım,". Ad soyaddan (Əliyev / Əliyeva) və ya ata adından
    (oğlu / qızı) seçilir — soyad əvvəl də yazıla bilər ("Əliyev Rauf"). Cinsi bilinməyəndə "bəy / xanım" qalır
    (göndərməzdən əvvəl düzəltmək üçün), ad yoxdursa — "Hörmətli cənab / xanım,"."""
    words = [w.strip(",.;") for w in (name or "").split()]
    words = [w for w in words if w and w.casefold() not in _TITLES]
    low = [w.casefold() for w in words]
    gender = None
    if "qızı" in low or "qizi" in low:
        gender = "xanım"
    elif "oğlu" in low or "oglu" in low:
        gender = "bəy"
    else:
        for w in low:
            if _FEMALE_SURNAME.search(w):
                gender = "xanım"
                break
            if _MALE_SURNAME.search(w):
                gender = "bəy"
                break
    skip = set()
    for i, w in enumerate(low):
        if w in ("oğlu", "oglu", "qızı", "qizi"):
            skip.update({i, i - 1})          # "Rəşid oğlu" — ata adı, müraciətə düşmür
        elif _SURNAME.search(w) and len(words) > 1:
            skip.add(i)
    first = next((w for i, w in enumerate(words) if i not in skip), None)
    if first is None:
        return "Hörmətli cənab / xanım,"
    return f"Hörmətli {first} {gender or 'bəy / xanım'},"


def for_recipient(b: Builtin, contact_name: str | None) -> Builtin:
    """Şəxsə müraciətli şablon, amma alıcının adı yoxdur → şirkət adından olan variant ("Hörmətli cənab / xanım"
    əvəzinə "“Seaport Catering” şirkəti Sizi salamlayır!")."""
    return WITHOUT_NAME.get(b.ref, b) if not (contact_name or "").strip() else b


def fill(b: Builtin, contact_name: str | None) -> dict[str, str]:
    """{subject, body} — body imzasızdır (imza `b.signature`, mail.with_signature əlavə edir)."""
    return {"subject": b.subject, "body": b.body.replace("{salutation}", salutation(contact_name))}


def template_text(b: Builtin) -> str:
    """AI üçün şablon mətni (başqa dil / "Yenidən yarat"): müraciət sətri boşluq kimi (şirkət variantında yoxdur)."""
    return b.body.replace("{salutation}", "Hörmətli [Ad] bəy / xanım,")
