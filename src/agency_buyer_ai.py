"""AI fallback untuk extract CEO/Founder dari /about page agency.

Dipanggil hanya kalau heuristic di agency_buyer_finder.py gagal nemu
decision maker. Pakai kie.ai Claude (sama dengan analyst.py).

Graceful fallback: kalau IDINCODE_API kosong / gagal -> return None.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from src.analyst import _extract_text_from_response
from src.config import (
    IDINCODE_API,
    KIE_AI_BASE_URL,
    KIE_AI_MESSAGES_PATH,
    KIE_AI_MODEL,
    KIE_AI_THINKING,
)


_MAX_PROMPT_CHARS = 6000


def _condense(htmls: list[str]) -> str:
    """Strip tags & condense for prompt."""
    text_parts: list[str] = []
    for h in htmls:
        if not h:
            continue
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            text_parts.append(t)
    joined = "\n\n---\n\n".join(text_parts)
    if len(joined) > _MAX_PROMPT_CHARS:
        joined = joined[:_MAX_PROMPT_CHARS]
    return joined


_SYSTEM = (
    "You are an extraction agent. Given raw text from an agency website "
    "(home + /about + /team pages), return the CEO / Founder / Owner name.\n\n"
    "Rules:\n"
    "1. Output ONLY valid JSON, no markdown fences.\n"
    "2. If unsure, return {\"name\": \"\", \"title\": \"\"}.\n"
    "3. Format: {\"name\": \"Full Name\", \"title\": \"CEO|Founder|Owner|...\"}.\n"
    "4. Prefer CEO > Founder > Co-Founder > Owner > Managing Director > President.\n"
    "5. Do NOT invent. If text doesn't name a person, return empty strings."
)


async def ai_extract_ceo(
    domain: str,
    homepage_html: str,
    all_htmls: list[str],
    *,
    timeout: float = 60.0,
) -> Optional[dict]:
    if not IDINCODE_API:
        return None

    text = _condense(all_htmls or [homepage_html])
    if not text:
        return None

    user_prompt = (
        f"Agency domain: {domain}\n\n"
        f"Website text (condensed):\n{text}\n\n"
        "Return JSON: {\"name\": \"...\", \"title\": \"...\"}"
    )

    payload = {
        "model": KIE_AI_MODEL,
        "max_tokens": 256,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": user_prompt}],
        "thinkingFlag": KIE_AI_THINKING,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {IDINCODE_API}",
        "Content-Type": "application/json",
    }
    url = f"{KIE_AI_BASE_URL.rstrip('/')}{KIE_AI_MESSAGES_PATH}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            return None
        text_out = _extract_text_from_response(resp.json())
    except Exception:  # noqa: BLE001
        return None

    if not text_out:
        return None

    cleaned = re.sub(r"^```(?:json)?\s*", "", text_out.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*?\}", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None
    name = str(data.get("name", "")).strip()
    title = str(data.get("title", "")).strip()
    if not name or len(name.split()) < 2 or len(name.split()) > 5:
        return None
    return {"name": name, "title": title or "Founder"}
