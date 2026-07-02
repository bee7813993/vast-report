from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections.abc import Iterable
from pathlib import Path
import subprocess
import sys
from typing import Any

from .config import load_config


ALLOWED_EARNING_FIELDS = {
    "machine_id",
    "machineId",
    "id",
    "day",
    "gpu_earn",
    "total_gpu",
    "gpu",
    "total_earn",
    "total",
    "storage_earn",
    "total_stor",
    "stor_earn",
    "sto_earn",
    "storage",
    "bandwidth_earn",
    "bandwidth_total",
    "bwu_bwd_earn",
    "bandwidth_up_earn",
    "bandwidth_down_earn",
    "total_bwu",
    "total_bwd",
    "bwu_earn",
    "bwd_earn",
    "bwu",
    "bwd",
    "sla_earn",
    "total_sla",
}
TIME_FIELDS = ("start_utc", "end_utc")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect machine-specific Vast.ai earnings without saving raw "
            "earnings responses."
        )
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory inside the daily archive staging area.",
    )
    parser.add_argument(
        "--machine-id",
        action="append",
        dest="machine_ids",
        help="Machine ID to collect. Can be specified more than once.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Config YAML path used to read machine IDs when --machine-id is omitted.",
    )
    parser.add_argument(
        "--vastai-bin",
        default=os.environ.get("VASTAI_BIN", "vastai"),
        help="Vast.ai CLI executable. Default: vastai or VASTAI_BIN.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-machine command timeout in seconds.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    output_dir = Path(args.output_dir).expanduser()
    machine_ids = resolve_machine_ids(args.machine_ids, Path(args.config))
    if not machine_ids:
        raise SystemExit("No machine IDs configured")

    output_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for machine_id in machine_ids:
        try:
            payload = collect_machine_earnings(
                machine_id=machine_id,
                vastai_bin=args.vastai_bin,
                timeout=args.timeout,
            )
            if not payload_has_machine_earnings(payload):
                raise RuntimeError("no machine-specific earnings found")
        except Exception as exc:
            failures += 1
            print(f"Failed machine {machine_id}: {exc}", file=sys.stderr)
            continue

        path = output_dir / f"earnings-last24h-machine-{machine_id}.json"
        write_json_atomic(path, payload)
        print(f"Wrote {path}")
    if failures:
        raise SystemExit(1)


def resolve_machine_ids(
    machine_ids: list[str] | None, config_path: Path
) -> list[str]:
    if machine_ids:
        return [str(machine_id) for machine_id in machine_ids]
    config = load_config(config_path.expanduser())
    return [str(machine_id) for machine_id in config["machines"].keys()]


def collect_machine_earnings(
    machine_id: str, vastai_bin: str = "vastai", timeout: float = 120.0
) -> dict[str, Any]:
    command = [vastai_bin, "show", "earnings", "--machine_id", str(machine_id)]
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"vastai show earnings failed for machine {machine_id}: {message}"
        )

    raw = parse_json_stdout(completed.stdout)
    return build_machine_earnings_payload(raw, machine_id)


def build_machine_earnings_payload(
    raw: Any, machine_id: str, collected_at_utc: str | None = None
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Vast earnings response must be a JSON object")

    machine_id = str(machine_id)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "machine_id": machine_id,
        "collected_at_utc": collected_at_utc or utc_now_text(),
        "source": "vastai show earnings --machine_id",
    }
    for field in TIME_FIELDS:
        if field in raw and is_json_scalar(raw[field]):
            payload[field] = raw[field]

    per_machine = []
    for row in iter_json_records(raw.get("per_machine")):
        if record_machine_id(row) != machine_id:
            continue
        clean = sanitized_earning_mapping(row)
        clean["machine_id"] = machine_id
        per_machine.append(clean)

    if per_machine:
        payload["per_machine"] = per_machine
    else:
        summary = sanitized_summary(raw)
        if summary:
            payload["summary"] = summary

    return payload


def payload_has_machine_earnings(payload: dict[str, Any]) -> bool:
    return bool(payload.get("per_machine")) or bool(payload.get("summary"))


def sanitized_summary(raw: dict[str, Any]) -> dict[str, Any]:
    summary = raw.get("summary")
    if isinstance(summary, dict):
        return sanitized_earning_mapping(summary)
    return sanitized_earning_mapping(raw, include_machine_keys=False)


def sanitized_earning_mapping(
    row: dict[str, Any], include_machine_keys: bool = True
) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in row.items():
        if key not in ALLOWED_EARNING_FIELDS:
            continue
        if not include_machine_keys and key in {"machine_id", "machineId", "id"}:
            continue
        if is_json_scalar(value):
            clean[key] = value
    return clean


def parse_json_stdout(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Vast earnings command returned empty output")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = stripped.find(start_char)
        end = stripped.rfind(end_char)
        if start == -1 or end == -1 or end <= start:
            continue
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            continue
    raise ValueError("Vast earnings command did not return JSON")


def iter_json_records(value: Any) -> Iterable[dict[str, Any]]:
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


def record_machine_id(row: dict[str, Any]) -> str:
    for key in ("machine_id", "machineId", "id"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def utc_now_text() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    main()
