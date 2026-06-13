"""AI Personalized Email Generator.

Generate cold email (subject, body, CTA) per lead/buyer via kie.ai (Claude).

Re-use konfigurasi yang sama dengan analyst.py/buyer_analyst.py supaya
konsisten dengan AI macro yang udah lo set.

Public API:
    generate_emails_for_leads(qualified_leads, *, batch_size, max_retries) -> dict
    generate_emails_for_buyers(buyer_leads, *, batch_size, max_retries)    -> dict

Output dict:
    {
        "<domain>":            # untuk leads pipeline
        OR "<domain>|<email>": # untuk buyers pipeline (1 person)
        {
            "subject": "...",
            "body":    "...",   # plain text, multi-paragraph
            "cta":     "..."
        }
    }

Fallback: kalau IDINCODE_API kosong atau API gagal -> template fallback
yang TETAP usable (gak crash pipeline).
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from src.analyst import _extract_text_from_response
from src.config import (
    IDINCODE_API,
    KIE_AI_BASE_URL,
    KIE_AI_MESSAGES_PATH,
    KIE_AI_MODEL,
    KIE_AI_THINKING,
)


# ============================================================
# Prompts
# ============================================================
def _system_for_leads() -> str:
    return (
        "You are an elite B2B cold email copywriter helping the operator "
        "(Idin Iskandar) generate personalized outreach to CLINIC / "
        "BUSINESS prospects. The clinics are pre-qualified leads — they "
        "have weak pixels, slow sites, or missing tracking, which the "
        "operator can fix (or sell as data to an agency that fixes it).\n\n"
        "For each lead, produce a cold email with:\n"
        "  - subject: <50 chars, pattern-interrupt, NO clickbait, NO emoji.\n"
        "  - body: 3-5 short paragraphs, conversational, plain text. Mention "
        "ONE specific issue (slow LCP, missing pixel, no GA4, etc) from the "
        "data given. Avoid buzzwords like 'leverage' / 'synergy' / 'unlock'.\n"
        "  - cta: 1 sentence soft ask (e.g. 'open to a 15-min look?').\n\n"
        "Rules:\n"
        "1. Output ONLY valid JSON. NO markdown fences.\n"
        '2. Format: {"results": {"<domain>": {"subject": "...", "body": "...", "cta": "..."}}}\n'
        "3. Body uses \\n\\n for paragraph breaks.\n"
        "4. NEVER invent data not present in the input."
    )


def _system_for_buyers() -> str:
    return (
        "You are an elite B2B cold email copywriter. The operator (Idin "
        "Iskandar) sells SCRAPED LEAD DATABASES (high-intent clinics, "
        "GLP-1, hair restoration, cosmetic dentistry, etc) to digital "
        "marketing AGENCIES who use that data for their own outreach.\n\n"
        "Target: a decision maker (CEO/Founder/Owner/Partner) at an "
        "agency. Write a cold email with:\n"
        "  - subject: <50 chars, specific to their niche, pattern-interrupt.\n"
        "  - body: 3-5 short paragraphs. Address by FIRST name. Reference "
        "their probable service line. Make the pitch: ready-to-use scored "
        "lead lists, not raw data. Plain text. No emoji.\n"
        "  - cta: 1 sentence soft ask (e.g. 'want a free 10-lead sample?').\n\n"
        "Rules:\n"
        "1. Output ONLY valid JSON. NO markdown fences.\n"
        '2. Format: {"results": {"<domain>|<email>": {"subject": "...", "body": "...", "cta": "..."}}}\n'
        "3. Body uses \\n\\n for paragraph breaks.\n"
        "4. NEVER invent data not present in the input."
    )


def _user_prompt_leads(leads: list[Any]) -> str:
    lines = [
        "Generate cold email for each lead. JSON only.",
        "",
        "Leads:",
    ]
    for l in leads:
        issues = []
        if getattr(l, "pagespeed_score", None) is not None and l.pagespeed_score < 60:
            issues.append(f"slow mobile speed (pagespeed={l.pagespeed_score})")
        if getattr(l, "lcp_ms", None) is not None and l.lcp_ms > 3500:
            issues.append(f"LCP {l.lcp_ms}ms (>3.5s)")
        if not getattr(l, "meta_pixel_in_html", False):
            issues.append("no Meta pixel in HTML")
        if not getattr(l, "ga4_in_html", False):
            issues.append("no GA4 tracking")
        if not getattr(l, "google_ads_in_html", False):
            issues.append("no Google Ads remarketing")
        issue_str = "; ".join(issues) or "general optimization opportunities"
        lines.append(
            f"- domain={l.domain} | niche={l.niche} | "
            f"location={l.location or 'unknown'} | score={l.score} | "
            f"issues={issue_str}"
        )
    lines.append("")
    lines.append("Remember: JSON only, no markdown.")
    return "\n".join(lines)


def _user_prompt_buyers(rows: list[dict]) -> str:
    """rows: list[{'key','domain','agency_name','niche_keyword','country','first_name','title'}]"""
    lines = [
        "Generate cold email for each agency decision maker. JSON only.",
        "",
        "Persons:",
    ]
    for r in rows:
        lines.append(
            f"- key={r['key']} | first_name={r['first_name']} | "
            f"title={r['title']} | agency={r['agency_name']} | "
            f"domain={r['domain']} | niche={r['niche_keyword']} | "
            f"country={r['country']}"
        )
    lines.append("")
    lines.append("Remember: JSON only, no markdown.")
    return "\n".join(lines)


# ============================================================
# Core call
# ============================================================
async def _call_kie(
    system: str, user: str, *, max_retries: int = 2
) -> dict[str, Any]:
    payload = {
        "model": KIE_AI_MODEL,
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": user}],
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
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                text = _extract_text_from_response(resp.json())
                if not text:
                    raise ValueError("empty AI text")
                return _parse(text)
            if resp.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
                if attempt < max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
            raise RuntimeError(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < max_retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise
    if last:
        raise last
    return {}


def _parse(text: str) -> dict[str, Any]:
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.MULTILINE)
    # extract first {...} block
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in AI response")
    data = json.loads(m.group(0))
    return data.get("results", {}) if isinstance(data, dict) else {}


# ============================================================
# Public: LEADS
# ============================================================
async def generate_emails_for_leads(
    leads: list[Any],
    *,
    batch_size: int = 8,
    max_retries: int = 2,
) -> dict[str, dict[str, str]]:
    """Return {domain: {subject, body, cta}}."""
    out: dict[str, dict[str, str]] = {}
    if not leads:
        return out

    if not IDINCODE_API:
        print("[email-gen] IDINCODE_API kosong, pakai fallback template")
        for l in leads:
            out[l.domain] = _fallback_lead(l)
        return out

    print(f"[email-gen] Generate cold email untuk {len(leads)} leads...")
    for i in range(0, len(leads), batch_size):
        chunk = leads[i:i + batch_size]
        try:
            results = await _call_kie(
                _system_for_leads(),
                _user_prompt_leads(chunk),
                max_retries=max_retries,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[email-gen] WARN batch {i}: {type(e).__name__}: {e}, fallback")
            for l in chunk:
                out[l.domain] = _fallback_lead(l)
            continue
        for l in chunk:
            data = results.get(l.domain)
            if isinstance(data, dict) and data.get("body"):
                out[l.domain] = {
                    "subject": str(data.get("subject", "")).strip()
                    or _fallback_lead(l)["subject"],
                    "body": str(data.get("body", "")).strip(),
                    "cta": str(data.get("cta", "")).strip()
                    or _fallback_lead(l)["cta"],
                }
            else:
                out[l.domain] = _fallback_lead(l)
    return out


# ============================================================
# Public: BUYERS
# ============================================================
async def generate_emails_for_buyers(
    buyer_leads: list[Any],
    *,
    batch_size: int = 8,
    max_retries: int = 2,
) -> dict[str, dict[str, str]]:
    """Return {"<domain>|<email>": {subject, body, cta}}.

    buyer_leads: list[BuyerLead] (from src.buyer_finder)
    """
    out: dict[str, dict[str, str]] = {}
    rows: list[dict] = []
    for l in buyer_leads:
        for p in l.persons:
            if not p.email:
                continue
            first = p.name.split()[0] if p.name else ""
            rows.append({
                "key": f"{l.agency_domain}|{p.email.lower()}",
                "domain": l.agency_domain,
                "agency_name": l.agency_name,
                "niche_keyword": l.niche_keyword,
                "country": l.country,
                "first_name": first,
                "title": p.title,
                "_person": p,
                "_lead": l,
            })
    if not rows:
        return out

    if not IDINCODE_API:
        print("[email-gen] IDINCODE_API kosong, pakai fallback template")
        for r in rows:
            out[r["key"]] = _fallback_buyer(r)
        return out

    print(f"[email-gen] Generate cold email untuk {len(rows)} buyer persons...")
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        try:
            # strip _person/_lead before sending to AI
            prompt_chunk = [
                {k: v for k, v in r.items() if not k.startswith("_")}
                for r in chunk
            ]
            results = await _call_kie(
                _system_for_buyers(),
                _user_prompt_buyers(prompt_chunk),
                max_retries=max_retries,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[email-gen] WARN batch {i}: {type(e).__name__}: {e}, fallback")
            for r in chunk:
                out[r["key"]] = _fallback_buyer(r)
            continue
        for r in chunk:
            data = results.get(r["key"])
            if isinstance(data, dict) and data.get("body"):
                out[r["key"]] = {
                    "subject": str(data.get("subject", "")).strip()
                    or _fallback_buyer(r)["subject"],
                    "body": str(data.get("body", "")).strip(),
                    "cta": str(data.get("cta", "")).strip()
                    or _fallback_buyer(r)["cta"],
                }
            else:
                out[r["key"]] = _fallback_buyer(r)
    return out


# ============================================================
# Fallbacks (template — selalu jalan)
# ============================================================
def _fallback_lead(l: Any) -> dict[str, str]:
    issue = "your site loading speed"
    if getattr(l, "pagespeed_score", None) is not None and l.pagespeed_score < 60:
        issue = f"your mobile PageSpeed score ({l.pagespeed_score}/100)"
    elif not getattr(l, "meta_pixel_in_html", False):
        issue = "the missing Meta pixel on your site"
    elif not getattr(l, "ga4_in_html", False):
        issue = "the missing GA4 tracking on your site"

    subject = f"Quick note about {l.domain}"
    body = (
        f"Hi team,\n\n"
        f"Ran a quick audit on {l.domain} and noticed {issue}. "
        f"In the {l.niche or 'your'} space this usually translates "
        f"directly into lost bookings — the prospect bounces before "
        f"the page even loads.\n\n"
        f"I help operators in your niche fix this kind of thing without "
        f"big agency retainers. Happy to share what I found, no strings.\n\n"
        f"— Idin"
    )
    cta = "Open to a 15-min look?"
    return {"subject": subject, "body": body, "cta": cta}


def _fallback_buyer(r: dict) -> dict[str, str]:
    first = r["first_name"] or "there"
    niche = r["niche_keyword"]
    subject = f"Pre-qualified {niche} leads for {r['agency_name']}"
    body = (
        f"Hi {first},\n\n"
        f"Saw you run {r['agency_name']} in the {niche} space. "
        f"I sell pre-scored clinic lead lists in that exact niche — "
        f"weak pixels, slow sites, missing tracking — basically the "
        f"prospects your team already pitches.\n\n"
        f"No data scraping work on your end. Ready CSV, tiered by "
        f"buying intent.\n\n"
        f"— Idin"
    )
    cta = "Want a free 10-lead sample?"
    return {"subject": subject, "body": body, "cta": cta}
