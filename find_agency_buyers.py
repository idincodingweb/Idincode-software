"""Find Agency Buyers — pipeline ke-4.

Cari OWNER agency kecil / FREELANCER yang berpotensi BELI data leads.
Sumber: (a) website scraping via DDG, (b) Reddit public JSON API.

Built by Idin Iskandar.

Usage:
    python find_agency_buyers.py                      # default config
    python find_agency_buyers.py --config x.yaml
    python find_agency_buyers.py --no-reddit
    python find_agency_buyers.py --no-web
    python find_agency_buyers.py --no-ai              # skip Claude fallback
    python find_agency_buyers.py --no-dedup
    python find_agency_buyers.py --reset-dedup
    python find_agency_buyers.py --include-seen
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys

from src.agency_buyer_export import (
    export_agency_buyers_csv,
    export_reddit_buyers_csv,
)
from src.agency_buyer_finder import (
    AgencyBuyerLead,
    find_agency_buyers_for_niche,
)
from src.agency_buyers_loader import load_agency_buyers
from src.config import IDINCODE_API
from src.dedup_db import DedupDB
from src.reddit_scraper import RedditBuyerLead, hunt_reddit_buyers


def _banner() -> None:
    print("=" * 64)
    print("  APEX AGENCY BUYER HUNTER — Small Agency / Freelancer Discovery")
    print("  Built by Idin Iskandar")
    print("=" * 64)


async def _main(args: argparse.Namespace) -> int:
    _banner()
    try:
        cfg = load_agency_buyers(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"[ERROR] agency_buyers.yaml invalid: {e}", file=sys.stderr)
        return 1

    # Dedup
    db: DedupDB | None = None
    skip_domains: set[str] = set()
    if not args.no_dedup:
        db = DedupDB()
        if args.reset_dedup:
            import os
            try:
                os.remove(db.path)
                print(f"[dedup] wiped {db.path}")
            except OSError:
                pass
            db = DedupDB()
        if not args.include_seen:
            # Reuse buyers_seen table — domain-level dedup
            with sqlite3.connect(db.path) as c:
                skip_domains = {
                    r[0] for r in c.execute(
                        "SELECT DISTINCT domain FROM buyers_seen"
                    ).fetchall()
                }
        print(f"[dedup] enabled (skip_domains={len(skip_domains)}, "
              f"include_seen={args.include_seen})")
    else:
        print("[dedup] DISABLED (--no-dedup)")

    print(f"[ENV] IDINCODE_API: {'SET' if IDINCODE_API else 'MISSING'}")
    use_ai = bool(IDINCODE_API) and (not args.no_ai)
    print(f"[CFG] niches={len(cfg.niches)} | reddit_queries={len(cfg.reddit_queries)} "
          f"| ai_fallback={use_ai}")

    web_leads: list[AgencyBuyerLead] = []
    reddit_leads: list[RedditBuyerLead] = []

    # 1. Web scraping pipeline
    if not args.no_web:
        for niche in cfg.niches:
            leads = await find_agency_buyers_for_niche(
                niche.keyword,
                country=niche.country,
                max_agencies=cfg.max_agencies_per_niche,
                max_concurrent=cfg.max_concurrent,
                use_ai_fallback=use_ai,
                skip_domains=skip_domains or None,
            )
            web_leads.extend(leads)
        # in-run dedup by website domain
        uniq: dict[str, AgencyBuyerLead] = {}
        for l in web_leads:
            uniq.setdefault(l.website.lower(), l)
        web_leads = list(uniq.values())
    else:
        print("[agency-buyer] --no-web set, skip website scraping")

    # 2. Reddit
    if cfg.enable_reddit and not args.no_reddit and cfg.reddit_queries:
        reddit_leads = await hunt_reddit_buyers(
            [(q.subreddit, q.query) for q in cfg.reddit_queries],
            limit_per_query=cfg.reddit_post_limit_per_query,
        )
    elif args.no_reddit:
        print("[reddit] --no-reddit set, skip")

    # Export
    files: list[str] = []
    if web_leads:
        files += export_agency_buyers_csv(web_leads)
    else:
        print("[agency-buyer] 0 web leads")
        export_agency_buyers_csv([])

    if reddit_leads:
        files += export_reddit_buyers_csv(reddit_leads)
    else:
        if cfg.enable_reddit and not args.no_reddit:
            print("[reddit] 0 leads matched")
            export_reddit_buyers_csv([])

    # Persist dedup (web leads only — reddit is post-level, ranges over time)
    if db and web_leads:
        marked = 0
        for l in web_leads:
            key_email = l.email or f"_no_email_{l.website}"
            db.mark_buyer(l.website, key_email)
            marked += 1
        print(f"[dedup] persisted {marked} agency domains")

    print("=" * 64)
    print("  AGENCY BUYER HUNTER COMPLETE")
    print("=" * 64)
    print(f"  Web agency leads : {len(web_leads)}")
    print(f"  Reddit leads     : {len(reddit_leads)}")
    print(f"  Files:")
    for f in files:
        print(f"    - {f}")

    if not web_leads and not reddit_leads:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find small agency owners & freelancers (buyers of leads data)"
    )
    parser.add_argument("--config", default="agency_buyers.yaml")
    parser.add_argument("--no-ai", action="store_true",
                        help="Skip AI fallback untuk CEO extraction")
    parser.add_argument("--no-web", action="store_true",
                        help="Skip website scraping pipeline")
    parser.add_argument("--no-reddit", action="store_true",
                        help="Skip Reddit hunter")
    parser.add_argument("--no-dedup", action="store_true")
    parser.add_argument("--include-seen", action="store_true")
    parser.add_argument("--reset-dedup", action="store_true")
    args = parser.parse_args()

    try:
        code = asyncio.run(_main(args))
    except KeyboardInterrupt:
        print("\n[ABORTED] User interrupted.", file=sys.stderr)
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
