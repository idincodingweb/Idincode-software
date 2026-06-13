"""Agency Buyer Finder — cari owner agency kecil / freelancer yang BELI data leads.

Beda sama src/buyer_finder.py:
  - Target: small agency (2-20), boutique, freelancer SEO/PPC.
  - Tambah ekstraksi PHONE (tel:, regex).
  - Tambah ekstraksi CEO/Founder via HYBRID:
        1. heuristic dari extract_people (re-use buyer_finder)
        2. AI fallback (agency_buyer_ai.py) kalau heuristic gagal
  - Output: AgencyBuyerLead dengan website + email + phone + ceo_name.

Email rule v3.1: LITERAL only (scraped). No guessing.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from src.buyer_finder import (
    _DECISION_MAKER_TITLES,
    _TEAM_PATHS,
    _extract_personal_emails,
    _guess_agency_name,
    _is_directory_site,
    extract_people,
    search_agencies,
)
from src.extras import (
    _DEFAULT_TIMEOUT,
    _HEADERS,
    _normalize_domain,
    validate_email_mx,
)


# ============================================================
# Models
# ============================================================
@dataclass
class AgencyBuyerLead:
    source: str            # "website" or "reddit"
    website: str           # bare domain (no scheme) for website source; URL for reddit
    agency_name: str
    niche_keyword: str
    country: str
    email: str = ""
    phone: str = ""
    ceo_name: str = ""
    ceo_title: str = ""
    ceo_source: str = ""   # "heuristic" | "ai" | ""
    mx_valid: Optional[bool] = None
    extra_emails: list[str] = field(default_factory=list)
    extra_phones: list[str] = field(default_factory=list)
    notes: str = ""


# ============================================================
# Phone extraction
# ============================================================
# tel: links — most reliable
_TEL_HREF_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.IGNORECASE)

# Free-form phone regex (intl + local). Conservative to avoid false positives.
_PHONE_RE = re.compile(
    r"(?<![\w])"
    r"(?:\+?\d{1,3}[\s\-.])?"            # country code
    r"(?:\(?\d{2,4}\)?[\s\-.])?"         # area code (optional)
    r"\d{3,4}[\s\-.]\d{3,4}"              # main
    r"(?![\w@])"
)


def _normalize_phone(raw: str) -> str:
    # Keep digits + leading '+', strip everything else
    cleaned = re.sub(r"[^\d+]", "", raw)
    if cleaned.startswith("+"):
        cleaned = "+" + re.sub(r"\D", "", cleaned[1:])
    # length sanity: 8-15 digits (E.164-ish)
    digits = re.sub(r"\D", "", cleaned)
    if not (8 <= len(digits) <= 15):
        return ""
    return cleaned


def extract_phones_from_html(html: str, *, limit: int = 8) -> list[str]:
    if not html:
        return []
    out: list[str] = []
    seen: set[str] = set()

    # 1. tel: hrefs — high signal
    for m in _TEL_HREF_RE.findall(html):
        p = _normalize_phone(m)
        if p and p not in seen:
            seen.add(p)
            out.append(p)
            if len(out) >= limit:
                return out

    # 2. plain text regex
    for m in _PHONE_RE.findall(html):
        p = _normalize_phone(m)
        if not p:
            continue
        if p in seen:
            continue
        # crude noise filter: long runs of same digit (e.g. 111-111-1111)
        digits = re.sub(r"\D", "", p)
        if len(set(digits)) <= 2:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= limit:
            break
    return out


# ============================================================
# Page fetch
# ============================================================
async def _fetch_pages(
    client: httpx.AsyncClient, domain: str, max_pages: int = 6
) -> dict[str, str]:
    pages: dict[str, str] = {}
    candidates = [f"https://{domain}/"] + [f"https://{domain}{p}" for p in _TEAM_PATHS]
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
# CEO/Founder selection — heuristic first
# ============================================================
# Priority weight: CEO/Founder/Owner first.
_CEO_TITLE_WEIGHT = {
    "ceo": 100, "chief executive officer": 100,
    "founder": 95, "co-founder": 92, "cofounder": 92, "co founder": 92,
    "owner": 90, "co-owner": 88,
    "managing director": 80, "managing partner": 78,
    "president": 70, "principal": 60, "partner": 55,
}


def _rank_ceo(people: list[tuple[str, str]]) -> Optional[tuple[str, str]]:
    """Return best (name, title) candidate for CEO/Founder slot."""
    best: Optional[tuple[int, tuple[str, str]]] = None
    for name, title in people:
        w = _CEO_TITLE_WEIGHT.get(title.lower(), 0)
        if w == 0:
            # generic fallback if no top-tier match
            w = 10
        if best is None or w > best[0]:
            best = (w, (name, title))
    if not best or best[0] < 50:
        return None
    return best[1]


# ============================================================
# Per-agency enrichment
# ============================================================
async def enrich_agency_buyer(
    client: httpx.AsyncClient,
    domain: str,
    *,
    niche_keyword: str,
    country: str,
    use_ai_fallback: bool,
) -> Optional[AgencyBuyerLead]:
    pages = await _fetch_pages(client, domain)
    if not pages:
        return None
    htmls = list(pages.values())
    homepage_html = next(iter(pages.values()), "")

    # CEO via heuristic
    people = extract_people(htmls)
    ceo_pair = _rank_ceo(people)
    ceo_source = ""
    ceo_name = ""
    ceo_title = ""
    if ceo_pair:
        ceo_name, ceo_title = ceo_pair
        ceo_source = "heuristic"

    # AI fallback if heuristic didn't find one
    if not ceo_name and use_ai_fallback:
        try:
            from src.agency_buyer_ai import ai_extract_ceo
            ai = await ai_extract_ceo(domain, homepage_html, htmls)
            if ai and ai.get("name"):
                ceo_name = str(ai["name"]).strip()
                ceo_title = str(ai.get("title", "Founder")).strip() or "Founder"
                ceo_source = "ai"
        except Exception as e:  # noqa: BLE001
            print(f"[agency-buyer] {domain} AI fallback fail: {type(e).__name__}: {e}")

    # Emails
    all_emails: list[str] = []
    seen_em: set[str] = set()
    for h in htmls:
        for em in _extract_personal_emails(h, domain):
            if em not in seen_em:
                seen_em.add(em)
                all_emails.append(em)

    primary_email = ""
    if ceo_name and all_emails:
        parts = ceo_name.lower().split()
        if len(parts) >= 2:
            first = re.sub(r"[^a-z]", "", parts[0])
            last = re.sub(r"[^a-z]", "", parts[-1])
            for em in all_emails:
                loc = em.split("@", 1)[0]
                if (first and last and first in loc and last in loc) or loc == first or loc == last:
                    primary_email = em
                    break
    if not primary_email and all_emails:
        primary_email = all_emails[0]

    # Phones
    all_phones: list[str] = []
    seen_ph: set[str] = set()
    for h in htmls:
        for ph in extract_phones_from_html(h):
            if ph not in seen_ph:
                seen_ph.add(ph)
                all_phones.append(ph)
    primary_phone = all_phones[0] if all_phones else ""

    # If we have NOTHING actionable (no email AND no phone AND no CEO), drop.
    if not primary_email and not primary_phone and not ceo_name:
        return None

    mx = validate_email_mx(domain) if primary_email else None
    agency_name = _guess_agency_name(domain, homepage_html)

    return AgencyBuyerLead(
        source="website",
        website=domain,
        agency_name=agency_name,
        niche_keyword=niche_keyword,
        country=country,
        email=primary_email,
        phone=primary_phone,
        ceo_name=ceo_name,
        ceo_title=ceo_title,
        ceo_source=ceo_source,
        mx_valid=mx,
        extra_emails=[e for e in all_emails if e != primary_email][:5],
        extra_phones=[p for p in all_phones if p != primary_phone][:5],
    )


# ============================================================
# Top-level: discover per niche
# ============================================================
async def find_agency_buyers_for_niche(
    keyword: str,
    *,
    country: str,
    max_agencies: int,
    max_concurrent: int = 4,
    use_ai_fallback: bool = True,
    skip_domains: Optional[set[str]] = None,
) -> list[AgencyBuyerLead]:
    print(f"\n[agency-buyer] >>> Niche: '{keyword}' ({country})")
    domains = await search_agencies(keyword, country=country, limit=max_agencies)
    print(f"[agency-buyer]     {len(domains)} domains discovered")
    if not domains:
        return []

    if skip_domains:
        before = len(domains)
        domains = [d for d in domains if d.lower() not in skip_domains]
        skipped = before - len(domains)
        if skipped:
            print(f"[agency-buyer]     [dedup] skip {skipped} domain seen")
        if not domains:
            return []

    sem = asyncio.Semaphore(max_concurrent)

    async def _bounded(client: httpx.AsyncClient, d: str):
        async with sem:
            try:
                return await enrich_agency_buyer(
                    client, d,
                    niche_keyword=keyword,
                    country=country,
                    use_ai_fallback=use_ai_fallback,
                )
            except Exception as e:  # noqa: BLE001
                print(f"[agency-buyer]     {d} fail: {type(e).__name__}: {e}")
                return None

    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT, follow_redirects=True, headers=_HEADERS,
    ) as client:
        results = await asyncio.gather(*[_bounded(client, d) for d in domains])

    leads = [r for r in results if r]
    print(f"[agency-buyer]     -> {len(leads)} agency buyers extracted")
    return leads
