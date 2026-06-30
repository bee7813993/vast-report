from __future__ import annotations

import unittest
from pathlib import Path

from vast_report.metrics import earnings_day_total_json, summarize_earnings
from vast_report.render import recommendation_payload, render_markdown


class EarningsAggregationTests(unittest.TestCase):
    def test_vast_per_day_total_earn_is_gpu_component(self) -> None:
        earnings = earnings_day_total_json(
            {
                "per_day": [
                    {
                        "total_earn": 12.08,
                        "total_stor": 1.60,
                        "total_bwu": 0.06,
                        "total_bwd": 0.04,
                    }
                ]
            }
        )

        self.assertIsNotNone(earnings)
        assert earnings is not None
        self.assertAlmostEqual(earnings["gpu_earn"], 12.08)
        self.assertAlmostEqual(earnings["storage_earn"], 1.60)
        self.assertAlmostEqual(earnings["bandwidth_earn"], 0.10)
        self.assertAlmostEqual(earnings["total_earn"], 13.78)

    def test_vast_per_day_short_component_names_are_summed(self) -> None:
        earnings = earnings_day_total_json(
            {
                "per_day": [
                    {
                        "gpu_earn": 9.90,
                        "sto_earn": 1.72,
                        "bwu_earn": 0.17,
                        "bwd_earn": 0.72,
                    }
                ]
            }
        )

        self.assertIsNotNone(earnings)
        assert earnings is not None
        self.assertAlmostEqual(earnings["gpu_earn"], 9.90)
        self.assertAlmostEqual(earnings["storage_earn"], 1.72)
        self.assertAlmostEqual(earnings["bandwidth_earn"], 0.89)
        self.assertAlmostEqual(earnings["total_earn"], 12.51)

    def test_vast_per_machine_short_component_names_are_summed(self) -> None:
        summary = summarize_earnings(
            [],
            earnings_json={
                "per_machine": [
                    {
                        "machine_id": "28351",
                        "gpu_earn": 9.90,
                        "sto_earn": 1.72,
                        "bwu_earn": 0.17,
                        "bwd_earn": 0.72,
                    }
                ]
            },
            machine_ids=["28351"],
        )

        earnings = summary["machines"]["28351"]
        self.assertAlmostEqual(earnings["gpu_earn"], 9.90)
        self.assertAlmostEqual(earnings["storage_earn"], 1.72)
        self.assertAlmostEqual(earnings["bandwidth_earn"], 0.89)
        self.assertAlmostEqual(earnings["total_earn"], 12.51)

    def test_tsv_total_earn_remains_explicit_total(self) -> None:
        summary = summarize_earnings(
            [
                {
                    "scope": "day",
                    "gpu_earn": "12.08",
                    "storage_earn": "1.60",
                    "bandwidth_up_earn": "0.06",
                    "bandwidth_down_earn": "0.04",
                    "total_earn": "13.78",
                }
            ]
        )

        self.assertAlmostEqual(summary["day"]["gpu_earn"], 12.08)
        self.assertAlmostEqual(summary["day"]["total_earn"], 13.78)

    def test_host_gpu_and_total_rates_are_rendered_separately(self) -> None:
        markdown = render_markdown(
            report_date="2026-06-29",
            archive_path=Path("vast-daily-2026-06-29.txz"),
            machine_reports=[],
            earnings_hours=24.0,
            host_gpu_earn_per_hour=12.08 / 24.0,
            host_total_earn_per_hour=13.78 / 24.0,
            warnings=[],
        )
        payload = recommendation_payload(
            report_date="2026-06-29",
            archive_path=Path("vast-daily-2026-06-29.txz"),
            machine_reports=[],
            host_gpu_earn_per_hour=12.08 / 24.0,
            host_total_earn_per_hour=13.78 / 24.0,
            warnings=[],
            auto_apply_price_change=False,
        )

        self.assertIn("ホスト全体 GPU収益/h: $0.503", markdown)
        self.assertIn("ホスト全体 総収益/h: $0.574", markdown)
        self.assertAlmostEqual(payload["host_gpu_earn_per_hour"], 12.08 / 24.0)
        self.assertAlmostEqual(payload["host_total_earn_per_hour"], 13.78 / 24.0)


if __name__ == "__main__":
    unittest.main()
