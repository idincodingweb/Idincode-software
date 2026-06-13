# src/enrichers.py
"""Enrichment layer: fetch HTML, detect pixels, detect platform, PageSpeed.

ARSITEKTUR:
- fetch_site() dengan multi-strategy fallback (https → http → www)
- detect_pixels() dari HTML markup (regex-based, fast)
- detect_platform() dari HTML/header signals
- fetch_pagespeed() via Google PageSpeed Insights API
- enrich_domain() = orchestrator concurrent semua di atas
- enrich_all() = batch dengan semaphore (rate-limit aware)

PRINSIP:
- Graceful degradation: 1 enricher fail ≠ domain di-discard
- Verbose logging: tiap fail wajib ada reason (DNS/timeout/HTTP code/SSL)
- Concurrent-safe: semaphore + per-API rate limit
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import httpx

from src.config import PAGESPEED_API_KEY
from src.models import EnrichmentResult


# ============================================================
# Constants
# ============================================================
_USER_AGENT = (
    "Mozilla/5.0 (compatible; ApexResearchBot/1.0; "
    "+https://github.com/idincode/idincode-researche)"
)

_DEFAULT_TIMEOUT = 15.0
_PAGESPEED_TIMEOUT = 60.0
_MAX_CONCURRENT_ENRICHMENTS = 8
_MAX_CONCURRENT_PAGESPEED = 4  # Google API ada quota

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ============================================================
# Public API
# ============================================================
async def enrich_all(targets: list[dict]) -> list[EnrichmentResult]:
    """Enrich SEMUA targets concurrent dengan semaphore.

    Args:
        targets: list of dict {domain, location, niche, category}

    Returns:
        list of EnrichmentResult (1:1 dengan targets, fail = reachable=False)
    """
    if not targets:
        return []

    print(f"[pipeline] Enriching {len(targets)} targets concurrently...")
    sem = asyncio.Semaphore(_MAX_CONCURRENT_ENRICHMENTS)

    async def _bounded(target: dict) -> EnrichmentResult:
        async with sem:
            return await enrich_domain(target)

    results = await asyncio.gather(
        *[_bounded(t) for t in targets],
        return_exceptions=False,
    )

    reachable = sum(1 for r in results if r.reachable)
    print(f"[pipeline] ✅ Enrichment done. Reachable: {reachable}/{len(results)}")

    return list(results)


async def enrich_domain(target: dict) -> EnrichmentResult:
    """Enrich single domain. Robust to all failure modes."""
    domain = target["domain"].strip().lower().replace("https://", "").replace("http://", "").rstrip("/")
    location = target.get("location")
    niche = target.get("niche", "default")
    category = target.get("category")

    print(f"[enrich] → {domain}")

    # 1. Fetch HTML (with fallback strategies)
    html, response_ms, final_url, status_code, fail_reason = await _fetch_site_with_fallback(domain)

    # Kalau total fail, return unreachable
    if html is None:
        print(f"[enrich] ❌ {domain} UNREACHABLE: {fail_reason}")
        return EnrichmentResult(
            domain=domain,
            location=location,
            niche=niche,
            category=category,
            reachable=False,
            fail_reason=fail_reason,
            response_ms=response_ms,
            status_code=status_code,
            platform=None,
            has_meta_pixel=False,
            has_tiktok_pixel=False,
            has_ga4=False,
            has_gtm=False,
            has_google_ads=False,
            pagespeed_score=None,
            lcp_ms=None,
        )

    # 2. Detect pixels (sync, fast)
    pixels = _detect_pixels(html)

    # 3. Detect platform (sync, fast)
    platform = _detect_platform(html)

    # 4. PageSpeed (async, slow — only if reachable)
    pagespeed_score, lcp_ms = await _fetch_pagespeed(domain)

    print(
        f"[enrich] ✅ {domain} | platform={platform or 'unknown'} | "
        f"pixels={sum(pixels.values())}/5 | ps={pagespeed_score} | "
        f"lcp={lcp_ms}ms | rt={response_ms}ms"
    )

    return EnrichmentResult(
        domain=domain,
        location=location,
        niche=niche,
        category=category,
        reachable=True,
        fail_reason=None,
        response_ms=response_ms,
        status_code=status_code,
        platform=platform,
        has_meta_pixel=pixels["meta"],
        has_tiktok_pixel=pixels["tiktok"],
        has_ga4=pixels["ga4"],
        has_gtm=pixels["gtm"],
        has_google_ads=pixels["google_ads"],
        pagespeed_score=pagespeed_score,
        lcp_ms=lcp_ms,
        raw_html=html,
    )


# ============================================================
# Fetch with multi-strategy fallback
# ============================================================
async def _fetch_site_with_fallback(
    domain: str,
) -> tuple[Optional[str], Optional[int], Optional[str], Optional[int], Optional[str]]:
    """Try multiple URL variants. Return (html, response_ms, final_url, status, fail_reason).

    Strategy:
    1. https://{domain}
    2. https://www.{domain}
    3. http://{domain}
    4. http://www.{domain}
    """
    # Skip kalau domain udah punya www
    variants = []
    if domain.startswith("www."):
        bare = domain[4:]
        variants = [
            f"https://{domain}",
            f"https://{bare}",
            f"http://{domain}",
            f"http://{bare}",
        ]
    else:
        variants = [
            f"https://{domain}",
            f"https://www.{domain}",
            f"http://{domain}",
            f"http://www.{domain}",
        ]

    last_fail_reason: Optional[str] = None
    last_status: Optional[int] = None
    last_response_ms: Optional[int] = None

    for url in variants:
        html, response_ms, status, fail_reason = await _fetch_once(url)
        if html is not None:
            return html, response_ms, url, status, None

        last_fail_reason = fail_reason
        last_status = status
        last_response_ms = response_ms

    return None, last_response_ms, None, last_status, last_fail_reason


async def _fetch_once(
    url: str,
) -> tuple[Optional[str], Optional[int], Optional[int], Optional[str]]:
    """Single GET request. Return (html, response_ms, status_code, fail_reason)."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers=_HEADERS,
            verify=True,
        ) as client:
            resp = await client.get(url)
            elapsed_ms = int((time.perf_counter() - start) * 1000)

            if resp.status_code == 200:
                # Verify it's actually HTML (not PDF/JSON/etc)
                content_type = resp.headers.get("content-type", "").lower()
                if "html" not in content_type and "text" not in content_type:
                    return None, elapsed_ms, resp.status_code, f"non-html content-type: {content_type}"

                # Limit size (5MB max) untuk mencegah memory bloat
                text = resp.text
                if len(text) > 5_000_000:
                    text = text[:5_000_000]
                return text, elapsed_ms, resp.status_code, None

            # Non-200 (404, 403, 500, etc)
            return None, elapsed_ms, resp.status_code, f"HTTP {resp.status_code}"

    except httpx.ConnectTimeout:
        return None, None, None, "connect_timeout"
    except httpx.ReadTimeout:
        return None, None, None, "read_timeout"
    except httpx.ConnectError as e:
        # DNS fail, connection refused, etc
        msg = str(e)[:100]
        return None, None, None, f"connect_error: {msg}"
    except httpx.RemoteProtocolError as e:
        return None, None, None, f"protocol_error: {str(e)[:80]}"
    except httpx.TooManyRedirects:
        return None, None, None, "too_many_redirects"
    except httpx.UnsupportedProtocol as e:
        return None, None, None, f"unsupported_protocol: {str(e)[:80]}"
    except (httpx.HTTPError, ssl_error_catch()) as e:  # type: ignore
        return None, None, None, f"http_error: {type(e).__name__}: {str(e)[:80]}"
    except Exception as e:  # noqa: BLE001
        return None, None, None, f"unknown: {type(e).__name__}: {str(e)[:80]}"


def ssl_error_catch():
    """Lazy import ssl to add to exception tuple."""
    try:
        import ssl
        return ssl.SSLError
    except ImportError:
        return Exception


# ============================================================
# Pixel detection (HTML markup only — legal & verifiable)
# ============================================================
# Compiled patterns (faster, cleaner)
_META_PIXEL_PATTERNS = [
    re.compile(r"connect\.facebook\.net/[^/]+/fbevents\.js", re.IGNORECASE),
    re.compile(r"fbq\s*\(\s*['\"]init['\"]", re.IGNORECASE),
    re.compile(r"facebook-pixel", re.IGNORECASE),
]

_TIKTOK_PIXEL_PATTERNS = [
    re.compile(r"analytics\.tiktok\.com/i18n/pixel", re.IGNORECASE),
    re.compile(r"ttq\.load\s*\(", re.IGNORECASE),
    re.compile(r"tiktok-pixel", re.IGNORECASE),
]

_GA4_PATTERNS = [
    re.compile(r"www\.googletagmanager\.com/gtag/js\?id=G-[A-Z0-9]+", re.IGNORECASE),
    re.compile(r"gtag\s*\(\s*['\"]config['\"]\s*,\s*['\"]G-", re.IGNORECASE),
]

_GTM_PATTERNS = [
    re.compile(r"www\.googletagmanager\.com/gtm\.js\?id=GTM-", re.IGNORECASE),
    re.compile(r"GTM-[A-Z0-9]{4,}", re.IGNORECASE),
]

_GOOGLE_ADS_PATTERNS = [
    re.compile(r"www\.googletagmanager\.com/gtag/js\?id=AW-", re.IGNORECASE),
    re.compile(r"gtag\s*\(\s*['\"]config['\"]\s*,\s*['\"]AW-", re.IGNORECASE),
    re.compile(r"google_conversion_id", re.IGNORECASE),
]


def _detect_pixels(html: str) -> dict[str, bool]:
    """Detect tracking pixels from HTML markup."""
    return {
        "meta": _any_match(html, _META_PIXEL_PATTERNS),
        "tiktok": _any_match(html, _TIKTOK_PIXEL_PATTERNS),
        "ga4": _any_match(html, _GA4_PATTERNS),
        "gtm": _any_match(html, _GTM_PATTERNS),
        "google_ads": _any_match(html, _GOOGLE_ADS_PATTERNS),
    }


def _any_match(html: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(html) for p in patterns)


# ============================================================
# Platform detection
# ============================================================
_PLATFORM_SIGNALS: list[tuple[str, list[re.Pattern]]] = [
    (
        "shopify",
        [
            re.compile(r"cdn\.shopify\.com", re.IGNORECASE),
            re.compile(r"shopify\.theme", re.IGNORECASE),
            re.compile(r"Shopify\.shop", re.IGNORECASE),
        ],
    ),
    (
        "woocommerce",
        [
            re.compile(r"woocommerce", re.IGNORECASE),
            re.compile(r"wc-blocks", re.IGNORECASE),
        ],
    ),
    (
        "wordpress",
        [
            re.compile(r"wp-content/", re.IGNORECASE),
            re.compile(r"wp-includes/", re.IGNORECASE),
            re.compile(r"wp-json/", re.IGNORECASE),
        ],
    ),
    (
        "wix",
        [
            re.compile(r"static\.wixstatic\.com", re.IGNORECASE),
            re.compile(r"_wixCIDX", re.IGNORECASE),
        ],
    ),
    (
        "squarespace",
        [
            re.compile(r"squarespace\.com", re.IGNORECASE),
            re.compile(r"static1\.squarespace\.com", re.IGNORECASE),
        ],
    ),
    (
        "webflow",
        [
            re.compile(r"webflow\.com", re.IGNORECASE),
            re.compile(r"data-wf-page", re.IGNORECASE),
        ],
    ),
    (
        "bigcommerce",
        [
            re.compile(r"cdn\.bcapp\.dev", re.IGNORECASE),
            re.compile(r"bigcommerce\.com", re.IGNORECASE),
        ],
    ),
    (
        "duda",
        [
            re.compile(r"irp\.cdn-website\.com", re.IGNORECASE),
            re.compile(r"dudamobile", re.IGNORECASE),
        ],
    ),
]


def _detect_platform(html: str) -> Optional[str]:
    """Detect CMS/platform from HTML signals. Return None if unknown."""
    # WooCommerce check WAJIB sebelum WordPress (Woo = subset Wordpress)
    for platform_name, patterns in _PLATFORM_SIGNALS:
        if any(p.search(html) for p in patterns):
            return platform_name
    return None


# ============================================================
# PageSpeed (Google API)
# ============================================================
_PAGESPEED_SEM = asyncio.Semaphore(_MAX_CONCURRENT_PAGESPEED)


async def _fetch_pagespeed(domain: str) -> tuple[Optional[int], Optional[int]]:
    """Fetch PageSpeed mobile score + LCP. Return (score, lcp_ms).

    Graceful fail: kalau API key kosong / API down, return (None, None).
    """
    if not PAGESPEED_API_KEY:
        return None, None

    url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {
        "url": f"https://{domain}",
        "strategy": "mobile",
        "category": "performance",
        "key": PAGESPEED_API_KEY,
    }

    async with _PAGESPEED_SEM:
        try:
            async with httpx.AsyncClient(timeout=_PAGESPEED_TIMEOUT) as client:
                resp = await client.get(url, params=params)

                if resp.status_code != 200:
                    print(f"[pagespeed] {domain}: HTTP {resp.status_code}")
                    return None, None

                data = resp.json()
                lighthouse = data.get("lighthouseResult", {})
                categories = lighthouse.get("categories", {})
                perf = categories.get("performance", {})
                score = perf.get("score")
                score_int = int(score * 100) if isinstance(score, (int, float)) else None

                # LCP dari audits
                audits = lighthouse.get("audits", {})
                lcp_audit = audits.get("largest-contentful-paint", {})
                lcp_ms = lcp_audit.get("numericValue")
                lcp_int = int(lcp_ms) if isinstance(lcp_ms, (int, float)) else None

                return score_int, lcp_int

        except httpx.TimeoutException:
            print(f"[pagespeed] {domain}: timeout")
            return None, None
        except Exception as e:  # noqa: BLE001
            print(f"[pagespeed] {domain}: {type(e).__name__}: {str(e)[:80]}")
            return None, None
