from app.scrapers.azerweb import AzerwebScraper
from app.scrapers.emlak import EmlakScraper
from app.scrapers.etender import EtenderScraper
from app.scrapers.linkedin import LinkedInScraper
from app.scrapers.marja import MarjaScraper
from app.scrapers.news import FedScraper, News1Scraper, OxuScraper

ALL_SCRAPERS = [
    MarjaScraper,
    AzerwebScraper,
    News1Scraper,
    OxuScraper,
    FedScraper,
    EtenderScraper,
    EmlakScraper,
    # LinkedInScraper qəsdən buraya salınmayıb — bax app/scrapers/linkedin.py
]

__all__ = [
    "ALL_SCRAPERS",
    "MarjaScraper",
    "AzerwebScraper",
    "News1Scraper",
    "OxuScraper",
    "FedScraper",
    "EtenderScraper",
    "EmlakScraper",
    "LinkedInScraper",
]
