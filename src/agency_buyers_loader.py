"""Load agency_buyers.yaml -> typed config untuk find_agency_buyers.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AgencyNiche:
    keyword: str
    country: str = "US"


@dataclass
class RedditQuery:
    subreddit: str
    query: str


@dataclass
class AgencyBuyersConfig:
    country: str = "US"
    max_agencies_per_niche: int = 25
    max_persons_per_agency: int = 3
    max_concurrent: int = 4
    enable_reddit: bool = True
    reddit_post_limit_per_query: int = 25
    niches: list[AgencyNiche] = field(default_factory=list)
    reddit_queries: list[RedditQuery] = field(default_factory=list)


def load_agency_buyers(path: str | Path = "agency_buyers.yaml") -> AgencyBuyersConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"agency_buyers.yaml tidak ditemukan di {p.absolute()}."
        )

    with p.open("r", encoding="utf-8") as f:
        raw: Any = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError("agency_buyers.yaml harus dict di top-level.")

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("'defaults' harus dict.")

    niches_raw = raw.get("niches") or []
    if not isinstance(niches_raw, list) or not niches_raw:
        raise ValueError("'niches' wajib berisi minimal 1 entry.")

    default_country = str(defaults.get("country", "US")).strip() or "US"

    niches: list[AgencyNiche] = []
    for idx, n in enumerate(niches_raw):
        if not isinstance(n, dict):
            raise ValueError(f"niches[{idx}] bukan dict")
        kw = str(n.get("keyword", "")).strip()
        if not kw:
            raise ValueError(f"niches[{idx}] missing 'keyword'")
        country = str(n.get("country", default_country)).strip() or default_country
        niches.append(AgencyNiche(keyword=kw, country=country))

    reddit_block = raw.get("reddit") or {}
    reddit_queries: list[RedditQuery] = []
    if isinstance(reddit_block, dict):
        qs = reddit_block.get("queries") or []
        if isinstance(qs, list):
            for idx, q in enumerate(qs):
                if not isinstance(q, dict):
                    continue
                sub = str(q.get("subreddit", "")).strip().lstrip("r/").strip("/")
                query = str(q.get("query", "")).strip()
                if not sub or not query:
                    continue
                reddit_queries.append(RedditQuery(subreddit=sub, query=query))

    return AgencyBuyersConfig(
        country=default_country,
        max_agencies_per_niche=int(defaults.get("max_agencies_per_niche", 25)),
        max_persons_per_agency=int(defaults.get("max_persons_per_agency", 3)),
        max_concurrent=int(defaults.get("max_concurrent", 4)),
        enable_reddit=bool(defaults.get("enable_reddit", True)),
        reddit_post_limit_per_query=int(defaults.get("reddit_post_limit_per_query", 25)),
        niches=niches,
        reddit_queries=reddit_queries,
    )
