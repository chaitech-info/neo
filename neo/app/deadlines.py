"""Təklif son tarixi: lead mətnindəki "Son tarix: ..." sətrindən `deadline_at`.

Tender scraper-ləri son tarixi mətnə eyni formatda yazır (etender, corporate,
bp EDP: "Son tarix: 07.10.2026[ 18:00]"; Dünya Bankı: "Son tarix: 2026-10-10").
Oxuma bir yerdədir ki, scraper-lərə toxunmadan köhnə lead-lərə də tətbiq olunsun.

Saxlama bazadakı digər tarixlər kimi naive UTC-dir; saatı olmayan son tarix
Bakı vaxtı ilə günün sonu (23:59) sayılır — həmin gün hələ müraciət olunur.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

_DMY = re.compile(r"Son tarix:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
_ISO = re.compile(r"Son tarix:\s*(\d{4})-(\d{2})-(\d{2})")
_BAKU_OFFSET = timedelta(hours=4)  # Azərbaycan 2016-dan DST istifadə etmir
_END_OF_DAY = time(23, 59)


def extract_deadline(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        if m := _DMY.search(text):
            day, month, year = int(m[1]), int(m[2]), int(m[3])
            at = time(int(m[4]), int(m[5])) if m[4] else _END_OF_DAY
        elif m := _ISO.search(text):
            year, month, day = int(m[1]), int(m[2]), int(m[3])
            at = _END_OF_DAY
        else:
            return None
        local = datetime.combine(datetime(year, month, day).date(), at)
    except ValueError:
        return None
    return local - _BAKU_OFFSET
