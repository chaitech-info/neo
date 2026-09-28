from datetime import datetime

from app.deadlines import extract_deadline


def test_date_with_time_is_baku_local_converted_to_utc():
    assert extract_deadline("X\nSon tarix: 07.10.2026 18:00") == datetime(2026, 10, 7, 14, 0)


def test_date_only_means_end_of_day_in_baku():
    assert extract_deadline("Son tarix: 07.10.2026") == datetime(2026, 10, 7, 19, 59)


def test_iso_format_from_world_bank():
    assert extract_deadline("Son tarix: 2026-10-10") == datetime(2026, 10, 10, 19, 59)


def test_missing_or_invalid():
    assert extract_deadline("Tenderin ehtimal olunan qiyməti 18 min manat") is None
    assert extract_deadline("Son tarix: 31.02.2026") is None
    assert extract_deadline(None) is None
