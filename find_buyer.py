"""Find Buyer — cari decision maker (CEO/Founder/Owner/Partner) di agency
yang berpotensi BELI data leads hasil run.py.

Built by Idin Iskandar.

Usage:
    python find_buyer.py                    # default buyers.yaml + dedup ON
    python find_buyer.py --config path.yaml
    python find_buyer.py --no-ai            # skip AI analyst layer
    python find_buyer.py --no-dedup         # nonaktifkan SQLite dedup
    python find_buyer.py --include-seen     # ikutsertakan buyer yg pernah muncul
    python find_buyer.py --reset-dedup      # wipe dedup DB sebelum jalan
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from src.buyer_analyst import enrich_buyers_with_ai
from src.buyer_export import export_buyers_csv
from src.buyer_finder import BuyerLead, find_buyers_for_niche
from src.buyers_loader import load_buyers
from src.config import IDINCODE_API
from src.dedup_db import DedupDB


def _banner() -> None:
    print("=" * 64)
    print("  APEX BUYER FINDER — Agency Decision Maker Discovery")
    print("  Built by Idin Iskandar")
    print("=" * 64)


async def _main(args: argparse.Namespace) -> int:
    _banner()
    try:
        cfg = load_buyers(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"[ERROR] buyers.yaml invalid: {e}", file=sys.stderr)
        return 1

    # Dedup DB
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
            # Pre-load: agency domain yang SEMUA personnya udah pernah
            # ke-deliver -> skip fetch. Simpler heuristic: skip kalau
            # domain pernah muncul di buyers_seen sama sekali.
            import sqlite3
            with sqlite3.connect(db.path) as c:
                skip_domains = {
                    r[0] for r in c.execute(
                        "SELECT DISTINCT domain FROM buyers_seen"
                    ).fetchall()
                }
        print(f"[dedup] enabled (db={db.path}, skip_domains={len(skip_domains)}, "
              f"include_seen={args.include_seen})")
    else:
        print("[dedup] DISABLED (--no-dedup)")

    print(f"[ENV] IDINCODE_API: {'SET' if IDINCODE_API else 'MISSING'}")
    print(f"[CFG] niches={len(cfg.niches)} | max_agencies/niche={cfg.max_agencies_per_niche} "
          f"| max_persons/agency={cfg.max_persons_per_agency}")

    all_leads: list[BuyerLead] = []
    for niche in cfg.niches:
        leads = await find_buyers_for_niche(
            niche.keyword,
            country=niche.country,
            max_agencies=cfg.max_agencies_per_niche,
            max_persons=cfg.max_persons_per_agency,
            max_concurrent=cfg.max_concurrent,
            skip_domains=skip_domains or None,
        )
        all_leads.extend(leads)

    if not all_leads:
        print("\n[FATAL] 0 agency dengan decision maker valid ketemu. "
              "Cek buyers.yaml, atau coba `--include-seen` kalau dedup aktif.")
        export_buyers_csv([])
        return 1

    # Dedup person-level: skip (domain,email) yg sudah ke-deliver
    fresh: list[BuyerLead] = []
    person_skipped = 0
    seen_inrun: set[tuple[str, str]] = set()
    for l in all_leads:
        keep_persons = []
        for p in l.persons:
            key = (l.agency_domain.lower(), p.email.lower())
            if key in seen_inrun:
                continue
            seen_inrun.add(key)
            if db and not args.include_seen and db.is_buyer_seen(*key):
                person_skipped += 1
                continue
            keep_persons.append(p)
        if keep_persons:
            l.persons = keep_persons
            fresh.append(l)
    if db and person_skipped:
        print(f"[dedup] skip {person_skipped} person yang udah pernah muncul")

    if not fresh:
        print("\n[INFO] Semua agency hasilnya udah pernah ke-deliver. Cek dedup "
              "(--include-seen / --reset-dedup) kalau mau ulang.")
        export_buyers_csv([])
        return 1

    print(f"\n[buyer] Total unique agencies (fresh): {len(fresh)} | "
          f"persons: {sum(len(l.persons) for l in fresh)}")

    # AI enrichment
    if not args.no_ai:
        fresh = await enrich_buyers_with_ai(fresh)
    else:
        print("[buyer] --no-ai set, pakai fallback template")
        from src.buyer_analyst import _apply_fallback
        for l in fresh:
            _apply_fallback(l)

    files = export_buyers_csv(fresh)

    # Mark sebagai seen (kalau dedup aktif)
    if db:
        for l in fresh:
            for p in l.persons:
                db.mark_buyer(l.agency_domain, p.email)
        s = db.stats()
        print(f"[dedup] persisted. total buyers_seen={s['buyers_seen']}")

    print("=" * 64)
    print("  BUYER FINDER COMPLETE")
    print("=" * 64)
    print(f"  Agencies        : {len(fresh)}")
    print(f"  Decision makers : {sum(len(l.persons) for l in fresh)}")
    print(f"  Output CSVs     :")
    for f in files:
        print(f"    - {f}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find agency decision makers (buyers of leads data)"
    )
    parser.add_argument("--config", default="buyers.yaml",
                        help="Path ke buyers.yaml")
    parser.add_argument("--no-ai", action="store_true",
                        help="Skip Claude AI layer (pakai fallback template)")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Nonaktifkan SQLite dedup")
    parser.add_argument("--include-seen", action="store_true",
                        help="Ikutkan buyer yang sudah pernah muncul di run sebelumnya")
    parser.add_argument("--reset-dedup", action="store_true",
                        help="Wipe dedup DB sebelum mulai")
    args = parser.parse_args()

    try:
        code = asyncio.run(_main(args))
    except KeyboardInterrupt:
        print("\n[ABORTED] User interrupted.", file=sys.stderr)
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
