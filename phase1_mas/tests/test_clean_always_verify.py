"""Offline accounting checks for the clean always-verify entry point."""

import unittest

from phase1_mas.clean_always_verify import _run_outcome, _summary
from phase1_mas.metrics import WorkSlice


class CleanAlwaysVerifyAccountingTests(unittest.TestCase):
    def test_safe_stop_is_not_correct_completion(self) -> None:
        events = ({"event_type": "run_finished", "termination_reason": "verifier_rejected_task_evidence"},)
        outcome = _run_outcome(events, task_success=True)
        self.assertTrue(outcome["safe_stopping"])
        self.assertTrue(outcome["task_utility_success"])
        self.assertFalse(outcome["correct_task_completion"])

    def test_clean_rejection_and_verifier_cost_are_separate(self) -> None:
        work = WorkSlice(llm_call_count=2, input_tokens_known=30, output_tokens_known=8)
        verifier_work = WorkSlice(llm_call_count=1, input_tokens_known=12, output_tokens_known=3)
        reference = {"total_work": WorkSlice(llm_call_count=1).as_dict(), "end_to_end_latency_ms": {"mean": 20.0}}
        rows = [{
            "outcome": {"task_utility_success": False, "correct_task_completion": False, "safe_stopping": True},
            "verifier_decisions": [{"logical_id": "task_evidence", "accepted": False}],
            "work_object": work,
            "verifier_work_object": verifier_work,
            "end_to_end_latency_ms": 35.0,
        }]
        summary = _summary(rows, {}, reference)
        self.assertEqual(summary["verification"]["by_handoff"]["researcher_to_planner"]["rejected"], 1)
        self.assertEqual(summary["verification"]["by_handoff"]["planner_to_executor"]["accepted"], 0)
        self.assertIsNone(summary["verification"]["by_handoff"]["planner_to_executor"]["rejection_rate_on_clean_handoffs"])
        self.assertEqual(summary["correct_task_completion_count"], 0)
        self.assertEqual(summary["verifier_only_work"]["llm_call_count"], 1)
        self.assertEqual(summary["comparison_with_clean_baseline"]["mean_end_to_end_latency_ms_delta"], 15.0)


if __name__ == "__main__":
    unittest.main()
