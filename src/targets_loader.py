# src/targets_loader.py
from __future__ import annotations
from pathlib import Path
import yaml

from src.models import Target, TargetsValidationError


def load_targets(path: str | Path = "targets.yaml") -> list[Target]:
    """Parse targets.yaml -> list of Target."""
    path = Path(path)
    if not path.exists():
        raise TargetsValidationError(f"targets.yaml not found at {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise TargetsValidationError(f"YAML parse error: {e}") from e

    if not isinstance(data, dict):
        raise TargetsValidationError("Root of targets.yaml must be a mapping")

    categories = data.get("categories", [])
    if not isinstance(categories, list):
        raise TargetsValidationError("'categories' must be a list")

    targets: list[Target] = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        cat_name = cat.get("name", "")
        niche = cat.get("niche", "unknown")
        for t in cat.get("targets", []) or []:
            if not isinstance(t, dict) or "domain" not in t:
                continue
            domain = str(t["domain"]).strip()
            if not domain:
                continue
            location = str(t.get("location", "") or "")
            extra = {k: v for k, v in t.items() if k not in ("domain", "location")}
            targets.append(Target(
                domain=domain,
                niche=niche,
                category_name=cat_name,
                location=location,
                extra=extra,
            ))

    if not targets:
        raise TargetsValidationError("No valid targets found in targets.yaml")

    return targets
