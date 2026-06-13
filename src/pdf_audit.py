# src/pdf_audit.py
"""Generate per-lead PDF audit report (premium gold tier).

Pakai reportlab (pure-Python, no system deps — beda dgn weasyprint yg butuh
cairo/pango/gobject di OS level). Cocok buat GitHub Actions.

Usage (from pipeline / CLI):
    from src.pdf_audit import generate_pdf_audits
    paths = generate_pdf_audits(qualified_leads, output_dir="output/pdf")
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
    )
    _HAS_REPORTLAB = True
except ImportError:
    _HAS_REPORTLAB = False


# ============================================================
# Public API
# ============================================================
def generate_pdf_audits(
    leads: list,
    output_dir: str = "output/pdf",
    *,
    only_top: Optional[int] = 25,
    min_score: float = 0.85,
) -> list[str]:
    """Generate 1 PDF audit per lead (premium gold only by default).

    Args:
        leads: list of QualifiedLead (sudah ter-sort by score)
        output_dir: target folder
        only_top: cuma generate N teratas (None = semua)
        min_score: filter minimum score

    Return: list path PDF yang berhasil dibuat.
    """
    if not _HAS_REPORTLAB:
        print("[pdf_audit] SKIP: reportlab not installed (pip install reportlab)")
        return []

    if not leads:
        print("[pdf_audit] SKIP: 0 leads")
        return []

    filtered = [l for l in leads if getattr(l, "score", 0) >= min_score]
    if only_top:
        filtered = filtered[:only_top]

    if not filtered:
        print(f"[pdf_audit] SKIP: 0 leads above min_score={min_score}")
        return []

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    paths: list[str] = []

    for lead in filtered:
        try:
            path = _generate_one(lead, output_dir)
            paths.append(path)
        except Exception as e:  # noqa: BLE001
            domain = getattr(lead, "domain", "unknown")
            print(f"[pdf_audit] FAIL {domain}: {type(e).__name__}: {e}")

    print(f"[pdf_audit] OK generated {len(paths)} PDF reports → {output_dir}/")
    return paths


# ============================================================
# Internal: single PDF
# ============================================================
def _generate_one(lead, output_dir: str) -> str:
    """Build 1 PDF audit. Return path."""
    domain = getattr(lead, "domain", "unknown")
    safe_name = _sanitize_filename(domain)
    out_path = str(Path(output_dir) / f"audit_{safe_name}.pdf")

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Marketing Audit — {domain}",
        author="Idincode Apex Market Intelligence",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "h1custom",
        parent=styles["Heading1"],
        fontSize=20,
        textColor=colors.HexColor("#1a1a1a"),
        spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "h2custom",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#0f4c81"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "bodycustom",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
    )
    small = ParagraphStyle(
        "small",
        parent=styles["BodyText"],
        fontSize=8,
        textColor=colors.grey,
    )

    story = []

    # === Header ===
    story.append(Paragraph("Marketing Infrastructure Audit", h1))
    story.append(Paragraph(f"<b>{_safe(domain)}</b>", body))
    story.append(Paragraph(
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d')} "
        f"by Idincode Apex Market Intelligence",
        small,
    ))
    story.append(Spacer(1, 8))

    # === Executive Summary ===
    score = getattr(lead, "score", 0.0)
    location = getattr(lead, "location", "") or "—"
    niche = getattr(lead, "niche", "") or "—"
    story.append(Paragraph("Executive Summary", h2))
    summary_data = [
        ["Opportunity Score", f"{score:.4f} / 1.00"],
        ["Location", _safe(str(location))],
        ["Niche", _safe(niche.replace("_", " ").title())],
        ["Platform", _safe(getattr(lead, "platform", None) or "Unknown")],
        ["Revenue Tier", _safe(getattr(lead, "revenue_tier", "unknown"))],
    ]
    story.append(_build_table(summary_data))

    # === Technical Audit ===
    story.append(Paragraph("Technical Audit", h2))
    ps = getattr(lead, "pagespeed_score", None)
    lcp = getattr(lead, "lcp_ms", None)
    rt = getattr(lead, "response_ms", None)
    tech_data = [
        ["PageSpeed (mobile)", _fmt_score(ps)],
        ["LCP (Largest Contentful Paint)", f"{lcp} ms" if lcp else "N/A"],
        ["Server Response Time", f"{rt} ms" if rt else "N/A"],
    ]
    story.append(_build_table(tech_data))

    # === Marketing Stack Gaps ===
    story.append(Paragraph("Marketing Stack Gaps", h2))
    stack_data = [
        ["Meta Pixel", _yes_no(getattr(lead, "meta_pixel_in_html", False))],
        ["TikTok Pixel", _yes_no(getattr(lead, "tiktok_pixel_in_html", False))],
        ["Google Analytics 4", _yes_no(getattr(lead, "ga4_in_html", False))],
        ["Google Tag Manager", _yes_no(getattr(lead, "gtm_in_html", False))],
        ["Google Ads Conversion", _yes_no(getattr(lead, "google_ads_in_html", False))],
    ]
    story.append(_build_table(stack_data))

    # === Advertising Activity ===
    ads_running = getattr(lead, "running_meta_ads", None)
    if ads_running is not None:
        story.append(Paragraph("Advertising Activity", h2))
        ad_data = [
            ["Active Meta Ads", "Yes" if ads_running else "No"],
            ["Approx. Ad Count", str(getattr(lead, "meta_ads_count", "") or "Unknown")],
        ]
        story.append(_build_table(ad_data))

    # === Contact Intelligence ===
    emails_found = getattr(lead, "emails_found", []) or []
    email_guesses = getattr(lead, "email_guesses", []) or []
    mx_valid = getattr(lead, "mx_valid", None)
    if emails_found or email_guesses:
        story.append(Paragraph("Contact Intelligence", h2))
        if emails_found:
            story.append(Paragraph(
                f"<b>Discovered emails ({len(emails_found)}):</b> "
                f"{_safe(', '.join(emails_found[:6]))}",
                body,
            ))
        if email_guesses:
            story.append(Paragraph(
                f"<b>Pattern guesses:</b> "
                f"{_safe(', '.join(email_guesses[:5]))}",
                body,
            ))
        mx_label = (
            "Verified" if mx_valid is True
            else "No MX record" if mx_valid is False
            else "Unknown"
        )
        story.append(Paragraph(f"<b>MX deliverability:</b> {mx_label}", body))

    # === Competitive Landscape ===
    competitors = getattr(lead, "competitors", []) or []
    if competitors:
        story.append(Paragraph("Competitive Landscape", h2))
        story.append(Paragraph(
            "<b>Top competitors detected:</b> " + _safe(", ".join(competitors[:6])),
            body,
        ))

    # === AI Reasons & Outreach ===
    reasons = getattr(lead, "gold_reasons", "") or ""
    outreach = getattr(lead, "outreach_angle", "") or ""
    if reasons or outreach:
        story.append(Paragraph("Recommendations", h2))
        if reasons:
            story.append(Paragraph(f"<b>Why this is a gold lead:</b><br/>{_safe(reasons)}", body))
            story.append(Spacer(1, 4))
        if outreach:
            story.append(Paragraph(f"<b>Suggested outreach angle:</b><br/>{_safe(outreach)}", body))

    # === Footer ===
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "This audit was generated automatically from public website signals. "
        "All data is observed from publicly accessible HTML markup. "
        "© Idincode Apex Market Intelligence.",
        small,
    ))

    doc.build(story)
    return out_path


# ============================================================
# Helpers
# ============================================================
def _build_table(rows: list[list[str]]) -> "Table":
    table = Table(rows, colWidths=[60 * mm, 110 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f6f8")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#0f4c81")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dde2e7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def _safe(s) -> str:
    """Escape HTML special chars buat reportlab Paragraph."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _yes_no(v: bool) -> str:
    return "Yes (present)" if v else "No (gap)"


def _fmt_score(score) -> str:
    if score is None:
        return "N/A"
    if score >= 85:
        return f"{score} / 100 (Good)"
    if score >= 50:
        return f"{score} / 100 (Needs improvement)"
    return f"{score} / 100 (Poor)"


def _sanitize_filename(name: str) -> str:
    """Domain → safe filename."""
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return out[:80] or "unknown"
