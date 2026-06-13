# src/buyer_analyst.py
"""AI layer untuk Buyer pipeline.

Tugas Claude:
1. Generate `outreach_angle` — kalimat pembuka cold email yang pattern-interrupt.
2. Generate `why_buy` — 1-2 kalimat kenapa agency INI cocok beli data leads lo.

Re-use kie.ai (Anthropic-native) config dari analyst.py.
Graceful fallback ke template kalau API kosong / gagal.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from src.analyst import _extract_text_from_response
from src.buyer_finder import BuyerLead
from src.config import (
    IDINCODE_API,
    KIE_AI_BASE_URL,
    KIE_AI_MESSAGES_PATH,
    KIE_AI_MODEL,
    KIE_AI_THINKING,
)


# ============================================================
# Public API
# ============================================================
async def enrich_buyers_with_ai(
    leads: list[BuyerLead],
    *,
    max_retries: int = 2,
    batch_size: int = 12,
) -> list[BuyerLead]:
    """Enrich SEMUA buyer leads dengan AI outreach_angle + why_buy."""
    if not leads:
        return leads

    if not IDINCODE_API:
        print("[buyer-ai] IDINCODE_API kosong, pakai fallback template")
        for l in leads:
            _apply_fallback(l)
        return leads

    print(f"[buyer-ai] AI rerank+angle untuk {len(leads)} agencies via kie.ai...")

    matched = 0
    # Batch supaya prompt gak meledak
    for i in range(0, len(leads), batch_size):
        chunk = leads[i:i + batch_size]
        try:
            ai_map = await _call_claude_batch(chunk, max_retries=max_retries)
        except Exception as e:  # noqa: BLE001
            print(f"[buyer-ai] WARN batch {i}: {type(e).__name__}: {e}, fallback")
            for l in chunk:
                _apply_fallback(l)
            continue

        for l in chunk:
            data = ai_map.get(l.agency_domain)
            if data and isinstance(data, dict):
                angle = str(data.get("outreach_angle", "")).strip()
                why = str(data.get("why_buy", "")).strip()
                l.outreach_angle = angle or _fallback_angle(l)
                l.why_buy = why or _fallback_why(l)
                if angle:
                    matched += 1
            else:
                _apply_fallback(l)

    print(f"[buyer-ai] OK: AI angle untuk {matched}/{len(leads)} agencies")
    return leads


# ============================================================
# Internals
# ============================================================
def _build_system_prompt() -> str:
    return (
        "You are a B2B sales strategist helping a market-intelligence vendor "
        "(operator: Idin Iskandar) sell SCRAPED LEAD DATABASES to digital "
        "marketing agencies in the dental / healthcare / medical niche.\n\n"
        "The vendor scrapes high-intent clinics (cosmetic dentistry, GLP-1 "
        "telehealth, hair restoration, etc), scores them, and packages them as "
        "tiered CSV lists. Agencies BUY this data to power their own cold "
        "outreach to those clinics.\n\n"
        "Your job: for each agency, write:\n"
        "  - outreach_angle: 1 line cold-email subject OR opening hook that "
        "stops the scroll. Pattern-interrupt, specific to the agency niche.\n"
        "  - why_buy: 1-2 sentences explaining why THIS agency is a fit buyer "
        "for pre-qualified clinic lead data (reference their probable niche, "
        "geo focus, or service line).\n\n"
        "Rules:\n"
        "1. Output ONLY valid JSON. No markdown fences.\n"
        "2. Tone: confident, no fluff, no buzzwords like 'leverage' or 'synergy'.\n"
        "3. Format:\n"
        "{\n"
        '  "results": {\n'
        '    "agency1.com": {"outreach_angle": "...", "why_buy": "..."},\n'
        '    "agency2.com": {"outreach_angle": "...", "why_buy": "..."}\n'
        "  }\n"
        "}"
    )


def _build_user_prompt(leads: list[BuyerLead]) -> str:
    lines = [
        "For each agency below, generate outreach_angle + why_buy. "
        "Return JSON only.\n",
        "Agencies:",
    ]
    for l in leads:
        persons_str = ", ".join(
            f"{p.name} ({p.title})" for p in l.persons[:3]
        ) or "no decision maker named"
        lines.append(
            f"- domain={l.agency_domain} | name={l.agency_name} | "
            f"niche_keyword={l.niche_keyword} | country={l.country} | "
            f"team={persons_str}"
        )
    lines.append("\nRemember: JSON only, no markdown.")
    return "\n".join(lines)


async def _call_claude_batch(
    leads: list[BuyerLead],
    *,
    max_retries: int,
) -> dict[str, dict[str, str]]:
    payload = {
        "model": KIE_AI_MODEL,
        "max_tokens": 3072,
        "system": _build_system_prompt(),
        "messages": [
            {"role": "user", "content": _build_user_prompt(leads)},
        ],
        "thinkingFlag": KIE_AI_THINKING,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {IDINCODE_API}",
        "Content-Type": "application/json",
    }
    url = f"{KIE_AI_BASE_URL.rstrip('/')}{KIE_AI_MESSAGES_PATH}"

    last: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                text = _extract_text_from_response(resp.json())
                if not text:
                    raise ValueError("empty AI text")
                return _parse(text)
            if resp.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise last
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        except httpx.TimeoutException as e:
            last = e
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
    if last:
        raise last
    raise RuntimeError("unknown")


def _parse(text: str) -> dict[str, dict[str, str]]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    results = data.get("results", {})
    if not isinstance(results, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for k, v in results.items():
        if not isinstance(v, dict):
            continue
        out[k] = {
            "outreach_angle": str(v.get("outreach_angle", "")).strip(),
            "why_buy": str(v.get("why_buy", "")).strip(),
        }
    return out


# ============================================================
# Fallback templates
# ============================================================
def _apply_fallback(l: BuyerLead) -> None:
    l.outreach_angle = _fallback_angle(l)
    l.why_buy = _fallback_why(l)


def _fallback_angle(l: BuyerLead) -> str:
    person = l.persons[0].name.split()[0] if l.persons else "team"
    return (
        f"Hey {person} — built a pre-scored list of {l.niche_keyword.split()[0]} "
        f"clinics in {l.country} that are already running paid ads but missing "
        f"GA4/Meta pixel. Want a sample row?"
    )


def _fallback_why(l: BuyerLead) -> str:
    return (
        f"{l.agency_name} positions around {l.niche_keyword}, so pre-qualified "
        f"clinic leads with tracking-gap signals match their service line and "
        f"shorten their prospecting cycle."
    )
