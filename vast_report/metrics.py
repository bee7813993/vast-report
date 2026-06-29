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

        current = result.setdefault(
            machine_id,
            _empty_earnings(),
        )
        _add_earnings(current, _earnings_from_mapping(row))
    return result


def earnings_by_machine_json(
    earnings_json: Any, machine_ids: Iterable[str] | None = None
) -> dict[str, dict[str, float]]:
    if not isinstance(earnings_json, dict):
        return {}

    wanted = {str(machine_id) for machine_id in machine_ids or []}
    result: dict[str, dict[str, float]] = {}
    for row in _iter_json_records(earnings_json.get("per_machine")):
        machine_id = _record_machine_id(row)
        if not machine_id:
            continue
        if wanted and machine_id not in wanted:
            continue
        current = result.setdefault(machine_id, _empty_earnings())
        _add_earnings(current, _earnings_from_mapping(row))
    return result


def summarize_earnings(
    rows: list[dict[str, str]],
    earnings_json: Any = None,
    machine_earnings_jsons: dict[str, Any] | None = None,
    machine_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    machine_rows = [row for row in rows if row.get("scope") == "machine"]
    day_rows = [row for row in rows if row.get("scope") == "day"]
    machines = earnings_by_machine(machine_rows)
    machine_sources = {
        machine_id: "earnings-last24h-summary.tsv:machine"
        for machine_id in machines
    }

    configured_machine_ids = [str(machine_id) for machine_id in machine_ids or []]

    machine_json_summaries: dict[str, dict[str, float]] = {}
    machine_json_per_machine: dict[str, dict[str, float]] = {}
    for machine_id, machine_json in (machine_earnings_jsons or {}).items():
        machine_id = str(machine_id)
        per_machine = earnings_by_machine_json(machine_json, [machine_id])
        if machine_id in per_machine:
            machine_json_per_machine[machine_id] = per_machine[machine_id]
            continue
        if isinstance(machine_json, dict) and _has_json_records(
            machine_json.get("per_machine")
        ):
            continue
        summary = _summary_earnings_from_json(machine_json)
        if summary is not None:
            machine_json_summaries[machine_id] = summary

    for machine_id, earnings in machine_json_summaries.items():
        machines[machine_id] = earnings
        machine_sources[machine_id] = "machine-earnings.json:summary"

    for machine_id, earnings in earnings_by_machine_json(
        earnings_json, configured_machine_ids
    ).items():
        machines[machine_id] = earnings
        machine_sources[machine_id] = "earnings-last24h.json:per_machine"

    for machine_id, earnings in machine_json_per_machine.items():
        machines[machine_id] = earnings
        machine_sources[machine_id] = "machine-earnings.json:per_machine"

    json_day = earnings_day_total_json(earnings_json)
    if json_day is not None:
        day = json_day
        day_source = "earnings-last24h.json:per_day"
    else:
        day = _sum_earning_rows(day_rows)
        day_source = "earnings-last24h-summary.tsv:day"

    machine_totals = _sum_earning_dicts(machines.values())
    tsv_machine_totals = _sum_earning_dicts(earnings_by_machine(machine_rows).values())
    machine_all_zero = bool(machine_rows) and not _has_earnings(tsv_machine_totals)
    day_has_earnings = _has_earnings(day)
    suppress_machine_earnings = (
        machine_all_zero and day_has_earnings and not _has_earnings(machine_totals)
    )

    return {
        "machines": machines,
        "day": day,
        "machine_sources": machine_sources,
        "day_source": day_source,
        "machine_all_zero": machine_all_zero,
        "day_has_earnings": day_has_earnings,
        "suppress_machine_earnings": suppress_machine_earnings,
    }


def earnings_day_total_json(earnings_json: Any) -> dict[str, float] | None:
    if not isinstance(earnings_json, dict):
        return None

    rows = list(_iter_json_records(earnings_json.get("per_day")))
    if not rows:
        return None
    return _sum_earning_mappings(rows)


def _sum_earning_dicts(rows: Iterable[dict[str, float]]) -> dict[str, float]:
    result = _empty_earnings()
    for row in rows:
        _add_earnings(result, row)
    return result


def _has_earnings(row: dict[str, float]) -> bool:
    return any(abs(value) >= 1e-12 for value in row.values())


def _sum_earning_rows(rows: list[dict[str, str]]) -> dict[str, float]:
    return _sum_earning_mappings(rows)


def _sum_earning_mappings(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    result = _empty_earnings()
    for row in rows:
        _add_earnings(result, _earnings_from_mapping(row))
    return result


def _summary_earnings_from_json(earnings_json: Any) -> dict[str, float] | None:
    if not isinstance(earnings_json, dict):
        return None
    summary = earnings_json.get("summary")
    if not isinstance(summary, dict):
        return None
    return _earnings_from_mapping(summary)


def _empty_earnings() -> dict[str, float]:
    return {
        "gpu_earn": 0.0,
        "storage_earn": 0.0,
        "bandwidth_earn": 0.0,
        "total_earn": 0.0,
    }


def _add_earnings(
    target: dict[str, float], values: dict[str, float]
) -> dict[str, float]:
    target["gpu_earn"] += values.get("gpu_earn", 0.0)
    target["storage_earn"] += values.get("storage_earn", 0.0)
    target["bandwidth_earn"] += values.get("bandwidth_earn", 0.0)
    target["total_earn"] += values.get("total_earn", 0.0)
    return target


def _earnings_from_mapping(row: dict[str, Any]) -> dict[str, float]:
    gpu = _first_float(row, ("gpu_earn", "total_gpu", "gpu"), None)
    # Vast API component rows use total_earn for GPU earnings, unlike the TSV.
    api_total_earn_is_gpu = gpu is None and any(
        key in row for key in ("total_stor", "total_bwu", "total_bwd")
    )
    if api_total_earn_is_gpu:
        gpu = _first_float(row, ("total_earn",), 0.0)
    if gpu is None:
        gpu = 0.0

    storage = (
        _first_float(row, ("storage_earn", "total_stor", "stor_earn", "storage"), 0.0)
        or 0.0
    )

    bandwidth = _first_float(row, ("bandwidth_earn", "bandwidth_total"), None)
    if bandwidth is None:
        bandwidth_up = (
            _first_float(row, ("bandwidth_up_earn", "total_bwu", "bwu"), 0.0) or 0.0
        )
        bandwidth_down = (
            _first_float(row, ("bandwidth_down_earn", "total_bwd", "bwd"), 0.0)
            or 0.0
        )
        bandwidth = bandwidth_up + bandwidth_down

    total_names = ("total",) if api_total_earn_is_gpu else ("total_earn", "total")
    total = _first_float(row, total_names, None)
    if total is None:
        total = gpu + storage + bandwidth

    return {
        "gpu_earn": gpu,
        "storage_earn": storage,
        "bandwidth_earn": bandwidth,
        "total_earn": total,
    }


def _first_float(
    row: dict[str, Any], names: Iterable[str], default: float | None
) -> float | None:
    for name in names:
        if name not in row:
            continue
        value = safe_float(row.get(name), None)
        if value is not None:
            return value
    return default


def _iter_json_records(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row.setdefault("machine_id", key)
            yield row


def _has_json_records(value: Any) -> bool:
    return any(True for _ in _iter_json_records(value))


def _record_machine_id(row: dict[str, Any]) -> str:
    for key in ("machine_id", "machineId", "id"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


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
