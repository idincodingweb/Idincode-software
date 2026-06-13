# run.py
"""Apex Market Intelligence — Lead Qualification Pipeline.

Built by Idin Iskandar.

Usage:
    python run.py                              # default targets.yaml, extras+pdf ON
    python run.py --targets path.yaml          # custom path
    python run.py --no-extras                  # skip emails/revenue/ads/competitors
    python run.py --no-pdf                     # skip PDF audit generation
    python run.py --ads                        # enable Meta Ad Library scrape (slow)
    python run.py --competitors                # enable competitor discovery (slow)
    python run.py --pdf-min-score 0.70         # lower the PDF threshold
    python run.py --pdf-top 50                 # generate more PDFs

CI/CD: dipakai di .github/workflows/research.yml
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from src.config import IDINCODE_API, PAGESPEED_API_KEY
from src.pipeline import run_pipeline


def _print_banner() -> None:
    print("=" * 64)
    print("  APEX MARKET INTELLIGENCE - Lead Qualification Pipeline")
    print("  Built by Idin Iskandar")
    print("=" * 64)


def _print_env_status() -> None:
    print("[ENV CHECK]")
    print(f"  PAGESPEED_API_KEY : {'SET' if PAGESPEED_API_KEY else 'MISSING'}")
    print(f"  IDINCODE_API      : {'SET' if IDINCODE_API else 'MISSING'}")


def _print_summary(summary: dict) -> None:
    print("=" * 64)
    print("  PIPELINE COMPLETE")
    print("=" * 64)
    print(f"  Total targets       : {summary['total_targets']}")
    print(f"  Reachable           : {summary['reachable']}")
    print(f"  Qualified leads     : {summary['qualified']}")
    print(f"  Output CSVs         : {len(summary['output_files'])}")
    print(f"  PDF audits          : {len(summary.get('pdf_files', []))}")
    print(f"  Duration            : {summary['duration_seconds']}s")
    print("  Generated CSVs:")
    for f in summary["output_files"]:
        print(f"    - {f}")
    if summary.get("pdf_files"):
        print("  Generated PDFs:")
        for f in summary["pdf_files"][:10]:
            print(f"    - {f}")
        if len(summary["pdf_files"]) > 10:
            print(f"    ... +{len(summary['pdf_files']) - 10} more")


async def _main(args: argparse.Namespace) -> int:
    _print_banner()
    _print_env_status()

    try:
        summary = await run_pipeline(
            args.targets,
            enable_extras=not args.no_extras,
            enable_ads=args.ads,
            enable_competitors=args.competitors,
            enable_pdf=not args.no_pdf,
            pdf_min_score=args.pdf_min_score,
            pdf_top_n=args.pdf_top,
            enable_dedup=not args.no_dedup,
            include_seen=args.include_seen,
            reset_dedup=args.reset_dedup,
        )
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[ABORTED] User interrupted.", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    _print_summary(summary)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apex Market Intelligence pipeline"
    )
    parser.add_argument("--targets", default="targets.yaml",
                        help="Path to targets.yaml")
    parser.add_argument("--no-extras", action="store_true",
                        help="Skip extras enrichment (emails/revenue/ads/competitors)")
    parser.add_argument("--no-pdf", action="store_true",
                        help="Skip PDF audit generation")
    parser.add_argument("--ads", action="store_true",
                        help="Enable Meta Ad Library scrape (slow, flaky)")
    parser.add_argument("--competitors", action="store_true",
                        help="Enable competitor discovery via DDG (slow)")
    parser.add_argument("--pdf-min-score", type=float, default=0.85,
                        help="Minimum score for PDF audit generation (default 0.85)")
    parser.add_argument("--pdf-top", type=int, default=25,
                        help="Max number of PDF audits to generate (default 25)")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Nonaktifkan SQLite dedup (default: dedup ON)")
    parser.add_argument("--include-seen", action="store_true",
                        help="Ikutkan target domain yang sudah pernah muncul")
    parser.add_argument("--reset-dedup", action="store_true",
                        help="Wipe dedup DB sebelum mulai")
    args = parser.parse_args()

    exit_code = asyncio.run(_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
