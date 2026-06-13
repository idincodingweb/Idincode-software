# src/qualifier.py
"""Inverted scoring: makin banyak gap = makin tinggi score = makin gede peluang jual."""
from __future__ import annotations

from typing import Optional

from src.models import EnrichmentResult, QualifiedLead


# ============================================================
# Niche Weights (sum = 1.0 per niche)
# ============================================================
NICHE_CONFIG: dict[str, dict[str, float]] = {
    # DEFAULT fallback — WAJIB ada biar .get(niche, NICHE_CONFIG["default"]) gak KeyError
    "default": {
        "pixels": 0.40,
        "pagespeed": 0.30,
        "lcp": 0.15,
        "platform": 0.15,
    },
    "cosmetic_dentistry": {
        "pixels": 0.45,
        "pagespeed": 0.30,
        "lcp": 0.15,
        "platform": 0.10,
    },
    "premium_orthodontics": {
        "pixels": 0.40,
        "pagespeed": 0.35,
        "lcp": 0.15,
        "platform": 0.10,
    },
    "weight_loss_glp1": {
        "pixels": 0.35,
        "pagespeed": 0.35,
        "lcp": 0.15,
        "platform": 0.15,
    },
    "premium_hair_restoration": {
        "pixels": 0.42,
        "pagespeed": 0.30,
        "lcp": 0.15,
        "platform": 0.13,
    },
}


# ============================================================
# Response time penalty threshold (ms)
# ============================================================
_RESPONSE_PENALTY_THRESHOLD_MS = 2000
_RESPONSE_PENALTY_FACTOR = 0.15  # 15% penalty


def qualify_lead(enrichment: EnrichmentResult) -> QualifiedLead:
    """Konversi EnrichmentResult → QualifiedLead dengan score."""
    weights = NICHE_CONFIG.get(enrichment.niche, NICHE_CONFIG["default"])

    pixel_score = _score_pixels(enrichment)
    pagespeed_score = _score_pagespeed(enrichment.pagespeed_score)
    lcp_score = _score_lcp(enrichment.lcp_ms)
    platform_score = _score_platform(enrichment.platform)

    composite = (
        pixel_score * weights["pixels"]
        + pagespeed_score * weights["pagespeed"]
        + lcp_score * weights["lcp"]
        + platform_score * weights["platform"]
    )

    # Response time penalty
    if (
        enrichment.response_ms is not None
        and enrichment.response_ms > _RESPONSE_PENALTY_THRESHOLD_MS
    ):
        composite *= 1 - _RESPONSE_PENALTY_FACTOR

    composite = max(0.0, min(1.0, composite))

    return QualifiedLead(
        domain=enrichment.domain,
        location=enrichment.location,
        niche=enrichment.niche,
        category=enrichment.category,
        score=round(composite, 4),
        platform=enrichment.platform,
        meta_pixel_in_html=enrichment.has_meta_pixel,
        tiktok_pixel_in_html=enrichment.has_tiktok_pixel,
        ga4_in_html=enrichment.has_ga4,
        gtm_in_html=enrichment.has_gtm,
        google_ads_in_html=enrichment.has_google_ads,
        pagespeed_score=enrichment.pagespeed_score,
        lcp_ms=enrichment.lcp_ms,
        response_ms=enrichment.response_ms,
        # Carry extras through (filled by src/extras.py in pipeline)
        emails_found=list(getattr(enrichment, "emails_found", []) or []),
        email_guesses=list(getattr(enrichment, "email_guesses", []) or []),
        mx_valid=getattr(enrichment, "mx_valid", None),
        revenue_tier=getattr(enrichment, "revenue_tier", "unknown"),
        revenue_score=getattr(enrichment, "revenue_score", 0),
        running_meta_ads=getattr(enrichment, "running_meta_ads", None),
        meta_ads_count=getattr(enrichment, "meta_ads_count", None),
        competitors=list(getattr(enrichment, "competitors", []) or []),
    )


# ============================================================
# Inverted scoring functions (higher = more opportunity)
# ============================================================
def _score_pixels(e: EnrichmentResult) -> float:
    """0 pixel = 1.0, 4 pixel = 0.10."""
    core_pixels = [
        e.has_meta_pixel,
        e.has_ga4,
        e.has_gtm,
        e.has_google_ads,
    ]
    present = sum(core_pixels)

    if present == 0:
        return 1.00
    if present == 1:
        return 0.85
    if present == 2:
        return 0.60
    if present == 3:
        return 0.30
    return 0.10


def _score_pagespeed(score: Optional[int]) -> float:
    """Inverted: 0-29 = 1.0, 85-100 = 0.10."""
    if score is None:
        return 0.50
    if score < 30:
        return 1.00
    if score < 50:
        return 0.85
    if score < 70:
        return 0.60
    if score < 85:
        return 0.35
    return 0.10


def _score_lcp(lcp_ms: Optional[int]) -> float:
    """Inverted: > 6000ms = 1.0, < 2500ms = 0.10."""
    if lcp_ms is None:
        return 0.50
    if lcp_ms > 6000:
        return 1.00
    if lcp_ms > 4000:
        return 0.80
    if lcp_ms > 2500:
        return 0.50
    return 0.10


def _score_platform(platform: Optional[str]) -> float:
    """WordPress/WooCommerce paling mudah onboard = score tinggi."""
    if not platform:
        return 0.50
    p = platform.lower()
    if p in ("wordpress", "woocommerce"):
        return 1.00
    if p in ("shopify", "bigcommerce"):
        return 0.80
    if p in ("wix", "squarespace", "webflow"):
        return 0.60
    return 0.40
