"""Tests proving the Milestone 1 contracts before LLMs or faults are added."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from phase1_mas.artifact_store import ArtifactStore
from phase1_mas.trace import TraceRecorder, read_jsonl


class ArtifactAndTraceTests(unittest.TestCase):
    """Dependency-free checks that can run without AgentDojo installed."""

    def test_artifact_versions_reads_and_jsonl_events(self) -> None:
        """A read must identify the exact version that a later step consumed."""

        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_path = Path(tmp_dir) / "trace.jsonl"
            with TraceRecorder("unit_run", trace_path) as trace:
                store = ArtifactStore(trace)
                first = store.write("evidence", {"amount": 98.70}, "researcher.step")
                second = store.write(
                    "plan",
                    {"uses": first.artifact_id},
                    "planner.step",
                    parent_node_ids=(first.artifact_id,),
                )
                read_back = store.read("plan", "executor.step")

            self.assertEqual(read_back.artifact_id, second.artifact_id)
            self.assertEqual(store.consumers_of(second.artifact_id), ("executor.step",))
            self.assertEqual(store.dependency_edges(), [{"from": first.artifact_id, "to": second.artifact_id}])
            event_types = [event["event_type"] for event in read_jsonl(trace_path)]
            self.assertEqual(event_types, ["artifact_written", "artifact_written", "artifact_read"])


def _agentdojo_importable() -> bool:
    """Avoid a misleading integration failure when optional benchmark deps are absent."""

    if importlib.util.find_spec("agentdojo") is None:
        return False
    try:
        from agentdojo.task_suite.load_suites import get_suite  # noqa: F401
    # AgentDojo imports optional-looking packages while registering all suites;
    # any missing runtime dependency means the integration test should skip.
    except ImportError:
        return False
    return True


@unittest.skipUnless(_agentdojo_importable(), "AgentDojo dependencies are not installed")
class BankingIntegrationTests(unittest.TestCase):
    """Use the real AgentDojo Banking tool implementations and task oracle."""

    def test_user_task_0_reads_artifacts_calls_tools_and_mutates_state(self) -> None:
        """The clean skeleton must produce all evidence required by the milestone."""

        # Imported here so the pure artifact/trace test remains dependency-free.
        from phase1_mas.workflow import DeterministicPaymentWorkflow

        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_path = Path(tmp_dir) / "banking_trace.jsonl"
            result = DeterministicPaymentWorkflow().run(trace_path=trace_path)
            events = read_jsonl(trace_path)

        self.assertTrue(result.task_passed)
        self.assertNotEqual(result.pre_state_hash, result.post_state_hash)
        self.assertEqual([effect.tool_name for effect in result.tool_effects], ["read_file", "send_money"])
        self.assertTrue(result.tool_effects[1].state_changed)
        self.assertEqual([artifact.logical_id for artifact in result.artifacts], ["task_evidence", "execution_plan", "execution_receipt"])
        self.assertIn("artifact_read", [event["event_type"] for event in events])
        self.assertIn("tool_call_finished", [event["event_type"] for event in events])
        self.assertIn("message_sent", [event["event_type"] for event in events])
        self.assertIn("state_snapshot", [event["event_type"] for event in events])
