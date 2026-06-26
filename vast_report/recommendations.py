from __future__ import annotations

from typing import Any

from .metrics import safe_float


EPSILON = 1e-9


def choose_recommendation(
    machine_cfg: dict[str, Any],
    status: dict[str, Any],
    reliability: float | None,
    gpu_effective_by_status: float | None,
    candidate_rows: list[dict[str, Any]] | None = None,
    active_contract_price_estimate: float | None = None,
    active_contract_price_source: str | None = None,
    current_listed_on_demand: float | None = None,
) -> dict[str, Any]:
    listed_current = safe_float(current_listed_on_demand, None)
    if listed_current is None:
        listed_current = safe_float(status.get("on_demand"), None)
    if listed_current is None:
        listed_current = safe_float(machine_cfg.get("current_on_demand"), None)

    contract_current = safe_float(active_contract_price_estimate, None)
    current = contract_current if contract_current is not None else listed_current

    if current is None:
        return {
            "action": "unknown",
            "recommended_on_demand": None,
            "reason": "現在のOn-demand価格を判定できません。",
        }

    if status.get("records", 0) == 0:
        return {
            "action": "unknown",
            "recommended_on_demand": listed_current,
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
        target = higher_candidates[0]
        hold = _hold_if_listed_already_covers_target(
            target=target,
            listed_current=listed_current,
            contract_current=contract_current,
            active_contract_price_source=active_contract_price_source,
        )
        if hold is not None:
            return hold
        return {
            "action": "consider_raise",
            "recommended_on_demand": target,
            "reason": (
                f"稼働率 {occupancy_rate * 100:.1f}%、Reliability "
                f"{reliability:.4f} が値上げ検討条件を満たしています。"
                f"判定には契約価格推定 {current:.3f} を使っています。"
            ),
        }

    soft_raise = _soft_raise_candidate(
        current=current,
        candidate_rows=candidate_rows or [],
    )
    if (
        soft_raise is not None
        and occupancy_rate is not None
        and idle_hours is not None
        and reliability is not None
        and min_reliability is not None
        and occupancy_rate >= 1.0 - EPSILON
        and idle_hours <= EPSILON
        and reliability >= min_reliability - 0.005
    ):
        hold = _hold_if_listed_already_covers_target(
            target=soft_raise["candidate"],
            listed_current=listed_current,
            contract_current=contract_current,
            active_contract_price_source=active_contract_price_source,
        )
        if hold is not None:
            return hold
        return {
            "action": "consider_raise_soft",
            "recommended_on_demand": soft_raise["candidate"],
            "reason": (
                f"稼働率 100.0%、空き時間 0.0h で、Reliability "
                f"{reliability:.4f} が通常閾値 {min_reliability:.4f} の"
                "近傍です。候補価格を上げても空きOffer内順位が "
                f"{soft_raise['current_rank']} / {soft_raise['total']} から "
                f"{soft_raise['candidate_rank']} / {soft_raise['total']} に"
                "悪化しないため、弱い値上げ候補として提案します。"
                f"判定には契約価格推定 {current:.3f} を使っています。"
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
        "recommended_on_demand": listed_current if listed_current is not None else current,
        "reason": reason,
    }


def _hold_if_listed_already_covers_target(
    target: float,
    listed_current: float | None,
    contract_current: float | None,
    active_contract_price_source: str | None,
) -> dict[str, Any] | None:
    if listed_current is None or contract_current is None:
        return None
    if abs(listed_current - contract_current) <= EPSILON:
        return None
    if target > listed_current + EPSILON:
        return None

    source_note = (
        f"（source: {active_contract_price_source}）"
        if active_contract_price_source
        else ""
    )
    return {
        "action": "hold",
        "recommended_on_demand": listed_current,
        "reason": (
            f"契約価格推定 {contract_current:.3f} {source_note} を基準にすると"
            f"次の候補価格は {target:.3f} ですが、現在Listed価格 "
            f"{listed_current:.3f} がすでにその水準以上です。"
            "Listed価格での新規成約がまだ確認できていないため、"
            "追加の値上げは保留します。"
        ),
    }


def _soft_raise_candidate(
    current: float, candidate_rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    sorted_rows = sorted(
        (
            row
            for row in candidate_rows
            if safe_float(row.get("candidate"), None) is not None
        ),
        key=lambda row: safe_float(row.get("candidate"), 0.0) or 0.0,
    )
    current_index = None
    for index, row in enumerate(sorted_rows):
        candidate = safe_float(row.get("candidate"), None)
        if candidate is not None and abs(candidate - current) <= EPSILON:
            current_index = index
            break
    if current_index is None:
        return None

    current_rank = sorted_rows[current_index].get("rank")
    total = sorted_rows[current_index].get("total")
    if current_rank is None or total is None:
        return None

    for row in sorted_rows[current_index + 1 : current_index + 3]:
        candidate_rank = row.get("rank")
        candidate = safe_float(row.get("candidate"), None)
        if candidate_rank is None or candidate is None:
            continue
        if candidate_rank <= current_rank:
            return {
                "candidate": candidate,
                "current_rank": current_rank,
                "candidate_rank": candidate_rank,
                "total": row.get("total", total),
            }
    return None
