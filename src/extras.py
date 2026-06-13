# src/extras.py
"""Zero-budget enrichment add-ons.

Semua fitur di sini TIDAK butuh API berbayar. Cuma butuh:
- httpx (HTTP requests)
- dnspython (MX lookup)
- stdlib (re, socket, smtplib)

Modul ini di-import oleh pipeline.py SETELAH enrich_all() jalan, untuk
nge-enrich tambahan: email, revenue estimate, ad detection, competitor.

Setiap fungsi WAJIB graceful-fail. Kalau gagal, return default kosong —
JANGAN throw. Pipeline harus tetep jalan walau extras fail.
"""
from __future__ import annotations

import asyncio
import re
import socket
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

try:
    import dns.resolver  # type: ignore
    _HAS_DNS = True
except ImportError:
    _HAS_DNS = False


# ============================================================
# Constants
# ============================================================
_USER_AGENT = (
    "Mozilla/5.0 (compatible; ApexResearchBot/1.0; "
    "+https://github.com/idincode/idincode-researche)"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_DEFAULT_TIMEOUT = 12.0
_MAX_CONTACT_PAGE_BYTES = 1_500_000

# Email regex — strict-ish, no trailing punctuation
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Ignore common noise emails
_EMAIL_BLOCKLIST_SUBSTR = (
    "example.com",
    "yourdomain",
    "domain.com",
    "sentry.io",
    "wixpress.com",
    "no-reply@",
    "noreply@",
    "@sentry",
    "@2x.png",
    "@3x.png",
    ".png@",
    ".jpg@",
    ".svg@",
    ".webp@",
)

# Role-based local-parts — shared inbox, NOT decision maker.
# Dipakai buat filter "personal_only" mode di extract_emails_from_html.
_ROLE_BASED_LOCALPARTS = frozenset((
    "info", "hello", "contact", "support", "admin", "office",
    "noreply", "no-reply", "sales", "marketing", "hr",
    "careers", "team", "enquiry", "enquiries", "general",
    "help", "feedback", "media", "press", "billing",
    "accounts", "accounting", "service", "services",
    "inquiries", "ask", "mail", "email", "webmaster", "postmaster",
    "abuse", "privacy", "legal", "compliance",
))



# ============================================================
# 1. Email Enrichment
# ============================================================
def extract_emails_from_html(html: str, *, personal_only: bool = False) -> list[str]:
    """Scrape email addresses dari HTML.

    Args:
        html: raw HTML.
        personal_only: kalau True, filter out role-based local-parts
            (info@, hello@, contact@, support@, dst). Default False utk
            backward-compat.

    Handle 3 case:
    - Plain text: john@clinic.com
    - mailto links: <a href="mailto:john@clinic.com">
    - HTML-entity encoded: john&#64;clinic.com (decode dulu)
    """
    if not html:
        return []

    cleaned = (
        html.replace("&#64;", "@")
        .replace("&#x40;", "@")
        .replace("[at]", "@")
        .replace("(at)", "@")
    )

    found = set()
    for m in _EMAIL_RE.findall(cleaned):
        email = m.lower().strip(".,;:")
        if _is_noise_email(email):
            continue
        if personal_only and _is_role_based(email):
            continue
        found.add(email)

    return sorted(found)


def _is_role_based(email: str) -> bool:
    try:
        local = email.split("@", 1)[0].lower()
    except (IndexError, AttributeError):
        return False
    return local in _ROLE_BASED_LOCALPARTS




def _is_noise_email(email: str) -> bool:
    """Filter image assets, placeholders, sentry, etc."""
    low = email.lower()
    return any(b in low for b in _EMAIL_BLOCKLIST_SUBSTR)


_CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/contactus",
    "/kontak",
    "/hubungi-kami",
    "/about",
    "/about-us",
    "/team",
    "/staff",
]


async def fetch_contact_page_emails(
    domain: str,
    base_html: str = "",
) -> list[str]:
    """Cari email dari halaman /contact, /about, dsb.

    Strategi:
    1. Cari link contact di base_html (kalau ada).
    2. Fallback: probe path umum (/contact, /about, dst).
    3. Scrape email dari setiap halaman, max 3 halaman.
    """
    candidates: list[str] = []

    # 1. Cari link dari base HTML
    if base_html:
        for href in _extract_contact_links(base_html, domain):
            if href not in candidates:
                candidates.append(href)

    # 2. Fallback: probe common paths
    for path in _CONTACT_PATHS:
        url = f"https://{domain}{path}"
        if url not in candidates:
            candidates.append(url)

    emails: set[str] = set()
    fetched = 0
    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
        headers=_HEADERS,
    ) as client:
        for url in candidates:
            if fetched >= 3:
                break
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                ct = resp.headers.get("content-type", "").lower()
                if "html" not in ct:
                    continue
                text = resp.text[:_MAX_CONTACT_PAGE_BYTES]
                for e in extract_emails_from_html(text):
                    # Prefer emails matching the domain
                    emails.add(e)
                fetched += 1
            except Exception:  # noqa: BLE001
                continue

    return sorted(emails)


def _extract_contact_links(html: str, domain: str) -> list[str]:
    """Extract internal links yang kemungkinan halaman contact/about."""
    # Cari href yang mengandung keyword contact/about/team
    pattern = re.compile(
        r'href=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    out: list[str] = []
    keywords = ("contact", "kontak", "about", "team", "staff", "hubungi")
    for href in pattern.findall(html):
        low = href.lower()
        if not any(k in low for k in keywords):
            continue
        # Normalize ke absolute URL
        if href.startswith("http"):
            parsed = urlparse(href)
            if domain not in parsed.netloc:
                continue  # external link
            out.append(href)
        elif href.startswith("/"):
            out.append(f"https://{domain}{href}")
        # ignore mailto:, tel:, javascript:
    return out[:5]


# ============================================================
# 2. Email pattern guesser (untuk decision maker)
# ============================================================
_COMMON_PATTERNS = (
    "info",
    "contact",
    "hello",
    "admin",
    "office",
    "support",
    "marketing",
    "sales",
    "owner",
    "manager",
)


def guess_email_patterns(domain: str) -> list[str]:
    """Generate guess email addresses berdasarkan domain.

    NOTE: Ini tebakan kasar — buyer harus validasi sendiri sebelum send.
    Tetep useful sebagai starting list buat outreach.
    """
    if not domain:
        return []
    # Bersihkan domain (buang www., port, path)
    d = domain.lower().strip()
    d = d.replace("https://", "").replace("http://", "").rstrip("/")
    if d.startswith("www."):
        d = d[4:]
    if "/" in d:
        d = d.split("/", 1)[0]

    return [f"{p}@{d}" for p in _COMMON_PATTERNS]


# ============================================================
# 3. Email Validator (MX + format)
# ============================================================
def validate_email_mx(domain: str) -> Optional[bool]:
    """Cek apakah domain punya MX record (= bisa terima email).

    Return:
        True  → MX exists, email kemungkinan deliverable
        False → MX gak ada, email pasti gak deliverable
        None  → DNS lib gak ada / lookup error (jangan asumsi)
    """
    if not domain or not _HAS_DNS:
        return None

    d = domain.lower().strip()
    if d.startswith("www."):
        d = d[4:]

    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5.0
        resolver.timeout = 3.0
        answers = resolver.resolve(d, "MX")
        return len(list(answers)) > 0
    except Exception:  # noqa: BLE001
        # NXDOMAIN, no MX, timeout, dst — semua treat sebagai "no MX"
        # tapi return False, BUKAN None (kita tau hasilnya)
        try:
            # Last-ditch: cek apakah ada A record (sebagian server pake A)
            socket.gethostbyname(d)
            return False  # ada A tapi gak ada MX → masih bisa attempt
        except Exception:  # noqa: BLE001
            return False


# ============================================================
# 4. Revenue Estimation (heuristic, 1-5)
# ============================================================
def estimate_revenue_tier(
    html: str,
    domain: str,
    location: Optional[str] = None,
) -> tuple[str, int]:
    """Heuristik revenue dari sinyal di HTML.

    Score 1-5:
        1 = micro  (solo practice / mom-and-pop)
        2 = small  (1 lokasi, < 10 staff signal)
        3 = mid    (multi-lokasi atau ada blog/SEO aktif)
        4 = large  (chain / franchise signal)
        5 = enterprise (national chain / public company signal)

    NOTE: Cuma estimasi kasar — gak ganti due-diligence beneran.
    """
    if not html:
        return ("unknown", 0)

    low = html.lower()
    signals = 0

    # Phone numbers (lebih dari 3 nomor = multi-lokasi)
    phone_count = len(re.findall(r"\+?\d[\d\-\s\(\)]{8,}\d", html))
    if phone_count >= 5:
        signals += 2
    elif phone_count >= 2:
        signals += 1

    # Multi-location keywords
    multiloc_keywords = (
        "locations",
        "our offices",
        "branches",
        "cabang",
        "lokasi kami",
        "find a location",
        "store locator",
    )
    if any(k in low for k in multiloc_keywords):
        signals += 2

    # Franchise / chain signals
    chain_keywords = (
        "franchise",
        "nationwide",
        "across the country",
        "international",
        "our team of",
    )
    if any(k in low for k in chain_keywords):
        signals += 2

    # Blog / content marketing aktif (= ada budget marketing)
    blog_keywords = ("blog", "articles", "news", "insights", "resources")
    if sum(1 for k in blog_keywords if k in low) >= 2:
        signals += 1

    # Career / hiring page (= growing)
    if any(k in low for k in ("careers", "join our team", "we're hiring", "open positions")):
        signals += 1

    # Press / media coverage
    if any(k in low for k in ("featured in", "as seen on", "press releases", "in the news")):
        signals += 2

    # Schema.org LocalBusiness with @type Organization (formal entity)
    if '"@type":"organization"' in low.replace(" ", "") or "schema.org/organization" in low:
        signals += 1

    # Map score → tier
    if signals >= 7:
        return ("enterprise", 5)
    if signals >= 5:
        return ("large", 4)
    if signals >= 3:
        return ("mid", 3)
    if signals >= 1:
        return ("small", 2)
    return ("micro", 1)


# ============================================================
# 5. Ad Detection (Meta Ad Library scrape)
# ============================================================
async def detect_meta_ads(
    domain: str,
    business_name: Optional[str] = None,
    country: str = "US",
) -> tuple[Optional[bool], Optional[int]]:
    """Cek apakah domain/bisnis lagi running iklan di Meta Ad Library.

    Return: (is_running, approximate_count)
        is_running = None kalau gak bisa dideteksi
                     True kalau ada minimal 1 ad active
                     False kalau cleanly nemu 0 hasil

    NOTE: Meta Ad Library suka berubah markup. Kita pake heuristik
    text-presence, BUKAN HTML parsing strict. Best-effort.
    """
    # Query string: prefer business name, fallback ke domain stripped
    query = business_name or _derive_business_name(domain)
    if not query:
        return (None, None)

    url = (
        "https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type=all&country={country}"
        f"&q={query}&search_type=keyword_unordered"
    )

    try:
        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return (None, None)

            html = resp.text.lower()

            # FB Ad Library bilang "~N results" or "0 results"
            m = re.search(r"~?(\d+[\d,]*)\s+results", html)
            if m:
                count = int(m.group(1).replace(",", ""))
                return (count > 0, count)

            # Heuristik lain: kalau page mention "no ads to show" / "0 result"
            if "no ads to show" in html or "0 results" in html:
                return (False, 0)

            # Kalau ada kata "active" + nama domain → kemungkinan ada
            if domain.lower() in html and "library_id" in html:
                return (True, None)

            return (None, None)
    except Exception:  # noqa: BLE001
        return (None, None)


def _derive_business_name(domain: str) -> str:
    """Tebak business name dari domain (e.g. drsmiledental.com → drsmiledental)."""
    d = domain.lower().replace("www.", "")
    # Strip TLD
    parts = d.split(".")
    if len(parts) >= 2:
        return parts[0]
    return d


# ============================================================
# 6. Competitor Discovery (DuckDuckGo HTML scrape — no API key)
# ============================================================
async def find_competitors(
    domain: str,
    niche: Optional[str] = None,
    location: Optional[str] = None,
    limit: int = 5,
) -> list[str]:
    """Cari competitor domain via DuckDuckGo HTML search.

    Query template: "{niche} {location} -site:{domain}"

    Return: list of competitor domains (tanpa scheme).

    NOTE: DDG HTML kadang rate-limit. Kalau fail → return [].
    """
    if not niche and not location:
        return []

    query_parts = []
    if niche:
        query_parts.append(niche.replace("_", " "))
    if location:
        query_parts.append(location)
    query_parts.append(f"-site:{domain}")
    query = " ".join(query_parts)

    url = "https://html.duckduckgo.com/html/"

    try:
        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers=_HEADERS,
        ) as client:
            resp = await client.post(url, data={"q": query})
            if resp.status_code != 200:
                return []

            # Extract result URLs
            html = resp.text
            urls = re.findall(
                r'<a[^>]+class="result__url"[^>]*>([^<]+)</a>',
                html,
            )
            if not urls:
                # Fallback: cari semua "uddg=" redirect URL & decode
                urls = re.findall(r'uddg=([^&"]+)', html)
                from urllib.parse import unquote
                urls = [unquote(u) for u in urls]

            seen: list[str] = []
            for u in urls:
                d = _normalize_domain(u)
                if not d or d == domain.lower():
                    continue
                if d in seen:
                    continue
                seen.append(d)
                if len(seen) >= limit:
                    break
            return seen
    except Exception:  # noqa: BLE001
        return []


def _normalize_domain(url_or_domain: str) -> str:
    """Convert URL/domain ke bare domain (lowercase, no scheme/path)."""
    s = url_or_domain.strip().lower()
    s = s.replace("https://", "").replace("http://", "")
    s = s.split("/", 1)[0]
    s = s.split(" ", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s


# ============================================================
# Aggregator — dipanggil dari pipeline.py
# ============================================================
async def enrich_extras(
    enrichment,  # EnrichmentResult — duck-typed (avoid circular import)
    *,
    base_html: str = "",
    enable_emails: bool = True,
    enable_revenue: bool = True,
    enable_ads: bool = False,  # default OFF — slow & flaky
    enable_competitors: bool = False,  # default OFF — slow
) -> dict:
    """Run all extras enrichment untuk 1 lead. Return dict siap di-merge ke lead.

    Args:
        enrichment: EnrichmentResult (with .domain, .niche, .location)
        base_html: optional pre-fetched HTML (untuk re-use, hindari double fetch)
        enable_*: granular toggle per feature

    Return: dict dengan key:
        emails_found: list[str]
        email_guesses: list[str]
        mx_valid: Optional[bool]
        revenue_tier: str
        revenue_score: int
        running_meta_ads: Optional[bool]
        meta_ads_count: Optional[int]
        competitors: list[str]
    """
    domain = getattr(enrichment, "domain", "")
    niche = getattr(enrichment, "niche", None)
    location = getattr(enrichment, "location", None)

    result = {
        "emails_found": [],
        "email_guesses": [],
        "mx_valid": None,
        "revenue_tier": "unknown",
        "revenue_score": 0,
        "running_meta_ads": None,
        "meta_ads_count": None,
        "competitors": [],
    }

    if not domain:
        return result

    # 1. Email enrichment — personal-only (filter info@/hello@/contact@ dst).
    #    Buyer lo (agency) butuh email decision maker, BUKAN shared inbox.
    if enable_emails:
        try:
            emails_html = (
                extract_emails_from_html(base_html, personal_only=True)
                if base_html else []
            )
            emails_contact = await fetch_contact_page_emails(domain, base_html)
            # fetch_contact_page_emails udah pre-filter sebagian, apply lagi
            emails_contact = [e for e in emails_contact if not _is_role_based(e)]
            all_emails = sorted(set(emails_html) | set(emails_contact))
            result["emails_found"] = all_emails
            result["email_guesses"] = guess_email_patterns(domain)
            result["mx_valid"] = validate_email_mx(domain)
        except Exception as e:  # noqa: BLE001
            print(f"[extras] {domain} email fail: {type(e).__name__}: {e}")


    # 2. Revenue estimation
    if enable_revenue and base_html:
        try:
            tier, score = estimate_revenue_tier(base_html, domain, location)
            result["revenue_tier"] = tier
            result["revenue_score"] = score
        except Exception as e:  # noqa: BLE001
            print(f"[extras] {domain} revenue fail: {type(e).__name__}: {e}")

    # 3. Meta ads detection
    if enable_ads:
        try:
            is_running, count = await detect_meta_ads(domain)
            result["running_meta_ads"] = is_running
            result["meta_ads_count"] = count
        except Exception as e:  # noqa: BLE001
            print(f"[extras] {domain} ads fail: {type(e).__name__}: {e}")

    # 4. Competitor discovery
    if enable_competitors:
        try:
            comps = await find_competitors(domain, niche=niche, location=location)
            result["competitors"] = comps
        except Exception as e:  # noqa: BLE001
            print(f"[extras] {domain} competitors fail: {type(e).__name__}: {e}")

    return result


async def enrich_extras_batch(
    enrichments: list,
    *,
    base_htmls: Optional[dict[str, str]] = None,
    enable_emails: bool = True,
    enable_revenue: bool = True,
    enable_ads: bool = False,
    enable_competitors: bool = False,
    max_concurrent: int = 4,
) -> list[dict]:
    """Batch enrich extras dengan concurrency cap."""
    base_htmls = base_htmls or {}
    sem = asyncio.Semaphore(max_concurrent)

    async def _bounded(e):
        async with sem:
            html = base_htmls.get(getattr(e, "domain", ""), "")
            return await enrich_extras(
                e,
                base_html=html,
                enable_emails=enable_emails,
                enable_revenue=enable_revenue,
                enable_ads=enable_ads,
                enable_competitors=enable_competitors,
            )

    print(
        f"[extras] enriching {len(enrichments)} leads "
        f"(emails={enable_emails}, revenue={enable_revenue}, "
        f"ads={enable_ads}, competitors={enable_competitors})"
    )
    results = await asyncio.gather(*[_bounded(e) for e in enrichments])
    return list(results)
