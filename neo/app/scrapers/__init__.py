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
    LinkedInScraper,
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
