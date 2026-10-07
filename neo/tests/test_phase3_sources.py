"""Faza 3 siyahı mənbələri: URL nümunələri real səhifələrdəki linklərə uyğun gəlir (2026-10-07 nümunələri)."""
import re

import pytest

from app.sources import BY_KEY

SAMPLES = {
    "unibank": ("https://unibank.az/az/news/view/2668/_unibank_kb_asc_it_tehlukesizlik_ve_elektrik_ishlerin",
                "https://unibank.az/az/news/category/649"),
    "dost": ("https://dost.gov.az/announcements?id=289", "https://dost.gov.az/announcements"),
    "afb": ("https://afb.az/afb-bank-tender-elani-02-10", "https://afb.az/tender-elanlari"),
}


@pytest.mark.parametrize("key", SAMPLES)
def test_pattern_matches_items_but_not_the_listing_page(key):
    site = BY_KEY[key].scraper.site
    item, listing = SAMPLES[key]
    assert re.search(site.link_pattern, item)
    assert not re.search(site.link_pattern, listing)
    assert site.kind == "tender" and site.org


def test_afb_pattern_also_matches_short_titles():
    site = BY_KEY["afb"].scraper.site
    assert re.search(site.link_pattern, "https://afb.az/tender-elani-agentlik")
    assert re.search(site.link_pattern, "https://afb.az/afb-bank-tender-elani-lisenziya")
    assert not re.search(site.link_pattern, "https://afb.az/teminatli-istehlak-kredit")
