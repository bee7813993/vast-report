from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .archive import archive_date_from_name, extracted_archive, find_latest_archive
from .config import load_config, resolve_config_path
from .contract_state import (
    empty_contract_state,
    load_contract_state,
    update_contract_state_from_status_rows,
    write_contract_state_atomic,
)
from .loaders import offer_prices, read_json, read_tsv
from .metrics import (
    candidate_price_rows,
    earnings_hours,
    latest_reliability,
    market_by_gpu,
    safe_float,
    summarize_earnings,
    summarize_status,
)
from .recommendations import choose_recommendation
from .render import recommendation_payload, render_markdown


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a Vast.ai daily archive and generate a report."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--latest", action="store_true", help="Use latest archive")
    source.add_argument("--archive", help="Path to vast-daily-YYYY-MM-DD.txz")
    source.add_argument(
        "--rebuild-state",
        action="store_true",
        help="Rebuild contract state from all archives in archive_dir",
    )
    parser.add_argument("--config", default="config.yaml", help="Config YAML path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config_path = Path(args.config).expanduser()
    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")

    try:
        config = load_config(config_path)
    except Exception as exc:
        raise SystemExit(f"Could not load config: {exc}") from exc

    warnings: list[str] = []
    state_path = resolve_config_path(
        config.get("state_file", "state/machine-contract-state.json"),
        config_path,
    )

    if args.rebuild_state:
        archive_dir = resolve_config_path(config.get("archive_dir", "."), config_path)
        try:
            count = rebuild_contract_state(
                archive_dir=archive_dir,
                state_path=state_path,
                config=config,
                warnings=warnings,
            )
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Contract state rebuilt from {count} archives: {state_path}")
        if warnings:
            print(f"Warnings: {len(warnings)}")
        return

    if args.archive:
        archive_path = Path(args.archive).expanduser()
    else:
        archive_dir = resolve_config_path(config.get("archive_dir", "."), config_path)
        try:
            archive_path = find_latest_archive(archive_dir)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc

    if not archive_path.exists():
        raise SystemExit(f"Archive not found: {archive_path}")

    report_date = archive_date_from_name(archive_path)
    report_dir = resolve_config_path(config.get("report_dir", "reports"), config_path)
    contract_state = load_contract_state(state_path, warnings)

    with extracted_archive(archive_path) as (data_dir, archive_warnings):
        warnings.extend(archive_warnings)
        if (data_dir / "earnings-last24h.raw.json").exists():
            warnings.append("Ignoring earnings-last24h.raw.json by policy")

        machine_reports, earnings_duration, host_total_earn_per_hour = analyze_data_dir(
            data_dir=data_dir,
            config=config,
            contract_state=contract_state,
            warnings=warnings,
        )

    auto_apply = bool(config.get("strategy", {}).get("auto_apply_price_change", False))
    if auto_apply:
        warnings.append(
            "auto_apply_price_change is true in config, but this CLI only reports "
            "recommendations and never changes Vast.ai prices"
        )
        auto_apply = False

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"report-{report_date}.md"
    recommendation_path = report_dir / f"recommendation-{report_date}.json"

    report_text = render_markdown(
        report_date=report_date,
        archive_path=archive_path,
        machine_reports=machine_reports,
        earnings_hours=earnings_duration,
        host_total_earn_per_hour=host_total_earn_per_hour,
        warnings=warnings,
    )
    payload = recommendation_payload(
        report_date=report_date,
        archive_path=archive_path,
        machine_reports=machine_reports,
        host_total_earn_per_hour=host_total_earn_per_hour,
        warnings=warnings,
        auto_apply_price_change=auto_apply,
    )

    report_path.write_text(report_text, encoding="utf-8")
    with recommendation_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    write_contract_state_atomic(state_path, contract_state)

    print(f"Report written: {report_path}")
    print(f"Recommendation written: {recommendation_path}")
    print(f"Contract state written: {state_path}")
    if warnings:
        print(f"Warnings: {len(warnings)}")


def rebuild_contract_state(
    archive_dir: Path,
    state_path: Path,
    config: dict[str, Any],
    warnings: list[str],
) -> int:
    archives = sorted(archive_dir.glob("vast-daily-*.txz"))
    if not archives:
        raise FileNotFoundError(f"No archive found under {archive_dir}")

    contract_state = empty_contract_state()
    processed = 0
    for archive_path in archives:
        with extracted_archive(archive_path) as (data_dir, archive_warnings):
            warnings.extend(archive_warnings)
            status_rows = read_tsv(data_dir / "machine-status-last24h.tsv", warnings)
            update_contract_state_from_status_rows(
                state=contract_state,
                status_rows=status_rows,
                machine_configs=config["machines"],
                warnings=warnings,
            )
        processed += 1

    write_contract_state_atomic(state_path, contract_state)
    return processed


def analyze_data_dir(
    data_dir: Path,
    config: dict[str, Any],
    contract_state: dict[str, Any],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], float, float | None]:
    status_rows = read_tsv(data_dir / "machine-status-last24h.tsv", warnings)
    reliability_rows = read_tsv(data_dir / "reliability-last24h.tsv", warnings)
    market_rows = read_tsv(data_dir / "gpu-market-summary.tsv", warnings)
    earnings_rows = read_tsv(data_dir / "earnings-last24h-summary.tsv", warnings)
    earnings_json = read_json(data_dir / "earnings-last24h.json", warnings)

    duration = earnings_hours(earnings_json, warnings)
    market_map = market_by_gpu(market_rows)
    earnings_summary = summarize_earnings(earnings_rows)
    earnings_map = earnings_summary["machines"]
    suppress_machine_earnings = earnings_summary["suppress_machine_earnings"]
    host_total_earn_per_hour = None
    if earnings_summary["day_has_earnings"] and duration > 0:
        host_total_earn_per_hour = earnings_summary["day"]["total_earn"] / duration
    if suppress_machine_earnings:
        warnings.append(
            "earnings-last24h-summary.tsv has zero machine earnings but non-zero "
            "day earnings; machine GPU収益/h and 総収益/h are shown as '-' and "
            "host total_earn_per_hour is calculated from day rows"
        )
    observation_minutes = safe_float(
        config.get("strategy", {}).get("observation_minutes"), 30.0
    )
    if observation_minutes is None or observation_minutes <= 0:
        warnings.append("Invalid observation_minutes; using 30")
        observation_minutes = 30.0
    contract_map = update_contract_state_from_status_rows(
        state=contract_state,
        status_rows=status_rows,
        machine_configs=config["machines"],
        warnings=warnings,
    )

    machine_reports: list[dict[str, Any]] = []
    for machine_id, machine_cfg in config["machines"].items():
        machine_report = analyze_machine(
            data_dir=data_dir,
            machine_id=str(machine_id),
            machine_cfg=machine_cfg,
            status_rows=status_rows,
            reliability_rows=reliability_rows,
            market_map=market_map,
            earnings_map=earnings_map,
            suppress_machine_earnings=suppress_machine_earnings,
            contract_info=contract_map.get(str(machine_id), {}),
            earnings_duration=duration,
            observation_minutes=observation_minutes,
            warnings=warnings,
        )
        machine_reports.append(machine_report)

    return machine_reports, duration, host_total_earn_per_hour


def analyze_machine(
    data_dir: Path,
    machine_id: str,
    machine_cfg: dict[str, Any],
    status_rows: list[dict[str, str]],
    reliability_rows: list[dict[str, str]],
    market_map: dict[str, dict[str, Any]],
    earnings_map: dict[str, dict[str, float]],
    suppress_machine_earnings: bool,
    contract_info: dict[str, Any],
    earnings_duration: float,
    observation_minutes: float,
    warnings: list[str],
) -> dict[str, Any]:
    name = str(machine_cfg.get("name", machine_id))
    gpu_key = str(machine_cfg.get("gpu_key", "")).strip()

    status = summarize_status(status_rows, machine_id, observation_minutes)
    reliability = latest_reliability(reliability_rows, machine_id)
    if reliability is None:
        warnings.append(f"Reliability missing for machine {machine_id}")

    current_on_demand = status["on_demand"]
    if current_on_demand is None:
        current_on_demand = safe_float(machine_cfg.get("current_on_demand"), None)

    current_interruptible = status["interruptible"]
    if current_interruptible is None:
        current_interruptible = safe_float(
            machine_cfg.get("current_interruptible"), None
        )

    occupancy_rate = status["occupancy_rate"]
    active_contract_price_estimate = safe_float(
        contract_info.get("active_contract_price_estimate"), None
    )
    active_contract_price_source = contract_info.get("active_contract_price_source")
    active_contract_type = contract_info.get("active_contract_type")
    active_contract_started_at = contract_info.get("active_contract_started_at")

    gpu_effective_by_listed_price = (
        current_on_demand * occupancy_rate
        if current_on_demand is not None and occupancy_rate is not None
        else None
    )
    gpu_effective_by_contract_estimate = (
        active_contract_price_estimate * occupancy_rate
        if active_contract_price_estimate is not None and occupancy_rate is not None
        else None
    )
    gpu_effective_by_status = (
        gpu_effective_by_contract_estimate
        if gpu_effective_by_contract_estimate is not None
        else gpu_effective_by_listed_price
    )

    earnings = earnings_map.get(machine_id, {})
    gpu_earn_per_hour = None
    total_earn_per_hour = None
    if earnings and earnings_duration > 0 and not suppress_machine_earnings:
        gpu_earn_per_hour = earnings.get("gpu_earn", 0.0) / earnings_duration
        total_earn_per_hour = earnings.get("total_earn", 0.0) / earnings_duration

    prices = offer_prices(data_dir / f"{gpu_key}-offers-price.json", warnings)
    storage_adjustment = safe_float(machine_cfg.get("storage_dph_adjustment"), 0.0)
    if storage_adjustment is None:
        storage_adjustment = 0.0
    candidate_rows = candidate_price_rows(
        machine_cfg.get("candidate_on_demand", []), storage_adjustment, prices
    )

    recommendation = choose_recommendation(
        machine_cfg=machine_cfg,
        status=status,
        reliability=reliability,
        gpu_effective_by_status=gpu_effective_by_status,
        candidate_rows=candidate_rows,
    )

    return {
        "machine_id": machine_id,
        "name": name,
        "gpu_key": gpu_key,
        "status": status,
        "reliability": reliability,
        "earnings": earnings,
        "earnings_hours": earnings_duration,
        "current_on_demand": current_on_demand,
        "current_interruptible": current_interruptible,
        "current_listed_on_demand": current_on_demand,
        "current_listed_interruptible": current_interruptible,
        "active_contract_type": active_contract_type,
        "active_contract_started_at": active_contract_started_at,
        "active_contract_price_estimate": active_contract_price_estimate,
        "active_contract_price_source": active_contract_price_source,
        "gpu_effective_by_status": gpu_effective_by_status,
        "gpu_effective_by_listed_price": gpu_effective_by_listed_price,
        "gpu_effective_by_contract_estimate": gpu_effective_by_contract_estimate,
        "gpu_earn_per_hour": gpu_earn_per_hour,
        "total_earn_per_hour": total_earn_per_hour,
        "market": market_map.get(gpu_key, {}),
        "candidate_rows": candidate_rows,
        "recommendation": recommendation,
    }
