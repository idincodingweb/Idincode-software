"""Reddit public JSON scraper — no auth needed.

Cari post/komentar di subreddit (r/SEO, r/PPC, r/dentistry, dst) di mana
author self-identify sebagai agency owner / freelancer / consultant.

Endpoints (public, no key):
  GET https://www.reddit.com/r/{sub}/search.json?q={q}&restrict_sr=1&limit=N&sort=relevance

Output: list[RedditBuyerLead] siap di-flatten ke CSV.
NB: Reddit suka throttle. Pakai User-Agent custom + delay tipis antar query.
"""
from __future__ import annotations

import asyncio
import html as _html_lib
import re
from dataclasses import dataclass
from typing import Optional

import httpx


_UA = (
    "Mozilla/5.0 (compatible; IdincodeBuyerHunter/1.0; "
    "+https://github.com/idincode/idincode-researche)"
)

_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json",
}

_TIMEOUT = 12.0

_BASE = "https://www.reddit.com"


# Indicator phrases: author self-identifies as agency owner / freelancer.
_BUYER_INDICATORS = (
    "agency owner", "i own an agency", "i run an agency", "i run a small agency",
    "my agency", "our agency", "founder of", "co-founder of",
    "freelance seo", "freelancer seo", "seo freelancer",
    "freelance google ads", "google ads freelancer", "ppc freelancer",
    "freelance ppc", "i'm a freelancer", "im a freelancer",
    "solo consultant", "marketing consultant",
    "i help dental", "i help dentists", "i work with dentists",
    "i work with dental", "i work with clinics",
    "boutique agency", "small marketing agency",
)


# Email & URL regex
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"https?://[^\s\)\]\}\"<>]+", re.IGNORECASE)

# Hint: own-domain (not reddit/social) — used as website signal.
_SOCIAL_DOMAINS = (
    "reddit.com", "redd.it", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "linkedin.com", "youtube.com", "youtu.be",
    "tiktok.com", "github.com", "medium.com", "imgur.com", "i.redd.it",
)


@dataclass
class RedditBuyerLead:
    """1 reddit post yang author-nya self-identify sebagai potential buyer."""
    subreddit: str
    author: str
    post_title: str
    post_url: str
    permalink: str
    snippet: str
    website: str = ""
    email: str = ""
    matched_indicators: str = ""
    score: int = 0


def _looks_like_buyer(text: str) -> list[str]:
    if not text:
        return []
    low = text.lower()
    hits = [ind for ind in _BUYER_INDICATORS if ind in low]
    return hits


def _first_external_url(text: str) -> str:
    if not text:
        return ""
    for url in _URL_RE.findall(text):
        u = url.rstrip(".,);]")
        low = u.lower()
        if any(s in low for s in _SOCIAL_DOMAINS):
            continue
        # ignore reddit-internal markdown img refs
        return u
    return ""


def _first_email(text: str) -> str:
    if not text:
        return ""
    for em in _EMAIL_RE.findall(text):
        return em.lower().strip(".,;:")
    return ""


def _decode(text: str) -> str:
    if not text:
        return ""
    return _html_lib.unescape(text)


async def _search_subreddit(
    client: httpx.AsyncClient,
    subreddit: str,
    query: str,
    limit: int,
) -> list[RedditBuyerLead]:
    url = f"{_BASE}/r/{subreddit}/search.json"
    params = {
        "q": query,
        "restrict_sr": "1",
        "limit": str(min(max(limit, 1), 100)),
        "sort": "relevance",
        "t": "year",
    }
    out: list[RedditBuyerLead] = []
    try:
        resp = await client.get(url, params=params)
    except Exception as e:  # noqa: BLE001
        print(f"[reddit] r/{subreddit} fail: {type(e).__name__}: {e}")
        return out

    if resp.status_code == 429:
        print(f"[reddit] r/{subreddit} 429 throttled, skip")
        return out
    if resp.status_code != 200:
        print(f"[reddit] r/{subreddit} HTTP {resp.status_code}")
        return out

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return out

    children = (data.get("data") or {}).get("children") or []
    for ch in children:
        d = (ch or {}).get("data") or {}
        author = str(d.get("author") or "").strip()
        title = _decode(str(d.get("title") or ""))
        selftext = _decode(str(d.get("selftext") or ""))
        permalink = str(d.get("permalink") or "")
        link_url = str(d.get("url") or "")
        score = int(d.get("score") or 0)
        if not author or author.lower() in ("[deleted]", "automoderator"):
            continue

        combined = f"{title}\n{selftext}"
        hits = _looks_like_buyer(combined)
        if not hits:
            continue

        website = _first_external_url(selftext) or (
            link_url if not any(s in link_url.lower() for s in _SOCIAL_DOMAINS) else ""
        )
        email = _first_email(combined)
        snippet = (selftext or title).strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."

        out.append(RedditBuyerLead(
            subreddit=subreddit,
            author=author,
            post_title=title[:200],
            post_url=link_url[:300],
            permalink=f"{_BASE}{permalink}" if permalink.startswith("/") else permalink,
            snippet=snippet,
            website=website[:200],
            email=email,
            matched_indicators=", ".join(hits[:4]),
            score=score,
        ))
    return out


async def hunt_reddit_buyers(
    queries: list[tuple[str, str]],
    *,
    limit_per_query: int = 25,
    delay_between_queries: float = 1.0,
) -> list[RedditBuyerLead]:
    """queries: list of (subreddit, query_string)."""
    if not queries:
        return []
    all_leads: list[RedditBuyerLead] = []
    seen_keys: set[tuple[str, str]] = set()  # (author, permalink)

    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        for i, (sub, q) in enumerate(queries):
            if i > 0 and delay_between_queries > 0:
                await asyncio.sleep(delay_between_queries)
            print(f"[reddit] r/{sub} q='{q}' ...")
            leads = await _search_subreddit(client, sub, q, limit_per_query)
            kept = 0
            for l in leads:
                key = (l.author.lower(), l.permalink)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                all_leads.append(l)
                kept += 1
            print(f"[reddit]   -> {kept} buyer-like posts (of {len(leads)} matched)")
    return all_leads
