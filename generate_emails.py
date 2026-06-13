"""Generate AI-personalized cold emails from latest leads/buyers CSV.

Built by Idin Iskandar.

Run setelah `run.py` (leads) atau `find_buyer.py` (buyers).
Membaca CSV terbaru, generate email (subject + body + CTA) lewat AI macro
(kie.ai / Claude) yang sama dengan analyst layer, lalu tulis hasilnya ke:

    output/emails/leads/<domain>.md       (1 file per lead)
    output/emails/buyers/<domain>__<email>.md
    output/emails/emails_index.csv        (summary semua subject)

Usage:
    python generate_emails.py                       # auto: leads + buyers
    python generate_emails.py --source leads        # leads only
    python generate_emails.py --source buyers       # buyers only
    python generate_emails.py --limit 20            # cap jumlah email
    python generate_emails.py --leads-csv path.csv  # override input file
    python generate_emails.py --buyers-csv path.csv
    python generate_emails.py --out output/emails   # output dir

Tanpa IDINCODE_API → tetap jalan pakai template fallback (warning printed).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.config import IDINCODE_API
from src.email_generator import (
    generate_emails_for_buyers,
    generate_emails_for_leads,
)


# ============================================================
# Lightweight rehydrators: read CSV -> objects yang email_generator
# bisa konsumsi (cuma butuh attribute access).
# ============================================================
@dataclass
class _LeadRow:
    domain: str
    niche: str = ""
    location: str = ""
    score: float = 0.0
    pagespeed_score: Optional[int] = None
    lcp_ms: Optional[int] = None
    meta_pixel_in_html: bool = False
    ga4_in_html: bool = False
    google_ads_in_html: bool = False


@dataclass
class _BuyerPerson:
    name: str
    title: str
    email: str


@dataclass
class _BuyerLead:
    agency_domain: str
    agency_name: str
    niche_keyword: str
    country: str
    persons: list[_BuyerPerson]


# ============================================================
# CSV readers
# ============================================================
def _safe_int(v: str) -> Optional[int]:
    try:
        return int(float(v)) if v not in ("", None) else None
    except (TypeError, ValueError):
        return None


def _safe_float(v: str) -> float:
    try:
        return float(v) if v not in ("", None) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _bool_csv(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def _find_latest(dir_path: str, prefix: str) -> Optional[str]:
    p = Path(dir_path)
    if not p.exists():
        return None
    candidates = sorted(p.glob(f"{prefix}*.csv"), reverse=True)
    return str(candidates[0]) if candidates else None


def read_leads_csv(path: str, *, limit: Optional[int]) -> list[_LeadRow]:
    rows: list[_LeadRow] = []
    with open(path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(_LeadRow(
                domain=r.get("domain", "").strip(),
                niche=r.get("niche", "") or "",
                location=r.get("location", "") or "",
                score=_safe_float(r.get("gold_score", "0")),
                pagespeed_score=_safe_int(r.get("pagespeed_mobile", "")),
                lcp_ms=_safe_int(r.get("lcp_ms", "")),
                meta_pixel_in_html=_bool_csv(r.get("meta_pixel_in_html", "")),
                ga4_in_html=_bool_csv(r.get("ga4_in_html", "")),
                google_ads_in_html=_bool_csv(r.get("google_ads_in_html", "")),
            ))
            if limit and len(rows) >= limit:
                break
    return [r for r in rows if r.domain]


def read_buyers_csv(path: str, *, limit: Optional[int]) -> list[_BuyerLead]:
    by_agency: dict[str, _BuyerLead] = {}
    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            domain = r.get("agency_domain", "").strip()
            email = r.get("email", "").strip()
            if not domain or not email:
                continue
            if domain not in by_agency:
                by_agency[domain] = _BuyerLead(
                    agency_domain=domain,
                    agency_name=r.get("agency_name", domain),
                    niche_keyword=r.get("niche_keyword", ""),
                    country=r.get("country", "US"),
                    persons=[],
                )
            by_agency[domain].persons.append(_BuyerPerson(
                name=r.get("person_name", "").strip(),
                title=r.get("person_title", "").strip(),
                email=email,
            ))
            count += 1
            if limit and count >= limit:
                break
    return list(by_agency.values())


# ============================================================
# Writers
# ============================================================
def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s).strip("_") or "x"


def _write_md(path: str, *, subject: str, body: str, cta: str, meta: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append(f"subject: {subject}")
    lines.append("---")
    lines.append("")
    lines.append(f"**Subject:** {subject}")
    lines.append("")
    lines.append(body)
    if cta:
        lines.append("")
        lines.append(f"_{cta}_")
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _append_index(index_path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    is_new = not os.path.exists(index_path)
    fields = ["source", "domain", "email", "subject", "cta", "file"]
    with open(index_path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if is_new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


# ============================================================
# Drivers
# ============================================================
async def _do_leads(csv_path: str, out_dir: str, limit: Optional[int]) -> int:
    print(f"[gen-emails] Leads source: {csv_path}")
    rows = read_leads_csv(csv_path, limit=limit)
    if not rows:
        print("[gen-emails] WARN: no rows in leads CSV.")
        return 0
    out = await generate_emails_for_leads(rows)
    idx = os.path.join(out_dir, "emails_index.csv")
    written = 0
    for r in rows:
        e = out.get(r.domain)
        if not e:
            continue
        path = os.path.join(out_dir, "leads", f"{_slug(r.domain)}.md")
        _write_md(
            path,
            subject=e["subject"], body=e["body"], cta=e["cta"],
            meta={
                "source": "leads",
                "domain": r.domain,
                "niche": r.niche,
                "score": r.score,
            },
        )
        _append_index(idx, {
            "source": "leads", "domain": r.domain, "email": "",
            "subject": e["subject"], "cta": e["cta"], "file": path,
        })
        written += 1
    print(f"[gen-emails] Wrote {written} lead emails -> {out_dir}/leads/")
    return written


async def _do_buyers(csv_path: str, out_dir: str, limit: Optional[int]) -> int:
    print(f"[gen-emails] Buyers source: {csv_path}")
    leads = read_buyers_csv(csv_path, limit=limit)
    if not leads:
        print("[gen-emails] WARN: no rows in buyers CSV.")
        return 0
    out = await generate_emails_for_buyers(leads)
    idx = os.path.join(out_dir, "emails_index.csv")
    written = 0
    for l in leads:
        for p in l.persons:
            key = f"{l.agency_domain}|{p.email.lower()}"
            e = out.get(key)
            if not e:
                continue
            fname = f"{_slug(l.agency_domain)}__{_slug(p.email)}.md"
            path = os.path.join(out_dir, "buyers", fname)
            _write_md(
                path,
                subject=e["subject"], body=e["body"], cta=e["cta"],
                meta={
                    "source": "buyers",
                    "agency_domain": l.agency_domain,
                    "agency_name": l.agency_name,
                    "niche": l.niche_keyword,
                    "person": p.name,
                    "title": p.title,
                    "email": p.email,
                },
            )
            _append_index(idx, {
                "source": "buyers", "domain": l.agency_domain,
                "email": p.email, "subject": e["subject"],
                "cta": e["cta"], "file": path,
            })
            written += 1
    print(f"[gen-emails] Wrote {written} buyer emails -> {out_dir}/buyers/")
    return written


# ============================================================
# CLI
# ============================================================
def _banner() -> None:
    print("=" * 64)
    print("  APEX EMAIL GENERATOR — Personalized Cold Email (AI)")
    print("  Built by Idin Iskandar")
    print("=" * 64)


async def _main(args: argparse.Namespace) -> int:
    _banner()
    print(f"[ENV] IDINCODE_API: {'SET' if IDINCODE_API else 'MISSING (fallback template)'}")
    out_dir = args.out

    total = 0
    if args.source in ("leads", "both"):
        path = args.leads_csv or _find_latest("output", "leads_premium_gold") \
            or _find_latest("output", "leads_pro") \
            or _find_latest("output", "leads_starter")
        if not path:
            print("[gen-emails] No leads CSV found (jalanin `python run.py` dulu).")
        else:
            total += await _do_leads(path, out_dir, args.limit)

    if args.source in ("buyers", "both"):
        path = args.buyers_csv or _find_latest("output/buyers", "buyers_latest") \
            or _find_latest("output/buyers", "buyers_")
        if not path:
            print("[gen-emails] No buyers CSV found (jalanin `python find_buyer.py` dulu).")
        else:
            total += await _do_buyers(path, out_dir, args.limit)

    print("=" * 64)
    print(f"  DONE — {total} emails generated")
    print(f"  Output dir : {out_dir}/")
    print(f"  Index CSV  : {out_dir}/emails_index.csv")
    print("=" * 64)
    return 0 if total > 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate AI-personalized cold emails from leads/buyers CSV"
    )
    parser.add_argument("--source", choices=("leads", "buyers", "both"),
                        default="both", help="Which pipeline output to use")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap jumlah email yang di-generate")
    parser.add_argument("--leads-csv", default=None,
                        help="Path manual ke leads CSV (override auto-detect)")
    parser.add_argument("--buyers-csv", default=None,
                        help="Path manual ke buyers CSV (override auto-detect)")
    parser.add_argument("--out", default="output/emails",
                        help="Output directory")
    args = parser.parse_args()

    try:
        code = asyncio.run(_main(args))
    except KeyboardInterrupt:
        print("\n[ABORTED] User interrupted.", file=sys.stderr)
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
