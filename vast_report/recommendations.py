from __future__ import annotations

from typing import Any

from .metrics import safe_float


EPSILON = 1e-9


def choose_recommendation(
    machine_cfg: dict[str, Any],
    status: dict[str, Any],
    reliability: float | None,
    gpu_effective_by_status: float | None,
) -> dict[str, Any]:
    current = safe_float(status.get("on_demand"), None)
    if current is None:
        current = safe_float(machine_cfg.get("current_on_demand"), None)

    if current is None:
        return {
            "action": "unknown",
            "recommended_on_demand": None,
            "reason": "現在のOn-demand価格を判定できません。",
        }

    if status.get("records", 0) == 0:
        return {
            "action": "unknown",
            "recommended_on_demand": current,
            "reason": "machine-status-last24h.tsv に対象マシンの記録がありません。",
        }

    candidates = sorted(
        {
            candidate
            for candidate in (
                safe_float(value, None)
                for value in machine_cfg.get("candidate_on_demand", [])
            )
            if candidate is not None
        }
    )
    higher_candidates = [value for value in candidates if value > current + EPSILON]
    lower_candidates = [value for value in candidates if value < current - EPSILON]

    min_reliability = safe_float(machine_cfg.get("min_reliability_for_raise"), 1.0)
    target_occupancy = safe_float(machine_cfg.get("target_occupancy_for_raise"), 1.0)
    max_idle_hours = safe_float(machine_cfg.get("max_idle_hours_before_cut"), 999.0)
    previous_effective = safe_float(machine_cfg.get("previous_effective_gpu_dph"), None)

    occupancy_rate = status.get("occupancy_rate")
    idle_hours = status.get("idle_hours")

    if (
        higher_candidates
        and occupancy_rate is not None
        and reliability is not None
        and occupancy_rate >= target_occupancy
        and reliability >= min_reliability
    ):
        return {
            "action": "consider_raise",
            "recommended_on_demand": higher_candidates[0],
            "reason": (
                f"稼働率 {occupancy_rate * 100:.1f}%、Reliability "
                f"{reliability:.4f} が値上げ検討条件を満たしています。"
            ),
        }

    if (
        lower_candidates
        and idle_hours is not None
        and max_idle_hours is not None
        and previous_effective is not None
        and gpu_effective_by_status is not None
        and idle_hours >= max_idle_hours
        and gpu_effective_by_status < previous_effective
    ):
        return {
            "action": "watch_lower",
            "recommended_on_demand": lower_candidates[-1],
            "reason": (
                f"空き時間 {idle_hours:.1f}h かつ 価格×稼働率 "
                f"{gpu_effective_by_status:.3f} が基準 "
                f"{previous_effective:.3f} を下回っています。初版では即時値下げ"
                "ではなく、次回も同傾向なら検討します。"
            ),
        }

    if reliability is None:
        reason = "Reliabilityが欠損しているため値上げ判定は保留し、現状維持します。"
    else:
        reason = "値上げ・値下げ注意の明確な条件には達していません。"
    return {
        "action": "hold",
        "recommended_on_demand": current,
        "reason": reason,
    }
