# src/buyer_finder.py
"""Cari decision maker (CEO/Founder/Owner/Partner/MD) di agency target.

Pipeline per niche:
  1. DDG search "{niche keyword}" -> list of agency domains
  2. Untuk tiap domain, fetch homepage + /about /team /leadership /our-team
     /people /contact /staff /our-people
  3. Ekstrak nama + jabatan decision maker (heuristic, regex + tag context)
  4. Ekstrak semua email personal di page
  5. Match email per person dengan nama (substring match).
     ⚠️ GUESSED / INFERRED EMAIL DIHAPUS — kita hanya keep email yang
        LITERAL muncul di page (email_source="scraped", confidence=1.0).
        Kalau gak ada email scraped untuk si person, person di-DROP.
  6. MX validate domain.

Output: list[BuyerLead] siap di-export ke CSV.
"""
from __future__ import annotations

import asyncio
import html as _html_lib
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse

import httpx

from src.extras import (
    _HEADERS,
    _DEFAULT_TIMEOUT,
    _normalize_domain,
    extract_emails_from_html,
    validate_email_mx,
)


# ============================================================
# Data models
# ============================================================
@dataclass
class BuyerPerson:
    name: str
    title: str
    email: str
    email_confidence: float = 1.0   # always 1.0 — scraped only
    email_source: str = "scraped"   # always "scraped"


@dataclass
class BuyerLead:
    """1 agency yang berpotensi beli data leads lo."""
    agency_domain: str
    agency_name: str
    niche_keyword: str
    country: str
    mx_valid: Optional[bool] = None
    persons: list[BuyerPerson] = field(default_factory=list)
    # AI-generated (filled by analyst layer)
    outreach_angle: str = ""
    why_buy: str = ""


# ============================================================
# Constants
# ============================================================
_DECISION_MAKER_TITLES = (
    "ceo", "chief executive officer",
    "founder", "co-founder", "cofounder", "co founder",
    "owner", "co-owner",
    "managing director", "managing partner",
    "president",
    "principal",
    "partner",
    "director of marketing", "marketing director",
    "head of growth", "growth lead",
    "director", "vp", "vice president",
)

_TEAM_PATHS = (
    "/about", "/about-us", "/team", "/our-team", "/people",
    "/our-people", "/leadership", "/staff", "/who-we-are",
    "/the-team", "/meet-the-team", "/agency", "/company",
)

# Email noise (role-based, gak dipakai untuk personal lookup).
_ROLE_BASED_LOCALPARTS = frozenset((
    "info", "hello", "contact", "support", "admin", "office",
    "noreply", "no-reply", "sales", "marketing", "hr",
    "careers", "team", "enquiry", "enquiries", "general",
    "help", "feedback", "media", "press", "billing",
    "accounts", "accounting", "service", "services",
    "inquiries", "ask", "mail", "email",
))

# Common free-email domains -> skip karena bukan corporate
_FREE_EMAIL_DOMAINS = frozenset((
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "protonmail.com", "live.com",
    "mail.com", "gmx.com", "yandex.com",
))


# ============================================================
# 1. DDG agency discovery
# ============================================================
async def search_agencies(
    keyword: str,
    *,
    country: str = "US",
    limit: int = 30,
) -> list[str]:
    """Cari agency domain via DuckDuckGo (HTML + lite fallback).

    Return: list of bare domains (no scheme).
    """
    region_map = {
        "US": "us-en", "UK": "uk-en", "AU": "au-en",
        "CA": "ca-en", "GLOBAL": "wt-wt",
    }
    kl = region_map.get(country.upper(), "us-en")

    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://duckduckgo.com/",
    }

    endpoints = (
        ("POST", "https://html.duckduckgo.com/html/"),
        ("GET", "https://lite.duckduckgo.com/lite/"),
    )

    page_html = ""
    try:
        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers=browser_headers,
        ) as client:
            for method, url in endpoints:
                try:
                    if method == "POST":
                        resp = await client.post(url, data={"q": keyword, "kl": kl})
                    else:
                        resp = await client.get(url, params={"q": keyword, "kl": kl})
                    if resp.status_code == 200 and len(resp.text) > 500:
                        page_html = resp.text
                        break
                    print(f"[buyer] DDG {url} HTTP {resp.status_code}")
                except Exception as e:  # noqa: BLE001
                    print(f"[buyer] DDG {url} fail: {type(e).__name__}: {e}")
                    continue
    except Exception as e:  # noqa: BLE001
        print(f"[buyer] DDG client fail: {type(e).__name__}: {e}")
        return []

    if not page_html:
        return []

    urls: list[str] = []
    urls += re.findall(r'<a[^>]+class="result__url"[^>]*>([^<]+)</a>', page_html)
    urls += re.findall(r'<a[^>]+href="(https?://[^"]+)"', page_html)
    urls += [unquote(u) for u in re.findall(r'uddg=([^&"]+)', page_html)]

    seen: list[str] = []
    for u in urls:
        d = _normalize_domain(u)
        if not d or "." not in d:
            continue
        if _is_directory_site(d):
            continue
        if d in seen:
            continue
        seen.append(d)
        if len(seen) >= limit:
            break
    return seen


_DIRECTORY_DOMAINS = (
    "clutch.co", "g2.com", "trustpilot", "yelp", "agencyspotter",
    "designrush", "expertise.com", "upcity.com", "goodfirms",
    "linkedin.com", "facebook.com", "twitter.com", "instagram.com",
    "youtube.com", "wikipedia", "reddit.com", "quora.com",
    "indeed.com", "glassdoor", "crunchbase", "bbb.org",
    "yellowpages", "manta.com", "thumbtack", "duckduckgo",
    "google.com", "bing.com",
)


def _is_directory_site(domain: str) -> bool:
    low = domain.lower()
    return any(d in low for d in _DIRECTORY_DOMAINS)


# ============================================================
# 2. Page fetching
# ============================================================
async def _fetch_pages(domain: str, max_pages: int = 6) -> dict[str, str]:
    pages: dict[str, str] = {}
    candidates = [f"https://{domain}/"] + [
        f"https://{domain}{p}" for p in _TEAM_PATHS
    ]

    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
        headers=_HEADERS,
    ) as client:
        for url in candidates:
            if len(pages) >= max_pages:
                break
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                ct = resp.headers.get("content-type", "").lower()
                if "html" not in ct:
                    continue
                pages[url] = resp.text[:1_500_000]
            except Exception:  # noqa: BLE001
                continue
    return pages


# ============================================================
# 3. Person extraction (name + title)
# ============================================================
def _strip_html(html: str) -> str:
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"</(h[1-6]|p|div|li|td|tr|section|article|header)>", " ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<br\s*/?>", " ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = _html_lib.unescape(txt)
    txt = re.sub(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", " ", txt)
    txt = re.sub(r"https?://\S+", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt


_NAME_TOKEN_RE = re.compile(r"^[A-Z][a-z'\-]{1,20}\.?$")
_LOWER_FILLER = frozenset((
    "of", "and", "the", "for", "de", "van", "von", "la", "le", "&",
))


def _looks_like_name_token(tok: str) -> bool:
    return bool(_NAME_TOKEN_RE.match(tok))


def _grab_name(tokens: list[str], idx: int, direction: int) -> Optional[str]:
    parts: list[str] = []
    i = idx
    while 0 <= i < len(tokens) and tokens[i] in (",", "-", "|", "–", "—", ":"):
        i += direction

    title_words = {w for t in _DECISION_MAKER_TITLES for w in t.split()}

    while 0 <= i < len(tokens) and len(parts) < 4:
        t = tokens[i].strip(",.:;|-")
        if not t:
            break
        tl = t.lower()
        if tl in title_words:
            break
        if _looks_like_name_token(t):
            if direction == -1:
                parts.insert(0, t)
            else:
                parts.append(t)
            i += direction
            continue
        if len(parts) >= 1 and tl in _LOWER_FILLER and len(parts) < 3:
            if direction == -1:
                parts.insert(0, t)
            else:
                parts.append(t)
            i += direction
            continue
        break

    if len(parts) < 2:
        return None
    while parts and parts[0].lower() in _LOWER_FILLER:
        parts.pop(0)
    while parts and parts[-1].lower() in _LOWER_FILLER:
        parts.pop()
    if len(parts) < 2 or len(parts) > 4:
        return None
    return " ".join(parts)


def extract_people(htmls: Iterable[str]) -> list[tuple[str, str]]:
    seen: dict[str, tuple[str, str]] = {}
    title_set_lower = set(_DECISION_MAKER_TITLES)
    multi_word = sorted(
        (t for t in _DECISION_MAKER_TITLES if " " in t),
        key=lambda x: -len(x.split()),
    )

    for html in htmls:
        if not html:
            continue
        text = _strip_html(html)
        tokens = re.findall(r"[A-Za-z][A-Za-z\.\-']*|[,\-|–—:]", text)
        lower_tokens = [t.lower().rstrip(".") for t in tokens]

        i = 0
        while i < len(tokens):
            matched_title: Optional[str] = None
            matched_len = 0

            for mw in multi_word:
                parts = mw.split()
                n = len(parts)
                if i + n <= len(tokens) and lower_tokens[i:i+n] == parts:
                    matched_title = mw
                    matched_len = n
                    break

            if not matched_title and lower_tokens[i] in title_set_lower:
                matched_title = lower_tokens[i]
                matched_len = 1

            if matched_title:
                name_before = _grab_name(tokens, i - 1, -1)
                name_after = _grab_name(tokens, i + matched_len, +1)
                cand = name_before or name_after
                if cand and _is_valid_name(cand):
                    key = cand.lower()
                    existing_keys = list(seen.keys())
                    skip = False
                    for ek in existing_keys:
                        if key == ek:
                            skip = True
                            break
                        if key.endswith(" " + ek) or ek.endswith(" " + key):
                            if len(key) < len(ek):
                                del seen[ek]
                            else:
                                skip = True
                            break
                    if not skip:
                        seen[key] = (cand, matched_title.title())
                i += matched_len
                continue
            i += 1

    return list(seen.values())


_NAME_STOPWORDS = frozenset((
    "Our", "About", "Team", "Meet", "Contact", "Home", "Services",
    "Work", "Case", "Studies", "Blog", "Privacy", "Policy", "Cookie",
    "Terms", "Read", "More", "Learn", "Click", "Here", "View",
    "Marketing", "Agency", "Digital", "Healthcare", "Dental",
    "Medical", "Strategy", "Growth", "All", "Rights", "Reserved",
    "United", "States", "New", "York", "Los", "Angeles", "San",
    "Francisco", "Miami", "Chicago", "Dallas", "Atlanta", "Boston",
    "Free", "Get", "Started", "Book", "Schedule", "Consultation",
    "Email", "Phone", "Address", "Office", "Hours", "Monday",
    "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
))


def _is_valid_name(name: str) -> bool:
    parts = name.split()
    if len(parts) < 2 or len(parts) > 4:
        return False
    sw_count = sum(1 for p in parts if p in _NAME_STOPWORDS)
    if sw_count >= 1 and len(parts) <= 2:
        return False
    if sw_count >= 2:
        return False
    if all(p.isupper() for p in parts):
        return False
    return True


# ============================================================
# 4. Personal email scraping  (NO inference / guessing)
# ============================================================
def _extract_personal_emails(html: str, domain: str) -> list[str]:
    """Hanya email yang (a) di domain yang sama, dan (b) BUKAN role-based."""
    all_emails = extract_emails_from_html(html)
    out = []
    for e in all_emails:
        try:
            local, dom = e.split("@", 1)
        except ValueError:
            continue
        if dom.lower() != domain.lower():
            continue
        if local.lower() in _ROLE_BASED_LOCALPARTS:
            continue
        out.append(e.lower())
    return list(dict.fromkeys(out))  # dedupe preserving order


def _scraped_email_for_person(
    name: str,
    personal_emails: list[str],
) -> Optional[str]:
    """Match nama -> email scraped via substring. Return None kalau gak ketemu.

    Strategy: first/last/full-name fragment harus muncul di local-part.
    """
    parts = name.lower().split()
    if len(parts) < 2:
        return None
    first = re.sub(r"[^a-z]", "", parts[0])
    last = re.sub(r"[^a-z]", "", parts[-1])
    if not first or not last:
        return None
    for em in personal_emails:
        loc = em.split("@", 1)[0].lower()
        if (first in loc and last in loc) or loc == first or loc == last:
            return em
    return None


# ============================================================
# 5. Agency name guess (from <title>) — bukan email
# ============================================================
def _guess_agency_name(domain: str, html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]{1,160})</title>", html, re.IGNORECASE)
    if m:
        t = _html_lib.unescape(m.group(1)).strip()
        t = re.split(r"\s*[\|\-–—]\s*", t)[0].strip()
        if 2 < len(t) < 80:
            return t
    bare = domain.lower().split(".")
    return bare[0].title() if bare else domain


# ============================================================
# 6. Per-agency pipeline
# ============================================================
async def enrich_agency(
    domain: str,
    *,
    niche_keyword: str,
    country: str,
    max_persons: int,
) -> Optional[BuyerLead]:
    """Full enrichment untuk 1 agency. Return None kalau gak dapet decision
    maker dengan email yang BENERAN ada di page (no guessing)."""
    pages = await _fetch_pages(domain)
    if not pages:
        return None

    htmls = list(pages.values())
    homepage_html = next(iter(pages.values()), "")

    # 1. people
    people = extract_people(htmls)
    if not people:
        return None

    # 2. personal emails on page
    all_personal: list[str] = []
    seen_em: set[str] = set()
    for h in htmls:
        for em in _extract_personal_emails(h, domain):
            if em not in seen_em:
                seen_em.add(em)
                all_personal.append(em)

    # No personal email scraped -> can't ship anyone (no guessing path).
    if not all_personal:
        return None

    # 3. MX validate domain once (informational)
    mx = validate_email_mx(domain)

    # 4. build persons — keep only those whose email is actually scraped
    persons: list[BuyerPerson] = []
    used_emails: set[str] = set()
    for name, title in people:
        em = _scraped_email_for_person(name, all_personal)
        if not em or em in used_emails:
            continue
        used_emails.add(em)
        persons.append(BuyerPerson(
            name=name,
            title=title,
            email=em,
            email_confidence=1.0,
            email_source="scraped",
        ))
        if len(persons) >= max_persons:
            break

    if not persons:
        return None

    agency_name = _guess_agency_name(domain, homepage_html)

    return BuyerLead(
        agency_domain=domain,
        agency_name=agency_name,
        niche_keyword=niche_keyword,
        country=country,
        mx_valid=mx,
        persons=persons,
    )


# ============================================================
# 7. Top-level discovery untuk 1 niche
# ============================================================
async def find_buyers_for_niche(
    keyword: str,
    *,
    country: str,
    max_agencies: int,
    max_persons: int,
    max_concurrent: int = 4,
    skip_domains: Optional[set[str]] = None,
) -> list[BuyerLead]:
    """Cari semua agency + decision maker untuk 1 niche.

    skip_domains: kalau diisi (dari dedup DB), domain di-skip sebelum fetch.
    """
    print(f"\n[buyer] >>> Niche: '{keyword}' ({country}) — search agencies...")
    domains = await search_agencies(keyword, country=country, limit=max_agencies)
    print(f"[buyer]     {len(domains)} agency domains discovered")
    if not domains:
        return []

    if skip_domains:
        before = len(domains)
        domains = [d for d in domains if d.lower() not in skip_domains]
        skipped = before - len(domains)
        if skipped:
            print(f"[buyer]     [dedup] skip {skipped} agency yang udah pernah muncul")
        if not domains:
            return []

    sem = asyncio.Semaphore(max_concurrent)

    async def _bounded(d: str) -> Optional[BuyerLead]:
        async with sem:
            try:
                return await enrich_agency(
                    d,
                    niche_keyword=keyword,
                    country=country,
                    max_persons=max_persons,
                )
            except Exception as e:  # noqa: BLE001
                print(f"[buyer]     {d} fail: {type(e).__name__}: {e}")
                return None

    results = await asyncio.gather(*[_bounded(d) for d in domains])
    leads = [r for r in results if r and r.persons]
    print(
        f"[buyer]     -> {len(leads)} agency dengan decision maker valid "
        f"({sum(len(l.persons) for l in leads)} persons)"
    )
    return leads
