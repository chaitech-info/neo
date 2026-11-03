"""Yemək kataloqu və şəkilləri — şəkilli qiymət təklifi üçün (Alətlər → Yemək şəkilləri).

Təklifdəki menyu maddəsi ("Mərcimək şorbası") kataloqdakı yeməklə adın normal forması (`dish_key`) ilə
uyğunlaşdırılır — az/en/ru adlarından hər biri ola bilər. Şəkil yükləyəndə JPEG-ə çevrilir və kiçildilir
(PDF-də kiçik görünür, bazada az yer tutsun); baytlar bazada saxlanılır (Vercel-də yazıla bilən disk yoxdur).
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime

from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, undefer

from app.db.models import Dish

MAX_UPLOAD = 8 * 1024 * 1024
MAX_SIDE = 900                    # PDF-də ~50 pt görünür; 900 px çap üçün də kifayətdir
JPEG_QUALITY = 82
CATEGORIES = ["Şorba", "Əsas yemək", "Qarnir", "Salat", "Desert", "İçki", "Çörək"]


class DishError(ValueError):
    """İstifadəçiyə göstərilən xəta (Azərbaycan dilində)."""


def category_name(label: str) -> str:
    """Menyudakı kateqoriya etiketi ("Salatlar", "Əsas yemək") → kataloq kateqoriyası (tanınmasa — olduğu kimi)."""
    k = dish_key(label)
    for c in CATEGORIES:
        ck = dish_key(c)
        if k == ck or k.startswith(ck[:5]):
            return c
    return (label or "").strip()[:60]


def dish_key(name: str) -> str:
    """Adın normal forması: kiçik hərf (az: I→ı, İ→i), durğu işarələri və artıq boşluqlar atılır.
    migrations/versions/0014_dishes.py::_key ilə eyni olmalıdır."""
    s = unicodedata.normalize("NFC", name or "").replace("İ", "i").replace("I", "ı").lower()
    s = re.sub(r"[\s\-–—_/,.;:()\"'«»]+", " ", s)
    return s.strip()[:200]


def prepare_image(data: bytes) -> bytes:
    """Yüklənən faylı yoxlayır və JPEG-ə çevirir (ən uzun tərəf MAX_SIDE px)."""
    if not data:
        raise DishError("Şəkil faylı seç")
    if len(data) > MAX_UPLOAD:
        raise DishError("Şəkil 8 MB-dan böyük olmamalıdır")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise DishError("Bu fayl şəkil deyil (JPEG, PNG və ya WebP yüklə)") from None
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out.getvalue()


def add_dish(db: Session, name: str, category: str = "", name_en: str = "", name_ru: str = "",
             user_id: int | None = None) -> Dish:
    name = " ".join((name or "").split())[:300]
    key = dish_key(name)
    if len(key) < 2:
        raise DishError("Yeməyin adını yaz")
    if db.scalar(select(Dish.id).where(Dish.key == key)) is not None:
        raise DishError(f"«{name}» kataloqda artıq var")
    dish = Dish(key=key, name=name, category=(category or "").strip()[:60] or None,
                name_en=(name_en or "").strip()[:300] or None, name_ru=(name_ru or "").strip()[:300] or None,
                updated_by_id=user_id)
    db.add(dish)
    db.flush()
    return dish


def set_image(dish: Dish, data: bytes, user_id: int | None) -> None:
    dish.image = prepare_image(data)
    dish.image_type = "image/jpeg"
    dish.image_updated_at = datetime.utcnow()
    dish.updated_by_id = user_id


def clear_image(dish: Dish, user_id: int | None) -> None:
    dish.image = None
    dish.image_type = None
    dish.image_updated_at = datetime.utcnow()
    dish.updated_by_id = user_id


def photos_for(db: Session, names: list[str]) -> dict[str, bytes]:
    """Menyu maddələri üçün şəkillər: {dish_key(maddə): JPEG}. Kataloqda az/en/ru adlarından biri uyğun gəlsə tapılır."""
    wanted = {dish_key(n) for n in names if n}
    wanted.discard("")
    if not wanted:
        return {}
    rows = db.scalars(select(Dish).options(undefer(Dish.image))
                      .where(Dish.image.is_not(None))
                      .where(or_(Dish.key.in_(wanted), Dish.name_en.is_not(None), Dish.name_ru.is_not(None))))
    out: dict[str, bytes] = {}
    for d in rows:
        for alias in (d.key, dish_key(d.name_en or ""), dish_key(d.name_ru or "")):
            if alias in wanted and alias not in out:
                out[alias] = d.image
    return out


def remember(db: Session, names: list[str], category_of: dict[str, str] | None = None) -> int:
    """Təklifdə görünən, kataloqda olmayan yeməkləri şəkilsiz əlavə edir — "Yemək şəkilləri"ndə şəkli
    gözləyənlər kimi görünsünlər. Neçəsinin əlavə olunduğunu qaytarır."""
    category_of = category_of or {}
    fresh: dict[str, str] = {}
    for n in names:
        n = " ".join((n or "").split())[:300]
        k = dish_key(n)
        if len(k) >= 2 and k not in fresh:
            fresh[k] = n
    if not fresh:
        return 0
    known = set(db.scalars(select(Dish.key).where(Dish.key.in_(fresh))))
    for k, n in fresh.items():
        if k not in known:
            db.add(Dish(key=k, name=n, category=(category_of.get(n) or None)))
    return len(set(fresh) - known)
