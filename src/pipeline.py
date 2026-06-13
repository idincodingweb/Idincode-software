# src/pipeline.py
"""Main orchestrator: load → enrich → extras → qualify → analyst → export → pdf.

Return summary dict yang dipakai run.py.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from src.analyst import enrich_with_ai_analyst
from src.dedup_db import DedupDB
from src.enrichers import enrich_all
from src.export import export_tiered_csvs
from src.extras import enrich_extras_batch
from src.loader import load_targets
from src.pdf_audit import generate_pdf_audits
from src.qualifier import qualify_lead


async def run_pipeline(
    targets_path: str = "targets.yaml",
    *,
    enable_extras: bool = True,
    enable_ads: bool = False,
    enable_competitors: bool = False,
    enable_pdf: bool = True,
    pdf_min_score: float = 0.85,
    pdf_top_n: int = 25,
    enable_dedup: bool = True,
    include_seen: bool = False,
    reset_dedup: bool = False,
) -> dict[str, Any]:
    """Run full pipeline. Return summary dict untuk reporting di run.py."""
    start_ts = time.perf_counter()

    print("=" * 60)
    print("🎯 Apex Market Intelligence | By Idincode")
    print("=" * 60)

    # 0. Dedup DB
    db: DedupDB | None = None
    if enable_dedup:
        db = DedupDB()
        if reset_dedup:
            import os
            try:
                os.remove(db.path)
                print(f"[dedup] wiped {db.path}")
            except OSError:
                pass
            db = DedupDB()
        s = db.stats()
        print(f"[dedup] enabled (db={s['db_path']}, leads_seen={s['leads_seen']}, "
              f"include_seen={include_seen})")
    else:
        print("[dedup] DISABLED")

    # 1. Load targets
    targets = load_targets(targets_path)
    total_targets = len(targets)
    print(f"[pipeline] Loaded {total_targets} targets from {targets_path}")

    # 1b. Dedup filter
    if db and not include_seen:
        before = len(targets)
        targets = [t for t in targets if not db.is_lead_seen(getattr(t, "domain", ""))]
        skipped = before - len(targets)
        if skipped:
            print(f"[dedup] skip {skipped} target yg udah pernah ke-process "
                  f"(--include-seen kalau mau ulang)")
        if not targets:
            print("[dedup] semua target udah pernah ke-process. "
                  "Tambah target baru atau pakai --include-seen.")
            duration = round(time.perf_counter() - start_ts, 2)
            return {
                "total_targets": total_targets, "reachable": 0, "qualified": 0,
                "output_files": [], "pdf_files": [],
                "duration_seconds": duration,
            }

    # 2. Normalize targets
    normalized_targets = []
    for t in targets:
        if hasattr(t, "to_dict"):
            normalized_targets.append(t.to_dict())
        elif isinstance(t, dict):
            normalized_targets.append(t)
        else:
            normalized_targets.append({
                "domain": getattr(t, "domain", ""),
                "location": getattr(t, "location", None),
                "niche": getattr(t, "niche", "default"),
                "category": getattr(t, "category", None),
            })

    # 3. Enrich (concurrent)
    enrichments = await enrich_all(normalized_targets)

    # 4. Filter unreachable
    reachable = [e for e in enrichments if getattr(e, "reachable", True)]
    unreachable = [e for e in enrichments if not getattr(e, "reachable", True)]

    if unreachable:
        print(f"\n[pipeline] WARN: {len(unreachable)} domains unreachable:")
        reasons: dict[str, int] = {}
        for e in unreachable:
            reason = getattr(e, "fail_reason", None) or "unknown"
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"   - {reason}: {count}")

    # 5. Handle 0 reachable
    if not reachable:
        print("\n[pipeline] FATAL: 0 reachable domains.")
        output_files: list[str] = []
        try:
            output_files = export_tiered_csvs([])
        except Exception as e:  # noqa: BLE001
            print(f"[pipeline] export empty failed: {e}")

        duration = round(time.perf_counter() - start_ts, 2)
        return {
            "total_targets": total_targets,
            "reachable": 0,
            "qualified": 0,
            "output_files": output_files,
            "pdf_files": [],
            "duration_seconds": duration,
        }

    # 6. Extras enrichment (zero-budget: emails, revenue, ads, competitors)
    if enable_extras:
        base_htmls = {
            getattr(e, "domain", ""): getattr(e, "raw_html", "") or ""
            for e in reachable
        }
        extras_results = await enrich_extras_batch(
            reachable,
            base_htmls=base_htmls,
            enable_emails=True,
            enable_revenue=True,
            enable_ads=enable_ads,
            enable_competitors=enable_competitors,
        )
        # Merge back into EnrichmentResult so qualifier picks them up
        for e, x in zip(reachable, extras_results):
            for k, v in x.items():
                setattr(e, k, v)

    # 7. Qualify (scoring)
    print(f"\n[pipeline] Scoring {len(reachable)} reachable leads...")
    qualified = [qualify_lead(e) for e in reachable]

    # 8. AI Analyst (with fallback)
    qualified = await enrich_with_ai_analyst(qualified)

    # 9. Sort by score
    qualified.sort(key=lambda x: x.score, reverse=True)

    # 10. Export tiered CSVs
    output_files = export_tiered_csvs(qualified)

    # 11. PDF audits (premium gold only by default)
    pdf_files: list[str] = []
    if enable_pdf:
        pdf_files = generate_pdf_audits(
            qualified,
            output_dir="output/pdf",
            only_top=pdf_top_n,
            min_score=pdf_min_score,
        )

    # 12. Persist dedup — mark domain yang berhasil ke-process (reachable)
    if db:
        for e in reachable:
            d = getattr(e, "domain", "")
            if d:
                db.mark_lead(d)
        s = db.stats()
        print(f"[dedup] persisted. total leads_seen={s['leads_seen']}")

    duration = round(time.perf_counter() - start_ts, 2)

    print("\n" + "=" * 60)
    print("✅ Pipeline complete!")
    print("=" * 60)

    return {
        "total_targets": total_targets,
        "reachable": len(reachable),
        "qualified": len(qualified),
        "output_files": output_files,
        "pdf_files": pdf_files,
        "duration_seconds": duration,
    }


def main() -> None:
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
