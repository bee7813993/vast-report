from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vast_report.collect_machine_earnings import (
    build_machine_earnings_payload,
    parse_json_stdout,
    write_json_atomic,
)


class MachineEarningsCollectionTests(unittest.TestCase):
    def test_payload_keeps_matching_per_machine_and_drops_raw_fields(self) -> None:
        payload = build_machine_earnings_payload(
            {
                "start_utc": "2026-07-01T00:00:00Z",
                "end_utc": "2026-07-02T00:00:00Z",
                "per_day": [{"gpu_earn": 99.0, "customer": "raw-host-scope"}],
                "per_machine": [
                    {
                        "machine_id": "28351",
                        "gpu_earn": 9.90,
                        "sto_earn": 1.72,
                        "bwu_earn": 0.17,
                        "bwd_earn": 0.72,
                        "reliability": 0.99,
                        "customer": "raw-machine-scope",
                    },
                    {
                        "machine_id": "35058",
                        "gpu_earn": 5.0,
                    },
                ],
            },
            "28351",
            collected_at_utc="2026-07-02T00:05:00Z",
        )

        self.assertNotIn("per_day", payload)
        self.assertEqual(payload["machine_id"], "28351")
        self.assertEqual(payload["start_utc"], "2026-07-01T00:00:00Z")
        rows = payload["per_machine"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["machine_id"], "28351")
        self.assertEqual(rows[0]["gpu_earn"], 9.90)
        self.assertEqual(rows[0]["sto_earn"], 1.72)
        self.assertEqual(rows[0]["bwu_earn"], 0.17)
        self.assertEqual(rows[0]["bwd_earn"], 0.72)
        self.assertNotIn("customer", rows[0])
        self.assertNotIn("reliability", rows[0])

    def test_payload_prefers_actual_per_machine_row_over_host_per_day(self) -> None:
        payload = build_machine_earnings_payload(
            {
                "per_day": [
                    {
                        "day": 20635,
                        "gpu_earn": 12.41,
                        "sto_earn": 1.47,
                        "bwu_earn": 0.09,
                        "bwd_earn": 0.01,
                        "reliability": 0.995818,
                    }
                ],
                "per_machine": [
                    {
                        "machine_id": 35058,
                        "gpu_earn": 3.35,
                        "sto_earn": 0.16,
                        "bwu_earn": 0.09,
                        "bwd_earn": 0.01,
                        "reliability": 1.0,
                    }
                ],
                "summary": {
                    "avg_reliability": 0.995739,
                    "total_bwd": 0.01,
                    "total_bwu": 0.09,
                    "total_gpu": 3.35,
                    "total_sla": 0.0,
                    "total_stor": 0.16,
                },
            },
            "35058",
            collected_at_utc="2026-07-02T00:05:00Z",
        )

        self.assertNotIn("per_day", payload)
        self.assertNotIn("summary", payload)
        rows = payload["per_machine"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["machine_id"], "35058")
        self.assertEqual(rows[0]["gpu_earn"], 3.35)
        self.assertEqual(rows[0]["sto_earn"], 0.16)
        self.assertEqual(rows[0]["bwu_earn"], 0.09)
        self.assertEqual(rows[0]["bwd_earn"], 0.01)
        self.assertNotIn("reliability", rows[0])

    def test_payload_uses_summary_when_per_machine_is_missing(self) -> None:
        payload = build_machine_earnings_payload(
            {
                "summary": {
                    "total_gpu": 9.90,
                    "total_stor": 1.72,
                    "total_bwu": 0.17,
                    "total_bwd": 0.72,
                    "unrelated": "drop-me",
                }
            },
            "28351",
            collected_at_utc="2026-07-02T00:05:00Z",
        )

        self.assertNotIn("per_machine", payload)
        self.assertEqual(payload["summary"]["total_gpu"], 9.90)
        self.assertEqual(payload["summary"]["total_stor"], 1.72)
        self.assertEqual(payload["summary"]["total_bwu"], 0.17)
        self.assertEqual(payload["summary"]["total_bwd"], 0.72)
        self.assertNotIn("unrelated", payload["summary"])

    def test_parse_json_stdout_allows_wrapped_json(self) -> None:
        parsed = parse_json_stdout('notice\n{"summary": {"total_gpu": 1.0}}\n')

        self.assertEqual(parsed["summary"]["total_gpu"], 1.0)

    def test_write_json_atomic_writes_final_file_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "earnings-last24h-machine-28351.json"
            write_json_atomic(path, {"schema_version": 1, "machine_id": "28351"})

            self.assertTrue(path.exists())
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["machine_id"], "28351")
            self.assertEqual(list(Path(temp_name).glob("*.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
