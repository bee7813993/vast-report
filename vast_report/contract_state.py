from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from .metrics import parse_iso_datetime, safe_float


SCHEMA_VERSION = 1
OCCUPIED_TO_CONTRACT_TYPE = {
    "D_": "on_demand",
    "I_": "interruptible",
}
OCCUPIED_TO_TRANSITION_SOURCE = {
    "D_": "x_to_d_transition",
    "I_": "x_to_i_transition",
}


def empty_contract_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at_utc": _now_utc(),
        "machines": {},
    }


def load_contract_state(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        return empty_contract_state()
    try:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except Exception as exc:
        warnings.append(f"Could not read contract state {path}: {exc}; using empty state")
        return empty_contract_state()

    if not isinstance(state, dict):
        warnings.append(f"Contract state {path} is not an object; using empty state")
        return empty_contract_state()
    machines = state.get("machines")
    if not isinstance(machines, dict):
        state["machines"] = {}
    state["schema_version"] = SCHEMA_VERSION
    return state


def write_contract_state_atomic(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["schema_version"] = SCHEMA_VERSION
    state["updated_at_utc"] = _now_utc()

    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp_path, path)


def update_contract_state_from_status_rows(
    state: dict[str, Any],
    status_rows: list[dict[str, str]],
    machine_configs: dict[str, Any],
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    machines = state.setdefault("machines", {})
    result: dict[str, dict[str, Any]] = {}

    for machine_id in machine_configs:
        machine_id_text = str(machine_id)
        rows = [
            row
            for row in status_rows
            if str(row.get("machine_id", "")) == machine_id_text
        ]
        rows.sort(key=lambda row: row.get("timestamp", ""))
        machine_state = machines.setdefault(machine_id_text, {})
        _update_machine_state(machine_id_text, machine_state, rows, warnings)
        result[machine_id_text] = dict(machine_state)

    return result


def _update_machine_state(
    machine_id: str,
    machine_state: dict[str, Any],
    rows: list[dict[str, str]],
    warnings: list[str],
) -> None:
    if not rows:
        return

    rows = [
        row
        for row in rows
        if _is_newer_timestamp(row.get("timestamp"), machine_state.get("last_seen_at"))
    ]
    if not rows:
        return

    previous_occupancy = machine_state.get("last_seen_occupancy")
    first_occupied_at: str | None = None

    for row in rows:
        timestamp = str(row.get("timestamp", "")).strip() or None
        occupancy = str(row.get("occupancy", "")).strip()
        listed_on_demand = safe_float(row.get("on_demand_dph"), None)
        listed_interruptible = safe_float(row.get("interruptible_dph"), None)

        if timestamp:
            machine_state["last_seen_at"] = timestamp
        if occupancy:
            machine_state["last_seen_occupancy"] = occupancy
        if listed_on_demand is not None:
            machine_state["current_listed_on_demand"] = listed_on_demand
        if listed_interruptible is not None:
            machine_state["current_listed_interruptible"] = listed_interruptible

        if occupancy in OCCUPIED_TO_CONTRACT_TYPE and first_occupied_at is None:
            first_occupied_at = timestamp

        if previous_occupancy == "x_" and occupancy in OCCUPIED_TO_CONTRACT_TYPE:
            _start_contract_from_transition(
                machine_state=machine_state,
                occupancy=occupancy,
                timestamp=timestamp,
                listed_on_demand=listed_on_demand,
                listed_interruptible=listed_interruptible,
                machine_id=machine_id,
                warnings=warnings,
            )

        if occupancy == "x_":
            if timestamp:
                machine_state["last_idle_at"] = timestamp
            if listed_on_demand is not None:
                machine_state["last_idle_on_demand"] = listed_on_demand
            if listed_interruptible is not None:
                machine_state["last_idle_interruptible"] = listed_interruptible
            _clear_active_contract(machine_state)

        previous_occupancy = occupancy or previous_occupancy

    latest_occupancy = str(rows[-1].get("occupancy", "")).strip()
    if latest_occupancy in OCCUPIED_TO_CONTRACT_TYPE:
        _ensure_active_contract_estimate(
            machine_state=machine_state,
            occupancy=latest_occupancy,
            first_occupied_at=first_occupied_at,
            machine_id=machine_id,
            warnings=warnings,
        )


def _start_contract_from_transition(
    machine_state: dict[str, Any],
    occupancy: str,
    timestamp: str | None,
    listed_on_demand: float | None,
    listed_interruptible: float | None,
    machine_id: str,
    warnings: list[str],
) -> None:
    contract_type = OCCUPIED_TO_CONTRACT_TYPE[occupancy]
    if occupancy == "D_":
        price = safe_float(machine_state.get("last_idle_on_demand"), None)
        fallback_price = listed_on_demand
    else:
        price = safe_float(machine_state.get("last_idle_interruptible"), None)
        fallback_price = listed_interruptible

    source = OCCUPIED_TO_TRANSITION_SOURCE[occupancy]
    if price is None:
        price = fallback_price
        source = "current_listed_fallback"
        warnings.append(
            f"Machine {machine_id} entered {occupancy} without a stored idle price; "
            "active contract price estimate falls back to current listed price"
        )

    machine_state["active_contract_started_at"] = timestamp
    machine_state["active_contract_type"] = contract_type
    machine_state["active_contract_price_estimate"] = price
    machine_state["active_contract_price_source"] = source


def _ensure_active_contract_estimate(
    machine_state: dict[str, Any],
    occupancy: str,
    first_occupied_at: str | None,
    machine_id: str,
    warnings: list[str],
) -> None:
    if safe_float(machine_state.get("active_contract_price_estimate"), None) is not None:
        return

    contract_type = OCCUPIED_TO_CONTRACT_TYPE[occupancy]
    if occupancy == "D_":
        price = safe_float(machine_state.get("last_idle_on_demand"), None)
        current = safe_float(machine_state.get("current_listed_on_demand"), None)
    else:
        price = safe_float(machine_state.get("last_idle_interruptible"), None)
        current = safe_float(machine_state.get("current_listed_interruptible"), None)

    source = "last_idle_before_window"
    if price is None:
        price = current
        source = "current_listed_fallback"
        warnings.append(
            f"Machine {machine_id} stayed {occupancy} for the whole available window "
            "and no prior idle price exists; active contract estimate uses current "
            "listed price with low confidence"
        )

    machine_state["active_contract_started_at"] = (
        machine_state.get("active_contract_started_at") or first_occupied_at
    )
    machine_state["active_contract_type"] = contract_type
    machine_state["active_contract_price_estimate"] = price
    machine_state["active_contract_price_source"] = source


def _clear_active_contract(machine_state: dict[str, Any]) -> None:
    machine_state["active_contract_started_at"] = None
    machine_state["active_contract_type"] = None
    machine_state["active_contract_price_estimate"] = None
    machine_state["active_contract_price_source"] = None


def _now_utc() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _is_newer_timestamp(value: Any, last_seen: Any) -> bool:
    if not value or not last_seen:
        return True
    current = parse_iso_datetime(value)
    previous = parse_iso_datetime(last_seen)
    if current is None or previous is None:
        return str(value) > str(last_seen)
    return current > previous
