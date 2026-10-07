"""Mənbə namizədlərini yoxlayır (yalnız oxuyur, bazaya/koda toxunmur).

Hər ünvan üçün: HTTP status, yönləndirmə, robots.txt statusu və `User-agent: *` qadağaları,
RSS/Atom linki, mətn həcmi və səhifədəki "tender/satınalma/elan/xəbər" link nümunələri.
Nəticə: scraper yazmazdan ƏVVƏL saytın qoşula bilib-bilməyəcəyini göstərən cədvəl.

İstifadə:
    python -m scripts.probe_sources https://metro.gov.az https://abc.az
    python -m scripts.probe_sources --file urls.txt
Qayda (docs/knowledge-base/03-site-research.md): robots.txt 5xx və ya bot qoruması (403)
"qoşulmaz" sayılır — bu skript onları keçməyə CƏHD ETMİR, yalnız nəticəni göstərir.
"""
from __future__ import annotations

import argparse
import re
import sys
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import DEFAULT_USER_AGENT

TOPIC = re.compile(r"tender|satın|satin|procure|müsabiq|musabiq|elan|announce|vendor|supplier|vacanc|vakans|career|news|xeber|xəbər|press", re.I)


def probe(url: str, client: httpx.Client) -> dict:
    parsed = urlparse(url if "//" in url else f"https://{url}")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    row: dict = {"url": url, "status": None, "final": None, "robots": None, "disallow": [], "rss": [], "size": 0, "links": []}
    try:
        resp = client.get(url if "//" in url else f"https://{url}")
    except httpx.HTTPError as exc:
        row["status"] = type(exc).__name__
        return row
    row.update(status=resp.status_code, final=str(resp.url), size=len(resp.content))
    if resp.status_code < 400:
        soup = BeautifulSoup(resp.text, "lxml")
        row["rss"] = [urljoin(str(resp.url), link["href"]) for link in
                      soup.select("link[rel=alternate][type*=rss], link[rel=alternate][type*=atom]") if link.get("href")][:2]
        seen: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)[:50]
            href = urljoin(str(resp.url), a["href"])
            if (TOPIC.search(href) or TOPIC.search(text)) and href not in seen and urlparse(href).netloc == urlparse(str(resp.url)).netloc:
                seen[href] = text
        row["links"] = list(seen.items())[:5]
    try:
        robots = client.get(f"{origin}/robots.txt")
        row["robots"] = robots.status_code
        if robots.status_code == 200:
            applies = False
            for line in robots.text.replace("\r", "").splitlines():
                key, _, value = line.partition(":")
                key, value = key.strip().lower(), value.strip()
                if key == "user-agent":
                    applies = value == "*"
                elif applies and key == "disallow" and value:
                    row["disallow"].append(value)
    except httpx.HTTPError as exc:
        row["robots"] = type(exc).__name__
    return row


def verdict(row: dict) -> str:
    status, robots = row["status"], row["robots"]
    if not isinstance(status, int):
        return f"CAVAB YOX ({status})"
    if status in (401, 403, 429):
        return f"BLOK ({status})"
    if status >= 400:
        return f"XƏTA ({status})"
    if isinstance(robots, int) and robots >= 500:
        return f"ROBOTS {robots} → qoşulmaz"
    if "/" in row["disallow"]:
        return "ROBOTS hamısını qadağan edir"
    return "OK" + (" · RSS var" if row["rss"] else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--file", help="hər sətirdə bir ünvan")
    args = ap.parse_args()
    urls = list(args.urls)
    if args.file:
        urls += [ln.strip() for ln in open(args.file, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    if not urls:
        ap.error("ən azı bir ünvan verin")

    with httpx.Client(headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=15.0, follow_redirects=True) as client:
        for url in urls:
            row = probe(url, client)
            print(f"{verdict(row):<34} {row['url']}")
            print(f"    status={row['status']} robots={row['robots']} size={row['size']} final={row['final']}")
            if row["disallow"]:
                print(f"    disallow: {', '.join(row['disallow'][:6])}")
            for feed in row["rss"]:
                print(f"    rss: {feed}")
            for href, text in row["links"]:
                print(f"    → {text or '-':<50} {href}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
