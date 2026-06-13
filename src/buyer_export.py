# src/buyer_export.py
"""Export BuyerLead -> CSV (1 row per person).

Output: output/buyers/buyers_<timestamp>.csv + latest symlink-friendly copy.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path

from src.buyer_finder import BuyerLead


_FIELDS = (
    "rank",
    "agency_domain",
    "agency_name",
    "niche_keyword",
    "country",
    "person_name",
    "person_title",
    "email",
    "email_confidence",
    "email_source",
    "mx_valid",
    "outreach_angle",
    "why_buy",
)


def export_buyers_csv(
    leads: list[BuyerLead],
    output_dir: str = "output/buyers",
) -> list[str]:
    """Flatten BuyerLead -> 1 row per person. Return list of file paths."""
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stamped = Path(output_dir) / f"buyers_{ts}.csv"
    latest = Path(output_dir) / "buyers_latest.csv"

    rows: list[dict] = []
    rank = 0
    # Stable sort: agency-level by max person confidence DESC, person count DESC
    leads_sorted = sorted(
        leads,
        key=lambda l: (
            max((p.email_confidence for p in l.persons), default=0.0),
            len(l.persons),
        ),
        reverse=True,
    )

    for l in leads_sorted:
        for p in l.persons:
            rank += 1
            rows.append({
                "rank": rank,
                "agency_domain": l.agency_domain,
                "agency_name": l.agency_name,
                "niche_keyword": l.niche_keyword,
                "country": l.country,
                "person_name": p.name,
                "person_title": p.title,
                "email": p.email,
                "email_confidence": p.email_confidence,
                "email_source": p.email_source,
                "mx_valid": "" if l.mx_valid is None else ("yes" if l.mx_valid else "no"),
                "outreach_angle": l.outreach_angle,
                "why_buy": l.why_buy,
            })

    for path in (stamped, latest):
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS)
            w.writeheader()
            w.writerows(rows)

    print(f"[buyer-export] Wrote {len(rows)} rows -> {stamped}")
    print(f"[buyer-export] Latest: {latest}")
    return [str(stamped), str(latest)]
