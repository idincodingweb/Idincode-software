# src/export.py
"""Export qualified leads ke CSV bertingkat (Starter / Pro / Premium Gold)."""
from __future__ import annotations

import csv
from copy import copy
from pathlib import Path

from src.config import OUTPUT_DIR, TIER_CONFIGS
from src.models import QualifiedLead


# ============================================================
# CSV column order (jangan diubah tanpa update analyst & buyer docs)
# ============================================================
_CSV_COLUMNS = [
    "rank",
    "domain",
    "location",
    "niche",
    "category",
    "gold_score",
    "platform",
    "meta_pixel_in_html",
    "ga4_in_html",
    "gtm_in_html",
    "google_ads_in_html",
    "pagespeed_mobile",
    "lcp_ms",
    "response_ms",
    # --- Extras (zero-budget enrichment) ---
    "revenue_tier",
    "revenue_score",
    "emails_found",
    "email_guesses",
    "mx_valid",
    "running_meta_ads",
    "meta_ads_count",
    "competitors",
    # --- AI Analyst output ---
    "gold_reasons",
    "outreach_angle",
]


def export_tiered_csvs(leads: list[QualifiedLead]) -> list[str]:
    """Export ke 4 file: leads_all + 3 tiered.

    Returns: list path file yang berhasil di-export.
    """
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # Handle empty input — tetep bikin file kosong biar artifact upload gak fail
    if not leads:
        print("[export] WARN: No leads to export. Writing empty leads_all.csv for debugging.")
        empty_path = Path(OUTPUT_DIR) / "leads_all.csv"
        _write_csv(empty_path, [])
        return [str(empty_path)]

    # Sort by score descending, kasih rank
    sorted_leads = sorted(leads, key=lambda x: x.score, reverse=True)
    for idx, lead in enumerate(sorted_leads, start=1):
        lead.rank = idx

    output_files: list[str] = []

    # 1. Internal master file (semua leads)
    all_path = Path(OUTPUT_DIR) / "leads_all.csv"
    _write_csv(all_path, sorted_leads)
    print(f"[export] OK leads_all.csv         ({len(sorted_leads)} leads) - INTERNAL")
    output_files.append(str(all_path))

    # 2. Tiered exports
    for tier in TIER_CONFIGS:
        filtered = [l for l in sorted_leads if l.score >= tier["min_score"]]
        filtered = filtered[: tier["limit"]]
        # Re-rank dalam tier (rank 1 = best dalam tier itu)
        ranked = [_with_local_rank(l, idx) for idx, l in enumerate(filtered, 1)]

        tier_path = Path(OUTPUT_DIR) / tier["filename"]
        _write_csv(tier_path, ranked)
        print(
            f"[export] OK {tier['filename']:<24} "
            f"({len(ranked):3d} leads, score >= {tier['min_score']}) - {tier['label']}"
        )
        output_files.append(str(tier_path))

    return output_files


# Alias backward-compatible (biar pipeline.py bisa import dengan nama mana aja)
export_tiered = export_tiered_csvs


def _with_local_rank(lead: QualifiedLead, rank: int) -> QualifiedLead:
    """Bikin shallow copy dengan rank lokal (untuk tiered CSV)."""
    new = copy(lead)
    new.rank = rank
    return new


def _write_csv(path: Path, leads: list[QualifiedLead]) -> None:
    """Write CSV dengan column order fixed."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(_CSV_COLUMNS)
        for lead in leads:
            writer.writerow([
                getattr(lead, "rank", 0),
                lead.domain,
                lead.location or "",
                lead.niche,
                lead.category or "",
                f"{lead.score:.4f}",
                lead.platform or "Unknown",
                _yn(lead.meta_pixel_in_html),
                _yn(lead.ga4_in_html),
                _yn(lead.gtm_in_html),
                _yn(lead.google_ads_in_html),
                lead.pagespeed_score if lead.pagespeed_score is not None else "",
                lead.lcp_ms if lead.lcp_ms is not None else "",
                lead.response_ms if lead.response_ms is not None else "",
                # Extras
                getattr(lead, "revenue_tier", "") or "",
                getattr(lead, "revenue_score", 0) or 0,
                _join(getattr(lead, "emails_found", [])),
                _join(getattr(lead, "email_guesses", [])),
                _mx_label(getattr(lead, "mx_valid", None)),
                _bool_label(getattr(lead, "running_meta_ads", None)),
                getattr(lead, "meta_ads_count", "") if getattr(lead, "meta_ads_count", None) is not None else "",
                _join(getattr(lead, "competitors", [])),
                # AI
                lead.gold_reasons or "",
                lead.outreach_angle or "",
            ])


def _yn(b: bool) -> str:
    return "yes" if b else "no"


def _bool_label(v) -> str:
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return "unknown"


def _mx_label(v) -> str:
    if v is True:
        return "valid"
    if v is False:
        return "invalid"
    return "unknown"


def _join(items) -> str:
    if not items:
        return ""
    return "; ".join(str(x) for x in items)
