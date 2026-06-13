"""Export AgencyBuyerLead + RedditBuyerLead -> CSV.

Dua file output:
  output/agency_buyers/agency_buyers_<ts>.csv     (websites)
  output/agency_buyers/reddit_buyers_<ts>.csv     (reddit)
  output/agency_buyers/agency_buyers_latest.csv
  output/agency_buyers/reddit_buyers_latest.csv
"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path

from src.agency_buyer_finder import AgencyBuyerLead
from src.reddit_scraper import RedditBuyerLead


_WEB_FIELDS = (
    "rank",
    "source",
    "website",
    "agency_name",
    "niche_keyword",
    "country",
    "ceo_name",
    "ceo_title",
    "ceo_source",
    "email",
    "phone",
    "mx_valid",
    "extra_emails",
    "extra_phones",
    "notes",
)

_REDDIT_FIELDS = (
    "rank",
    "subreddit",
    "author",
    "post_title",
    "permalink",
    "post_url",
    "website",
    "email",
    "matched_indicators",
    "score",
    "snippet",
)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def export_agency_buyers_csv(
    leads: list[AgencyBuyerLead],
    output_dir: str = "output/agency_buyers",
) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    ts = _ts()
    stamped = Path(output_dir) / f"agency_buyers_{ts}.csv"
    latest = Path(output_dir) / "agency_buyers_latest.csv"

    # Rank: CEO+email > CEO+phone > email-only > phone-only
    def _score(l: AgencyBuyerLead) -> int:
        s = 0
        if l.ceo_name:
            s += 10
        if l.email:
            s += 5
        if l.phone:
            s += 2
        if l.mx_valid:
            s += 1
        return s

    leads_sorted = sorted(leads, key=_score, reverse=True)
    rows = []
    for i, l in enumerate(leads_sorted, start=1):
        rows.append({
            "rank": i,
            "source": l.source,
            "website": l.website,
            "agency_name": l.agency_name,
            "niche_keyword": l.niche_keyword,
            "country": l.country,
            "ceo_name": l.ceo_name,
            "ceo_title": l.ceo_title,
            "ceo_source": l.ceo_source,
            "email": l.email,
            "phone": l.phone,
            "mx_valid": "" if l.mx_valid is None else ("yes" if l.mx_valid else "no"),
            "extra_emails": "; ".join(l.extra_emails),
            "extra_phones": "; ".join(l.extra_phones),
            "notes": l.notes,
        })

    for path in (stamped, latest):
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_WEB_FIELDS)
            w.writeheader()
            w.writerows(rows)
    print(f"[agency-export] Wrote {len(rows)} agency rows -> {stamped}")
    return [str(stamped), str(latest)]


def export_reddit_buyers_csv(
    leads: list[RedditBuyerLead],
    output_dir: str = "output/agency_buyers",
) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    ts = _ts()
    stamped = Path(output_dir) / f"reddit_buyers_{ts}.csv"
    latest = Path(output_dir) / "reddit_buyers_latest.csv"

    leads_sorted = sorted(leads, key=lambda l: l.score, reverse=True)
    rows = []
    for i, l in enumerate(leads_sorted, start=1):
        rows.append({
            "rank": i,
            "subreddit": l.subreddit,
            "author": l.author,
            "post_title": l.post_title,
            "permalink": l.permalink,
            "post_url": l.post_url,
            "website": l.website,
            "email": l.email,
            "matched_indicators": l.matched_indicators,
            "score": l.score,
            "snippet": l.snippet,
        })

    for path in (stamped, latest):
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_REDDIT_FIELDS)
            w.writeheader()
            w.writerows(rows)
    print(f"[agency-export] Wrote {len(rows)} reddit rows -> {stamped}")
    return [str(stamped), str(latest)]
