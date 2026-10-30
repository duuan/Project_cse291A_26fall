"""Integration tests for the fixed three-task clean baseline pilot."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


def _agentdojo_importable() -> bool:
    """Skip, rather than fake, a benchmark run when AgentDojo deps are unavailable."""

    if importlib.util.find_spec("agentdojo") is None:
        return False
    try:
        from agentdojo.task_suite.load_suites import get_suite  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(_agentdojo_importable(), "AgentDojo dependencies are not installed")
class CleanPilotIntegrationTests(unittest.TestCase):
    """Exercise all declared clean tasks against their original AgentDojo oracles."""

    def test_three_task_clean_pilot(self) -> None:
        """All tasks must succeed with no denied tools and isolated JSONL traces."""

        from phase1_mas.trace import read_jsonl
        from phase1_mas.workflow import CleanMultiAgentWorkflow

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            summary = CleanMultiAgentWorkflow().run_clean_pilot(output_dir=output_dir)

            self.assertEqual(summary.task_count, 3)
            self.assertEqual(summary.success_count, 3)
            self.assertEqual(summary.task_success_rate, 1.0)
            self.assertEqual([score.task_id for score in summary.scores], ["user_task_0", "user_task_2", "user_task_3"])
            self.assertTrue(all(score.denied_tool_call_count == 0 for score in summary.scores))
            self.assertTrue(all(score.scorer == "agentdojo_user_task_utility" for score in summary.scores))
            self.assertTrue(all((output_dir / f"clean_{score.task_id}.jsonl").is_file() for score in summary.scores))
            # Each trace proves that Planner and Executor consumed a versioned
            # artifact instead of relying on hidden shared chat history.
            for score in summary.scores:
                events = read_jsonl(output_dir / f"clean_{score.task_id}.jsonl")
                self.assertEqual(sum(event["event_type"] == "artifact_read" for event in events), 2)
