from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable
from typing import Any


def safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def safe_int(value: Any, default: int = 0) -> int:
    number = safe_float(value, None)
    if number is None:
        return default
    return int(number)


def normalize_gpu_key(value: Any) -> str:
    return str(value or "").replace(" ", "_").strip()


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def earnings_hours(earnings_json: Any, warnings: list[str]) -> float:
    if not isinstance(earnings_json, dict):
        warnings.append("Earnings metadata missing; using 24.0 hours")
        return 24.0

    start = parse_iso_datetime(earnings_json.get("start_utc"))
    end = parse_iso_datetime(earnings_json.get("end_utc"))
    if not start or not end:
        warnings.append("Earnings start/end timestamps missing; using 24.0 hours")
        return 24.0

    hours = (end - start).total_seconds() / 3600.0
    if hours <= 0:
        warnings.append("Earnings time range is not positive; using 24.0 hours")
        return 24.0
    return hours


def summarize_status(
    rows: list[dict[str, str]], machine_id: str, observation_minutes: float
) -> dict[str, Any]:
    machine_rows = [
        row for row in rows if str(row.get("machine_id", "")) == str(machine_id)
    ]
    machine_rows.sort(key=lambda row: row.get("timestamp", ""))

    if not machine_rows:
        return {
            "records": 0,
            "d_count": 0,
            "i_count": 0,
            "x_count": 0,
            "other_count": 0,
            "occupancy_rate": None,
            "idle_hours": None,
            "observed_hours": 0.0,
            "latest": {},
            "on_demand": None,
            "interruptible": None,
        }

    d_count = 0
    i_count = 0
    x_count = 0
    other_count = 0
    for row in machine_rows:
        occupancy = str(row.get("occupancy", "")).strip()
        if occupancy == "D_":
            d_count += 1
        elif occupancy == "I_":
            i_count += 1
        elif occupancy == "x_":
            x_count += 1
        else:
            other_count += 1

    records = len(machine_rows)
    occupied_count = d_count + i_count
    latest = machine_rows[-1]
    return {
        "records": records,
        "d_count": d_count,
        "i_count": i_count,
        "x_count": x_count,
        "other_count": other_count,
        "occupancy_rate": occupied_count / records if records else None,
        "idle_hours": x_count * observation_minutes / 60.0,
        "observed_hours": records * observation_minutes / 60.0,
        "latest": latest,
        "on_demand": safe_float(latest.get("on_demand_dph"), None),
        "interruptible": safe_float(latest.get("interruptible_dph"), None),
    }


def latest_reliability(rows: list[dict[str, str]], machine_id: str) -> float | None:
    machine_rows = [
        row for row in rows if str(row.get("machine_id", "")) == str(machine_id)
    ]
    machine_rows.sort(key=lambda row: row.get("timestamp", ""))
    if not machine_rows:
        return None
    return safe_float(machine_rows[-1].get("reliability2"), None)


def earnings_by_machine(rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        if row.get("scope") != "machine":
            continue
        machine_id = str(row.get("machine_id", "")).strip()
        if not machine_id:
            continue

        gpu = safe_float(row.get("gpu_earn"), 0.0) or 0.0
        storage = safe_float(row.get("storage_earn"), 0.0) or 0.0
        bandwidth_up = safe_float(row.get("bandwidth_up_earn"), 0.0) or 0.0
        bandwidth_down = safe_float(row.get("bandwidth_down_earn"), 0.0) or 0.0
        total = safe_float(row.get("total_earn"), None)
        if total is None:
            total = gpu + storage + bandwidth_up + bandwidth_down

        current = result.setdefault(
            machine_id,
            {
                "gpu_earn": 0.0,
                "storage_earn": 0.0,
                "bandwidth_earn": 0.0,
                "total_earn": 0.0,
            },
        )
        current["gpu_earn"] += gpu
        current["storage_earn"] += storage
        current["bandwidth_earn"] += bandwidth_up + bandwidth_down
        current["total_earn"] += total
    return result


def summarize_earnings(rows: list[dict[str, str]]) -> dict[str, Any]:
    machine_rows = [row for row in rows if row.get("scope") == "machine"]
    day_rows = [row for row in rows if row.get("scope") == "day"]
    machines = earnings_by_machine(machine_rows)
    day = _sum_earning_rows(day_rows)

    machine_totals = _sum_earning_dicts(machines.values())
    machine_all_zero = bool(machine_rows) and not _has_earnings(machine_totals)
    day_has_earnings = _has_earnings(day)

    return {
        "machines": machines,
        "day": day,
        "machine_all_zero": machine_all_zero,
        "day_has_earnings": day_has_earnings,
        "suppress_machine_earnings": machine_all_zero and day_has_earnings,
    }


def _sum_earning_dicts(rows: Iterable[dict[str, float]]) -> dict[str, float]:
    result = {
        "gpu_earn": 0.0,
        "storage_earn": 0.0,
        "bandwidth_earn": 0.0,
        "total_earn": 0.0,
    }
    for row in rows:
        result["gpu_earn"] += row.get("gpu_earn", 0.0)
        result["storage_earn"] += row.get("storage_earn", 0.0)
        result["bandwidth_earn"] += row.get("bandwidth_earn", 0.0)
        result["total_earn"] += row.get("total_earn", 0.0)
    return result


def _has_earnings(row: dict[str, float]) -> bool:
    return any(abs(value) >= 1e-12 for value in row.values())


def _sum_earning_rows(rows: list[dict[str, str]]) -> dict[str, float]:
    result = {
        "gpu_earn": 0.0,
        "storage_earn": 0.0,
        "bandwidth_earn": 0.0,
        "total_earn": 0.0,
    }
    for row in rows:
        gpu = safe_float(row.get("gpu_earn"), 0.0) or 0.0
        storage = safe_float(row.get("storage_earn"), 0.0) or 0.0
        bandwidth_up = safe_float(row.get("bandwidth_up_earn"), 0.0) or 0.0
        bandwidth_down = safe_float(row.get("bandwidth_down_earn"), 0.0) or 0.0
        total = safe_float(row.get("total_earn"), None)
        if total is None:
            total = gpu + storage + bandwidth_up + bandwidth_down

        result["gpu_earn"] += gpu
        result["storage_earn"] += storage
        result["bandwidth_earn"] += bandwidth_up + bandwidth_down
        result["total_earn"] += total
    return result


def market_by_gpu(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        gpu_key = normalize_gpu_key(row.get("gpu"))
        if not gpu_key:
            continue
        result[gpu_key] = {
            "records": safe_int(row.get("records")),
            "first_utc": row.get("first_utc", ""),
            "last_utc": row.get("last_utc", ""),
            "avg_rented_median": safe_float(row.get("avg_rented_median"), None),
            "last_rented_median": safe_float(row.get("last_rented_median"), None),
            "avg_available_median": safe_float(
                row.get("avg_available_median"), None
            ),
            "last_available_median": safe_float(
                row.get("last_available_median"), None
            ),
            "avg_market_utilization": safe_float(
                row.get("avg_market_utilization"), None
            ),
            "last_market_utilization": safe_float(
                row.get("last_market_utilization"), None
            ),
        }
    return result


def rank_candidate(prices: list[float], candidate_total: float) -> tuple[int | None, int]:
    if not prices:
        return None, 1
    cheaper_count = sum(1 for price in prices if price < candidate_total)
    return cheaper_count + 1, len(prices) + 1


def candidate_price_rows(
    candidate_prices: list[Any], storage_adjustment: float, prices: list[float]
) -> list[dict[str, float | int | None]]:
    rows: list[dict[str, float | int | None]] = []
    for value in candidate_prices:
        candidate = safe_float(value, None)
        if candidate is None:
            continue
        estimated_total = candidate + storage_adjustment
        rank, total = rank_candidate(prices, estimated_total)
        rows.append(
            {
                "candidate": candidate,
                "estimated_total": estimated_total,
                "rank": rank,
                "total": total,
            }
        )
    return rows
