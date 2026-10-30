"""Dependency-free tests for post-hoc Phase 1 metric aggregation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phase1_mas.metrics import aggregate_fault_experiment, write_metric_outputs


def _event(event_type: str, timestamp: str, **payload: object) -> dict[str, object]:
    """Make a compact synthetic JSONL event with the normal trace fields."""

    return {"event_type": event_type, "timestamp": timestamp, "run_id": "run_test", **payload}


def _write_jsonl(path: Path, events: list[dict[str, object]]) -> None:
    """Write a trace fixture exactly as TraceRecorder would (one JSON object/line)."""

    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


class MetricsTests(unittest.TestCase):
    """Verify success, propagation, denied, wasted-work, and latency aggregates."""

    def test_aggregate_paired_fault_metrics(self) -> None:
        """A harmful injected artifact should produce all report-required measurements."""

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            pair_dir = root / "user_task_0" / "incorrect_intermediate_result"
            pair_dir.mkdir(parents=True)
            clean_events = [
                _event(
                    "run_started",
                    "2026-01-01T00:00:00+00:00",
                    task_id="user_task_0",
                    condition="clean",
                ),
                _event(
                    "llm_call_finished",
                    "2026-01-01T00:00:00.010000+00:00",
                    record={
                        "step_id": "researcher.collect_evidence",
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "estimated_cost_usd": 0.01,
                        "latency_ms": 10.0,
                    },
                ),
                _event(
                    "agent_step_finished",
                    "2026-01-01T00:00:00.020000+00:00",
                    record={"step_id": "researcher.collect_evidence"},
                ),
                _event(
                    "tool_call_finished",
                    "2026-01-01T00:00:00.030000+00:00",
                    effect={"step_id": "researcher.collect_evidence"},
                ),
                _event(
                    "run_finished",
                    "2026-01-01T00:00:00.100000+00:00",
                    task_passed=True,
                ),
            ]
            faulty_events = [
                _event(
                    "run_started",
                    "2026-01-01T00:00:01+00:00",
                    task_id="user_task_0",
                    condition="faulty",
                ),
                _event(
                    "llm_call_finished",
                    "2026-01-01T00:00:01.010000+00:00",
                    record={
                        "step_id": "researcher.collect_evidence",
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "estimated_cost_usd": 0.01,
                        "latency_ms": 10.0,
                    },
                ),
                _event(
                    "tool_call_finished",
                    "2026-01-01T00:00:01.015000+00:00",
                    effect={"step_id": "researcher.collect_evidence"},
                ),
                _event(
                    "agent_step_finished",
                    "2026-01-01T00:00:01.020000+00:00",
                    record={"step_id": "researcher.collect_evidence"},
                ),
                _event(
                    "artifact_written",
                    "2026-01-01T00:00:01.021000+00:00",
                    artifact={
                        "artifact_id": "task_evidence:v1",
                        "producer_step_id": "researcher.collect_evidence",
                    },
                ),
                _event(
                    "fault_injected",
                    "2026-01-01T00:00:01.030000+00:00",
                    fault_spec={"seed": 42},
                    record={"original_artifact_id": "task_evidence:v1"},
                ),
                _event(
                    "llm_call_finished",
                    "2026-01-01T00:00:01.040000+00:00",
                    record={
                        "step_id": "planner.build_execution_plan",
                        "input_tokens": 20,
                        "output_tokens": 3,
                        "estimated_cost_usd": 0.02,
                        "latency_ms": 10.0,
                    },
                ),
                _event(
                    "agent_step_finished",
                    "2026-01-01T00:00:01.045000+00:00",
                    record={"step_id": "planner.build_execution_plan"},
                ),
                _event(
                    "llm_call_finished",
                    "2026-01-01T00:00:01.050000+00:00",
                    record={
                        "step_id": "executor.execute_plan",
                        "input_tokens": 30,
                        "output_tokens": 4,
                        "estimated_cost_usd": 0.03,
                        "latency_ms": 10.0,
                    },
                ),
                _event(
                    "tool_call_denied",
                    "2026-01-01T00:00:01.052000+00:00",
                    step_id="executor.execute_plan",
                ),
                _event(
                    "agent_step_finished",
                    "2026-01-01T00:00:01.060000+00:00",
                    record={"step_id": "executor.execute_plan"},
                ),
                _event(
                    "run_finished",
                    "2026-01-01T00:00:01.080000+00:00",
                    task_passed=False,
                ),
            ]
            _write_jsonl(pair_dir / "clean_user_task_0.jsonl", clean_events)
            _write_jsonl(
                pair_dir / "fault_incorrect_intermediate_result_seed_42_user_task_0.jsonl",
                faulty_events,
            )
            report = {
                "task_id": "user_task_0",
                "fault_id": "incorrect_intermediate_result_seed_42",
                "fault_type": "incorrect_intermediate_result",
                "category": "accidental",
                "exposure": True,
                "behavioral_propagation": True,
                "harmful_propagation": True,
                "clean_task_success": True,
                "faulty_task_success": False,
                "affected_stages": ["planner.build_execution_plan", "executor.execute_plan"],
            }
            (pair_dir / "propagation_report.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            (root / "fault_pilot_summary.json").write_text(
                json.dumps({"reports": [{"propagation_report": report}]}), encoding="utf-8"
            )

            rows, summary = aggregate_fault_experiment(root)
            paths = write_metric_outputs(rows=rows, summary=summary, output_dir=root / "metrics")
            output_files_exist = paths["metrics_summary"].is_file() and paths["experiment_rows"].is_file()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seed"], 42)
        self.assertEqual(rows[0]["clean"]["end_to_end_latency_ms"], 100.0)
        self.assertEqual(rows[0]["faulty"]["end_to_end_latency_ms"], 80.0)
        self.assertEqual(rows[0]["wasted_work"]["total"]["llm_call_count"], 3)
        self.assertEqual(rows[0]["wasted_work"]["total"]["tool_call_count"], 1)
        self.assertEqual(rows[0]["wasted_work"]["total"]["denied_tool_call_count"], 1)
        overall = summary["overall"]
        self.assertEqual(overall["clean"]["task_success_rate"], 1.0)
        self.assertEqual(overall["faulty"]["task_success_rate"], 0.0)
        self.assertEqual(overall["faulty"]["unauthorized_actions"]["denied_tool_call_count"], 1)
        self.assertEqual(overall["failure_propagation"]["harmful_propagation_count"], 1)
        self.assertEqual(overall["affected_stages"]["stage_occurrences"]["executor.execute_plan"], 1)
        self.assertTrue(output_files_exist)


if __name__ == "__main__":
    unittest.main()
