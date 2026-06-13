# src/loader.py
"""Load targets dari targets.yaml dengan validasi."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.models import Target


REQUIRED_FIELDS = ("domain", "location", "niche", "category")


def load_targets(yaml_path: str | Path = "targets.yaml") -> list[Target]:
    """Load & validate targets dari YAML file.
    
    Raises:
        FileNotFoundError: kalau YAML gak ada.
        ValueError: kalau struktur YAML salah / missing fields.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(
            f"targets.yaml tidak ditemukan di {path.absolute()}. "
            f"Buat dulu file targets.yaml di root project."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(
            f"targets.yaml harus dict di top-level, dapat {type(raw).__name__}"
        )

    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, list):
        raise ValueError(
            "targets.yaml harus punya key 'targets' yang berisi list."
        )

    if not targets_raw:
        raise ValueError("targets.yaml kosong — minimal harus ada 1 target.")

    targets: list[Target] = []
    for idx, item in enumerate(targets_raw):
        if not isinstance(item, dict):
            raise ValueError(
                f"Target index {idx} bukan dict (dapat {type(item).__name__})"
            )

        missing = [f for f in REQUIRED_FIELDS if f not in item or not item[f]]
        if missing:
            raise ValueError(
                f"Target index {idx} (domain={item.get('domain', '?')}) "
                f"missing field: {', '.join(missing)}"
            )

        # Normalisasi domain (lowercase, strip protocol kalau ada)
        domain = _normalize_domain(item["domain"])

        targets.append(
            Target(
                domain=domain,
                location=str(item["location"]).strip(),
                niche=str(item["niche"]).strip().lower(),
                category=str(item["category"]).strip(),
            )
        )

    return targets


def _normalize_domain(raw: Any) -> str:
    """Strip protocol & trailing slash dari domain."""
    s = str(raw).strip().lower()
    s = s.removeprefix("https://").removeprefix("http://")
    s = s.removeprefix("www.")
    s = s.rstrip("/")
    return s
