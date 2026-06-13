# src/analyst.py
"""Claude AI Analyst Layer via kie.ai (Anthropic-native format).

Generate gold_reasons + outreach_angle untuk setiap qualified lead.
Graceful fallback ke deterministic template kalau API fail.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from src.config import (
    IDINCODE_API,
    KIE_AI_BASE_URL,
    KIE_AI_MESSAGES_PATH,
    KIE_AI_MODEL,
    KIE_AI_THINKING,
)
from src.models import QualifiedLead


# ============================================================
# Public API
# ============================================================
async def enrich_with_ai_analyst(
    leads: list[QualifiedLead],
    *,
    max_retries: int = 2,
) -> list[QualifiedLead]:
    """Enrich SEMUA leads dengan AI-generated gold_reasons + outreach_angle."""
    if not leads:
        return leads

    if not IDINCODE_API:
        print("[analyst] IDINCODE_API kosong, pakai fallback template")
        return _apply_fallback_to_all(leads)

    print(f"[analyst] Generating AI reasoning untuk {len(leads)} leads via kie.ai...")

    try:
        ai_results = await _call_claude_batch(leads, max_retries=max_retries)
    except Exception as e:  # noqa: BLE001
        print(
            f"[analyst] WARN: Claude call failed "
            f"({type(e).__name__}: {e}), pakai fallback"
        )
        return _apply_fallback_to_all(leads)

    enriched: list[QualifiedLead] = []
    matched = 0
    for lead in leads:
        ai_data = ai_results.get(lead.domain)
        if ai_data and isinstance(ai_data, dict):
            lead.gold_reasons = ai_data.get("gold_reasons") or _fallback_reasons(lead)
            lead.outreach_angle = ai_data.get("outreach_angle") or _fallback_outreach(lead)
            if ai_data.get("gold_reasons"):
                matched += 1
        else:
            lead.gold_reasons = _fallback_reasons(lead)
            lead.outreach_angle = _fallback_outreach(lead)
        enriched.append(lead)

    print(f"[analyst] OK: AI reasoning generated untuk {matched}/{len(enriched)} leads")
    return enriched


# ============================================================
# kie.ai API call (Anthropic-native format)
# ============================================================
async def _call_claude_batch(
    leads: list[QualifiedLead],
    *,
    max_retries: int,
) -> dict[str, dict[str, str]]:
    """Call kie.ai endpoint /claude/v1/messages (Anthropic-native)."""
    system_prompt = _build_system_prompt(leads)
    user_prompt = _build_user_prompt(leads)

    payload = {
        "model": KIE_AI_MODEL,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_prompt},
        ],
        "thinkingFlag": KIE_AI_THINKING,
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {IDINCODE_API}",
        "Content-Type": "application/json",
    }

    url = f"{KIE_AI_BASE_URL.rstrip('/')}{KIE_AI_MESSAGES_PATH}"

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                text = _extract_text_from_response(data)
                if not text:
                    raise ValueError(f"Empty text from response: {str(data)[:300]}")

                parsed = _parse_json_response(text)
                if parsed:
                    return parsed
                raise ValueError(f"Failed to parse JSON. Raw text: {text[:300]}")

            # Retry-able errors
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
                if attempt < max_retries:
                    wait = 2 ** attempt
                    print(f"[analyst] HTTP {resp.status_code}, retry in {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                raise last_error

            # Non-retry error (400, 401, 403, 404)
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        except httpx.TimeoutException as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"[analyst] Timeout, retry in {wait}s...")
                await asyncio.sleep(wait)
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError("Unknown error in _call_claude_batch")


# ============================================================
# Dynamic Prompt Builder (per-niche context)
# ============================================================
_NICHE_CONTEXT: dict[str, dict[str, str]] = {
    "medical_high_ticket": {
        "industry_label": "high-ticket medical & aesthetic clinics",
        "typical_ticket": "$3,000-$30,000 per case",
        "pain_point": "consult-to-book conversion, attribution clarity, ROAS visibility",
    },
    "default": {
        "industry_label": "high-ticket service businesses",
        "typical_ticket": "$1,000-$10,000 per customer",
        "pain_point": "lead-to-close conversion, marketing attribution",
    },
    "cosmetic_dentistry": {
        "industry_label": "cosmetic & implant dentistry practices",
        "typical_ticket": "$3,000-$30,000 per case",
        "pain_point": "consult-to-book conversion, attribution clarity",
    },
    "premium_orthodontics": {
        "industry_label": "premium orthodontics & clear aligner clinics",
        "typical_ticket": "$3,000-$8,000 per patient",
        "pain_point": "adult market competition, patient LTV tracking",
    },
    "weight_loss_glp1": {
        "industry_label": "weight loss & GLP-1 telehealth clinics",
        "typical_ticket": "$200-$500/month subscription",
        "pain_point": "telehealth conversion gaps, retention funnels",
    },
    "premium_hair_restoration": {
        "industry_label": "premium hair restoration & transplant clinics",
        "typical_ticket": "$8,000-$15,000 per procedure",
        "pain_point": "high CAC, emotional + surgical decision support",
    },
}


def _detect_primary_niche(leads: list[QualifiedLead]) -> str:
    """Cari niche paling umum di batch."""
    counts: dict[str, int] = {}
    for lead in leads:
        counts[lead.niche] = counts.get(lead.niche, 0) + 1
    if not counts:
        return "medical_high_ticket"
    return max(counts.items(), key=lambda x: x[1])[0]


def _build_system_prompt(leads: list[QualifiedLead]) -> str:
    """Build dynamic system prompt berdasarkan niche dominant di batch."""
    primary_niche = _detect_primary_niche(leads)
    ctx = _NICHE_CONTEXT.get(primary_niche, _NICHE_CONTEXT["medical_high_ticket"])

    return (
        f"You are an expert B2B sales analyst specializing in digital marketing "
        f"for {ctx['industry_label']} (typical deal size: {ctx['typical_ticket']}, "
        f"common pain point: {ctx['pain_point']}).\n\n"
        f"Your job: analyze website tracking infrastructure & performance data to "
        f"identify SALES OPPORTUNITIES that marketing agencies can pitch.\n\n"
        f"Your output is used by agencies to cold-pitch services to these businesses. "
        f"Be SPECIFIC, ACTIONABLE, and slightly URGENT.\n\n"
        "Rules:\n"
        "1. Output ONLY valid JSON. No markdown fences, no preamble, no explanation.\n"
        "2. For each domain, generate:\n"
        "   - gold_reasons (1-2 sentences): WHY this is a hot lead. Reference "
        "specific gaps with concrete impact (revenue, attribution clarity, ROAS).\n"
        "   - outreach_angle (1 sentence): A cold email subject line OR opening "
        "hook an agency can use immediately. Make it pattern-interrupting.\n"
        "3. Tone: confident, data-driven, no fluff, no buzzwords.\n"
        "4. If a clinic/business already has mature infra, honestly say "
        "'limited opportunity'.\n"
        "5. Response format MUST be exactly:\n"
        "{\n"
        '  "results": {\n'
        '    "domain1.com": {"gold_reasons": "...", "outreach_angle": "..."},\n'
        '    "domain2.com": {"gold_reasons": "...", "outreach_angle": "..."}\n'
        "  }\n"
        "}"
    )


def _build_user_prompt(leads: list[QualifiedLead]) -> str:
    lines = [
        "Analyze these businesses. For each, generate gold_reasons & "
        "outreach_angle. Return JSON only.\n",
        "Data per business:",
    ]

    for lead in leads:
        pixels = []
        if lead.meta_pixel_in_html:
            pixels.append("Meta")
        if lead.ga4_in_html:
            pixels.append("GA4")
        if lead.gtm_in_html:
            pixels.append("GTM")
        if lead.google_ads_in_html:
            pixels.append("GoogleAds")
        pixels_str = ",".join(pixels) if pixels else "NONE"

        ps_str = f"{lead.pagespeed_score}" if lead.pagespeed_score is not None else "N/A"
        lcp_str = f"{lead.lcp_ms}ms" if lead.lcp_ms is not None else "N/A"
        rt_str = f"{lead.response_ms}ms" if lead.response_ms else "N/A"

        lines.append(
            f"- domain={lead.domain} | niche={lead.niche} | "
            f"location={lead.location or 'N/A'} | "
            f"platform={lead.platform or 'Unknown'} | "
            f"pixels_in_html=[{pixels_str}] | "
            f"pagespeed_mobile={ps_str} | lcp={lcp_str} | response_time={rt_str} | "
            f"gold_score={lead.score:.2f}"
        )

    lines.append(
        "\nRemember: output ONLY the JSON object, no markdown fences, no explanation."
    )
    return "\n".join(lines)


# ============================================================
# Response parsing (Anthropic-native format)
# ============================================================
def _extract_text_from_response(data: dict[str, Any]) -> str:
    """Extract text dari Anthropic-native response format."""
    content = data.get("content")
    if not isinstance(content, list) or not content:
        return ""

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text:
                return text

    first = content[0]
    if isinstance(first, dict):
        text = first.get("text", "")
        if isinstance(text, str):
            return text

    return ""


def _parse_json_response(text: str) -> dict[str, dict[str, str]]:
    """Parse JSON dari response. Strip markdown fences kalau ada (defensive)."""
    if not text:
        return {}

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    results = data.get("results", {})
    if not isinstance(results, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for domain, payload in results.items():
        if not isinstance(payload, dict):
            continue
        normalized[domain] = {
            "gold_reasons": str(payload.get("gold_reasons", "")).strip(),
            "outreach_angle": str(payload.get("outreach_angle", "")).strip(),
        }
    return normalized


# ============================================================
# Fallback (deterministic, no API needed)
# ============================================================
def _apply_fallback_to_all(leads: list[QualifiedLead]) -> list[QualifiedLead]:
    for lead in leads:
        lead.gold_reasons = _fallback_reasons(lead)
        lead.outreach_angle = _fallback_outreach(lead)
    return leads


def _fallback_reasons(lead: QualifiedLead) -> str:
    reasons = []

    missing = []
    if not lead.meta_pixel_in_html:
        missing.append("Meta Pixel")
    if not lead.ga4_in_html:
        missing.append("GA4")
    if not lead.gtm_in_html:
        missing.append("GTM")
    if not lead.google_ads_in_html:
        missing.append("Google Ads tag")

    if len(missing) >= 3:
        reasons.append(
            f"Missing {len(missing)} key tracking pixels "
            f"({', '.join(missing[:3])}) - major retargeting & attribution gap."
        )
    elif missing:
        reasons.append(
            f"Missing {', '.join(missing)} - incomplete attribution stack."
        )

    if lead.pagespeed_score is not None:
        if lead.pagespeed_score < 50:
            reasons.append(
                f"Mobile PageSpeed {lead.pagespeed_score}/100 - high bounce risk "
                f"on mobile traffic."
            )
        elif lead.pagespeed_score < 70:
            reasons.append(
                f"Mobile PageSpeed {lead.pagespeed_score}/100 - room for "
                f"conversion uplift."
            )

    if lead.response_ms and lead.response_ms > 3000:
        reasons.append(
            f"Server response {lead.response_ms}ms - signals hosting/tech debt."
        )

    if lead.platform and lead.platform.lower() in ("wordpress", "woocommerce"):
        reasons.append(
            "WordPress stack - easy to onboard for tracking & speed fixes."
        )

    if not reasons:
        return (
            "Limited opportunity - infrastructure looks healthy. "
            "Consider for retention plays only."
        )

    return " ".join(reasons)


def _fallback_outreach(lead: QualifiedLead) -> str:
    domain_label = lead.domain.replace("www.", "").split(".")[0].title()

    missing_pixels = []
    if not lead.meta_pixel_in_html:
        missing_pixels.append("Meta Pixel")
    if not lead.ga4_in_html:
        missing_pixels.append("GA4")
    if not lead.google_ads_in_html:
        missing_pixels.append("Google Ads tag")

    if len(missing_pixels) >= 2:
        return (
            f"Subject: Found {len(missing_pixels)} tracking gaps on "
            f"{domain_label}'s site - worth a 15-min chat?"
        )

    if lead.pagespeed_score is not None and lead.pagespeed_score < 50:
        return (
            f"Subject: {domain_label}'s mobile site loads at "
            f"{lead.pagespeed_score}/100 - here's what it's costing you"
        )

    if lead.response_ms and lead.response_ms > 3000:
        return (
            f"Subject: Quick note about {domain_label}'s site speed "
            f"(I think you're losing leads)"
        )

    return (
        f"Subject: 3 quick wins I spotted for {domain_label} "
        f"(takes 5 min to read)"
                )
