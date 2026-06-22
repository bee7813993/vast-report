from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .metrics import safe_float


def read_tsv(path: Path, warnings: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        warnings.append(f"Missing file: {path.name}")
        return []
    if path.stat().st_size == 0:
        warnings.append(f"Empty file: {path.name}")
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))
    except Exception as exc:
        warnings.append(f"Could not read TSV {path.name}: {exc}")
        return []


def read_json(path: Path, warnings: list[str]) -> Any:
    if not path.exists():
        warnings.append(f"Missing file: {path.name}")
        return None
    if path.stat().st_size == 0:
        warnings.append(f"Empty file: {path.name}")
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        warnings.append(f"Could not read JSON {path.name}: {exc}")
        return None


def offer_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("offers"), list):
        return [item for item in data["offers"] if isinstance(item, dict)]
    return []


def offer_prices(path: Path, warnings: list[str]) -> list[float]:
    data = read_json(path, warnings)
    if data is None:
        return []

    prices: list[float] = []
    for offer in offer_list(data):
        price = safe_float(offer.get("dph_total"), None)
        if price is None:
            price = safe_float(offer.get("discounted_dph_total"), None)
        if price is not None:
            prices.append(price)

    if not prices:
        warnings.append(f"No usable offer prices found in {path.name}")
    return sorted(prices)
