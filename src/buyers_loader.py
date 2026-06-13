"""Load buyers.yaml -> typed config untuk find_buyer.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class BuyerNiche:
    keyword: str
    country: str = "US"


@dataclass
class BuyerConfig:
    country: str = "US"
    max_agencies_per_niche: int = 30
    max_persons_per_agency: int = 5
    max_concurrent: int = 4
    niches: list[BuyerNiche] = field(default_factory=list)


def load_buyers(path: str | Path = "buyers.yaml") -> BuyerConfig:
    """Load & validate buyers.yaml.

    Note: `min_confidence` di buyers.yaml diabaikan (backward-compat) —
    sejak v3.1 software ini cuma menerima email yang LITERAL muncul di page
    (no guessing/inference), confidence selalu 1.0.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"buyers.yaml tidak ditemukan di {p.absolute()}. "
            f"Lihat README / PENJELASAN.md untuk contoh."
        )

    with p.open("r", encoding="utf-8") as f:
        raw: Any = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ValueError("buyers.yaml harus dict di top-level.")

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("buyers.yaml 'defaults' harus dict.")

    niches_raw = raw.get("niches") or []
    if not isinstance(niches_raw, list) or not niches_raw:
        raise ValueError(
            "buyers.yaml harus punya key 'niches' yang berisi minimal 1 entry."
        )

    default_country = str(defaults.get("country", "US")).strip() or "US"

    niches: list[BuyerNiche] = []
    for idx, n in enumerate(niches_raw):
        if not isinstance(n, dict):
            raise ValueError(f"niches[{idx}] bukan dict")
        kw = str(n.get("keyword", "")).strip()
        if not kw:
            raise ValueError(f"niches[{idx}] missing 'keyword'")
        country = str(n.get("country", default_country)).strip() or default_country
        niches.append(BuyerNiche(keyword=kw, country=country))

    return BuyerConfig(
        country=default_country,
        max_agencies_per_niche=int(defaults.get("max_agencies_per_niche", 30)),
        max_persons_per_agency=int(defaults.get("max_persons_per_agency", 5)),
        max_concurrent=int(defaults.get("max_concurrent", 4)),
        niches=niches,
    )
