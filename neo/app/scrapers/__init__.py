from app.scrapers.azerweb import AzerwebScraper
from app.scrapers.bp_edp import BpEdpScraper
from app.scrapers.corporate import CorporateTenderScraper
from app.scrapers.emlak import EmlakScraper
from app.scrapers.etender import EtenderScraper
from app.scrapers.linkedin import LinkedInScraper
from app.scrapers.marja import MarjaScraper
from app.scrapers.news import FedScraper, FedTenderScraper, News1Scraper, OxuScraper
from app.scrapers.worldbank import WorldBankScraper

ALL_SCRAPERS = [
    MarjaScraper,
    News1Scraper,
    OxuScraper,
    FedScraper,
    FedTenderScraper,
    EtenderScraper,
    EmlakScraper,
    CorporateTenderScraper,
    WorldBankScraper,
    BpEdpScraper,
    # AzerwebScraper deaktivdir — robots.txt /api/-ni qadağan edir (bax app/scrapers/azerweb.py)
    LinkedInScraper,  # Apify vasitəsilə (lisenziyalı) — bax app/scrapers/linkedin.py
]

__all__ = [
    "ALL_SCRAPERS",
    "MarjaScraper",
    "AzerwebScraper",
    "News1Scraper",
    "OxuScraper",
    "FedScraper",
    "FedTenderScraper",
    "EtenderScraper",
    "EmlakScraper",
    "CorporateTenderScraper",
    "WorldBankScraper",
    "BpEdpScraper",
    "LinkedInScraper",
]
