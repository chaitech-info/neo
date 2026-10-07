from app.scrapers.azerweb import AzerwebScraper
from app.scrapers.boss import BossAzScraper
from app.scrapers.bp_edp import BpEdpScraper
from app.scrapers.corporate import CorporateTenderScraper
from app.scrapers.emlak import EmlakScraper
from app.scrapers.etender import EtenderScraper
from app.scrapers.intl import IntlTenderScraper
from app.scrapers.jobsearch import JobSearchScraper
from app.scrapers.media import MediaScraper
from app.scrapers.linkedin import LinkedInScraper
from app.scrapers.marja import MarjaScraper
from app.scrapers.news import (
    FedScraper,
    FedTenderScraper,
    News1Scraper,
    OxuScraper,
    ReportEnergetikaScraper,
    ReportInfrastrukturScraper,
    ReportSenayeScraper,
)
from app.scrapers.worldbank import WorldBankScraper

ALL_SCRAPERS = [
    MarjaScraper,
    News1Scraper,
    OxuScraper,
    FedScraper,
    FedTenderScraper,
    ReportSenayeScraper,
    ReportEnergetikaScraper,
    ReportInfrastrukturScraper,
    EtenderScraper,
    EmlakScraper,
    CorporateTenderScraper,
    WorldBankScraper,
    BpEdpScraper,
    BossAzScraper,
    JobSearchScraper,
    MediaScraper,
    IntlTenderScraper,
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
    "ReportSenayeScraper",
    "ReportEnergetikaScraper",
    "ReportInfrastrukturScraper",
    "EtenderScraper",
    "EmlakScraper",
    "CorporateTenderScraper",
    "WorldBankScraper",
    "BpEdpScraper",
    "BossAzScraper",
    "JobSearchScraper",
    "MediaScraper",
    "IntlTenderScraper",
    "LinkedInScraper",
]
