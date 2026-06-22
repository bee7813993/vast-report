from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any


def fmt_money(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    return f"${float(value):.{digits}f}"


def fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.1f}%"


def fmt_hours(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f}h"


def fmt_float(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return "\n".join(lines)


def render_markdown(
    report_date: str,
    archive_path: Path,
    machine_reports: list[dict[str, Any]],
    earnings_hours: float,
    warnings: list[str],
) -> str:
    lines: list[str] = [
        f"# Vast.ai 日次レポート {report_date}",
        "",
        f"- Archive: `{archive_path}`",
        f"- Earnings集計時間: 約 {earnings_hours:.2f} h",
        "- 価格変更: 自動実行していません",
        "",
        "## 結論",
    ]

    conclusion_rows = []
    for report in machine_reports:
        rec = report["recommendation"]
        conclusion_rows.append(
            [
                report["name"],
                fmt_money(report["current_on_demand"]),
                fmt_money(rec["recommended_on_demand"]),
                rec["action"],
                rec["reason"],
            ]
        )
    lines.append(
        markdown_table(
            ["GPU", "現在On-demand価格", "推奨On-demand価格", "判断", "理由"],
            conclusion_rows,
        )
    )
    lines.extend(["", "## 直近24時間実績"])

    performance_rows = []
    for report in machine_reports:
        status = report["status"]
        performance_rows.append(
            [
                report["name"],
                status["records"],
                status["d_count"],
                status["i_count"],
                status["x_count"],
                fmt_pct(status["occupancy_rate"]),
                fmt_hours(status["idle_hours"]),
                fmt_money(report["gpu_effective_by_status"]),
                fmt_money(report["gpu_earn_per_hour"]),
                fmt_money(report["total_earn_per_hour"]),
                fmt_float(report["reliability"]),
            ]
        )
    lines.append(
        markdown_table(
            [
                "GPU",
                "records",
                "D_",
                "I_",
                "x_",
                "稼働率",
                "空き時間",
                "価格×稼働率",
                "GPU収益/h",
                "総収益/h",
                "Reliability",
            ],
            performance_rows,
        )
    )
    lines.extend(["", "## 市場状況"])

    market_rows = []
    for report in machine_reports:
        market = report["market"]
        market_rows.append(
            [
                report["name"],
                fmt_money(market.get("avg_rented_median")),
                fmt_money(market.get("last_rented_median")),
                fmt_money(market.get("avg_available_median")),
                fmt_money(market.get("last_available_median")),
                fmt_pct(market.get("avg_market_utilization")),
                fmt_pct(market.get("last_market_utilization")),
            ]
        )
    lines.append(
        markdown_table(
            [
                "GPU",
                "平均Rented Median",
                "最新Rented Median",
                "平均Available Median",
                "最新Available Median",
                "平均市場稼働率",
                "最新市場稼働率",
            ],
            market_rows,
        )
    )
    lines.extend(["", "## 候補価格の競合順位"])

    for report in machine_reports:
        lines.append(f"### {report['name']}")
        candidate_rows = []
        for candidate in report["candidate_rows"]:
            rank = candidate["rank"]
            total = candidate["total"]
            candidate_rows.append(
                [
                    fmt_money(candidate["candidate"], 3),
                    fmt_money(candidate["estimated_total"], 5),
                    f"{rank} / {total}" if rank is not None else "-",
                ]
            )
        lines.append(
            markdown_table(["候補GPU価格", "推定dph_total", "空きOffer内順位"], candidate_rows)
        )
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.extend(
        [
            "## メモ",
            "",
            "- 価格変更は自動実行していません。",
            "- 価格×稼働率は概算です。",
            "- GPU収益/h と 総収益/h は Vast.ai Earnings 由来です。",
            "- Reliability評価には reliability-last24h.tsv を使います。",
            "",
        ]
    )
    return "\n".join(lines)


def recommendation_payload(
    report_date: str,
    archive_path: Path,
    machine_reports: list[dict[str, Any]],
    warnings: list[str],
    auto_apply_price_change: bool,
) -> dict[str, Any]:
    machines: dict[str, Any] = {}
    for report in machine_reports:
        recommendation = report["recommendation"]
        machines[str(report["machine_id"])] = {
            "name": report["name"],
            "gpu_key": report["gpu_key"],
            "current_on_demand": report["current_on_demand"],
            "current_interruptible": report["current_interruptible"],
            "recommended_on_demand": recommendation["recommended_on_demand"],
            "action": recommendation["action"],
            "reason": recommendation["reason"],
            "occupancy_rate": report["status"]["occupancy_rate"],
            "idle_hours": report["status"]["idle_hours"],
            "reliability": report["reliability"],
            "gpu_effective_by_status": report["gpu_effective_by_status"],
            "gpu_earn_per_hour": report["gpu_earn_per_hour"],
            "total_earn_per_hour": report["total_earn_per_hour"],
            "candidate_prices": report["candidate_rows"],
        }

    return {
        "schema_version": 1,
        "date": report_date,
        "archive": str(archive_path),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "auto_apply_price_change": auto_apply_price_change,
        "warnings": warnings,
        "machines": machines,
    }


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value)
    if not text:
        return "-"
    return text.replace("|", "\\|").replace("\n", "<br>")
