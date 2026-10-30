"""Dependency-free tests for all four required Phase 1 fault transformations."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase1_mas.artifact_store import ArtifactStore
from phase1_mas.faults import FaultInjector, FaultSpec, FaultType
from phase1_mas.trace import TraceRecorder


class FaultInjectorTests(unittest.TestCase):
    """Ensure each schedule records a category, payload, and injected version."""

    def test_all_required_fault_types_write_an_invalid_artifact_version(self) -> None:
        """Fault variants must be explicit artifacts rather than hidden prompt mutations."""

        expected_categories = {
            FaultType.INCORRECT_INTERMEDIATE_RESULT: "accidental",
            FaultType.TIMEOUT: "accidental",
            FaultType.STALE_OUTPUT: "accidental",
            FaultType.MALICIOUS_INSTRUCTION: "adversarial",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            for fault_type, category in expected_categories.items():
                trace_path = Path(tmp_dir) / f"{fault_type.value}.jsonl"
                with TraceRecorder(f"unit_{fault_type.value}", trace_path) as trace:
                    store = ArtifactStore(trace)
                    original = store.write(
                        "task_evidence",
                        {"observations": [{"result": "correct"}], "task_id": "user_task_0"},
                        producer_step_id="researcher.collect_evidence",
                    )
                    injector = FaultInjector(
                        FaultSpec(fault_id=f"fault_{fault_type.value}", fault_type=fault_type, seed=19),
                        trace,
                    )
                    injected = injector.apply_after_write(store, original)

                self.assertEqual(injected.version, 2)
                self.assertEqual(injected.parent_node_ids, (original.artifact_id,))
                self.assertEqual(injector.records[0].category, category)
                self.assertEqual(injected.content["fault_metadata"]["fault_type"], fault_type.value)
                if fault_type is FaultType.TIMEOUT:
                    self.assertEqual(injected.content["response_status"], "timeout_or_missing_response")
                if fault_type is FaultType.MALICIOUS_INSTRUCTION:
                    self.assertIn("injected_instruction", injected.content)
                if fault_type is FaultType.STALE_OUTPUT:
                    self.assertTrue(injected.content["stale_output"])
