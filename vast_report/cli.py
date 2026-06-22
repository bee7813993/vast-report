from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .archive import archive_date_from_name, extracted_archive, find_latest_archive
from .config import load_config, resolve_config_path
from .loaders import offer_prices, read_json, read_tsv
from .metrics import (
    candidate_price_rows,
    earnings_by_machine,
    earnings_hours,
    latest_reliability,
    market_by_gpu,
    safe_float,
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

    warnings: list[str] = []
    report_date = archive_date_from_name(archive_path)
    report_dir = resolve_config_path(config.get("report_dir", "reports"), config_path)

    with extracted_archive(archive_path) as (data_dir, archive_warnings):
        warnings.extend(archive_warnings)
        if (data_dir / "earnings-last24h.raw.json").exists():
            warnings.append("Ignoring earnings-last24h.raw.json by policy")

        machine_reports, earnings_duration = analyze_data_dir(
            data_dir=data_dir,
            config=config,
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
        warnings=warnings,
    )
    payload = recommendation_payload(
        report_date=report_date,
        archive_path=archive_path,
        machine_reports=machine_reports,
        warnings=warnings,
        auto_apply_price_change=auto_apply,
    )

    report_path.write_text(report_text, encoding="utf-8")
    with recommendation_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"Report written: {report_path}")
    print(f"Recommendation written: {recommendation_path}")
    if warnings:
        print(f"Warnings: {len(warnings)}")


def analyze_data_dir(
    data_dir: Path,
    config: dict[str, Any],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], float]:
    status_rows = read_tsv(data_dir / "machine-status-last24h.tsv", warnings)
    reliability_rows = read_tsv(data_dir / "reliability-last24h.tsv", warnings)
    market_rows = read_tsv(data_dir / "gpu-market-summary.tsv", warnings)
    earnings_rows = read_tsv(data_dir / "earnings-last24h-summary.tsv", warnings)
    earnings_json = read_json(data_dir / "earnings-last24h.json", warnings)

    duration = earnings_hours(earnings_json, warnings)
    market_map = market_by_gpu(market_rows)
    earnings_map = earnings_by_machine(earnings_rows)
    observation_minutes = safe_float(
        config.get("strategy", {}).get("observation_minutes"), 30.0
    )
    if observation_minutes is None or observation_minutes <= 0:
        warnings.append("Invalid observation_minutes; using 30")
        observation_minutes = 30.0

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
            earnings_duration=duration,
            observation_minutes=observation_minutes,
            warnings=warnings,
        )
        machine_reports.append(machine_report)

    return machine_reports, duration


def analyze_machine(
    data_dir: Path,
    machine_id: str,
    machine_cfg: dict[str, Any],
    status_rows: list[dict[str, str]],
    reliability_rows: list[dict[str, str]],
    market_map: dict[str, dict[str, Any]],
    earnings_map: dict[str, dict[str, float]],
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
    gpu_effective_by_status = (
        current_on_demand * occupancy_rate
        if current_on_demand is not None and occupancy_rate is not None
        else None
    )

    earnings = earnings_map.get(machine_id, {})
    gpu_earn_per_hour = None
    total_earn_per_hour = None
    if earnings and earnings_duration > 0:
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
        "gpu_effective_by_status": gpu_effective_by_status,
        "gpu_earn_per_hour": gpu_earn_per_hour,
        "total_earn_per_hour": total_earn_per_hour,
        "market": market_map.get(gpu_key, {}),
        "candidate_rows": candidate_rows,
        "recommendation": recommendation,
    }
