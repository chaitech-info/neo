"""robots.txt yoxlaması (sənəd §7: "Hər saytda robots.txt-a ... əməl edilir").

Standart `urllib.robotparser` `*` və `$` şablonlarını dəstəkləmir — məs. fed.az-ın
`Disallow: /*?` qaydası onunla səssizcə işləməzdi. Bu modul RFC 9309-u izləyir:
- bizim token-ə ("neoleadbot") uyğun qrup varsa o, yoxsa `*` qrupu tətbiq olunur;
- ən uzun uyğun qayda qalib gəlir, bərabərlikdə Allow;
- robots.txt 4xx → hər şeyə icazə; 5xx / şəbəkə xətası → heç nəyə icazə yoxdur;
- nəticə host üzrə 24 saat keşlənir.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import structlog

log = structlog.get_logger()

AGENT_TOKEN = "neoleadbot"
CACHE_TTL_S = 24 * 3600


class RobotsDisallowed(Exception):
    """URL robots.txt ilə qadağandır — sorğu göndərilmir."""


@dataclass
class RobotsRules:
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    disallow_all: bool = False

    def allows(self, path: str) -> bool:
        if self.disallow_all:
            return False
        best_len, allowed = -1, True
        for allow, pattern in self.rules:
            if _matches(pattern, path) and (
                len(pattern) > best_len or (len(pattern) == best_len and allow)
            ):
                best_len, allowed = len(pattern), allow
        return allowed


def _matches(pattern: str, path: str) -> bool:
    regex = re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    return re.match(regex, path) is not None


def parse_robots(text: str, agent: str = AGENT_TOKEN) -> RobotsRules:
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:  # yeni qrup başlayır
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(value.lower())
        elif key in ("allow", "disallow") and agents:
            in_rules = True
            if value:  # boş "Disallow:" = heç nə qadağan deyil
                rules.append((key == "allow", value))
    if agents:
        groups.append((agents, rules))

    specific = [r for a, r in groups if any(ua != "*" and ua in agent for ua in a)]
    chosen = specific or [r for a, r in groups if "*" in a]
    return RobotsRules(rules=[rule for group in chosen for rule in group])


_cache: dict[str, tuple[float, RobotsRules]] = {}


def rules_for(url: str, client: httpx.Client) -> RobotsRules:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    cached = _cache.get(origin)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_S:
        return cached[1]

    try:
        resp = client.get(f"{origin}/robots.txt")
        if resp.status_code >= 500:
            # Müvəqqəti server xətası (məs. etender 502) — bu run-da oxumuruq,
            # amma keşləmirik ki, bütün gün bloklanmasın.
            log.warning("robots_server_error", origin=origin, status=resp.status_code)
            return RobotsRules(disallow_all=True)
        if resp.status_code >= 400:
            rules = RobotsRules()
        else:
            rules = parse_robots(resp.text)
    except httpx.HTTPError:
        # Əlçatmazlıq müvəqqəti ola bilər — keşləmirik, növbəti run yenidən yoxlasın.
        log.warning("robots_fetch_failed", origin=origin)
        return RobotsRules(disallow_all=True)

    _cache[origin] = (time.monotonic(), rules)
    return rules


def check_allowed(url: str, client: httpx.Client) -> None:
    parts = urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    if not rules_for(url, client).allows(path):
        log.warning("robots_disallowed", url=url)
        raise RobotsDisallowed(url)
