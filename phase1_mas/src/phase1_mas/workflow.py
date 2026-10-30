"""Fixed clean multi-agent workflow and the reproducible three-task pilot."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .agentdojo_adapter import AgentDojoAdapter
from .agents import CLEAN_PILOT_TASK_IDS, DeterministicExecutor, DeterministicPlanner, DeterministicResearcher
from .artifact_store import ArtifactStore
from .models import WorkflowRunResult
from .scoring import AgentDojoScorer, CleanPilotSummary, summarize_clean_pilot
from .tool_gateway import ToolGateway
from .trace import TraceRecorder


class CleanMultiAgentWorkflow:
    """Run one fixed Researcher → Planner → Executor baseline without faults.

    The stage structure and artifact contract do not change across the three
    tasks.  Only the deterministic task handler inside each stage differs,
    which lets Milestone 3 inject a fault at the same conceptual locations.
    """

    def __init__(self, adapter: AgentDojoAdapter | None = None) -> None:
        self.adapter = adapter or AgentDojoAdapter()
        self.scorer = AgentDojoScorer(self.adapter)

    @staticmethod
    def _permissions() -> dict[str, set[str]]:
        """Return least-privilege role permissions shared by every pilot task."""

        return {
            "researcher": {"read_file", "get_scheduled_transactions", "get_most_recent_transactions"},
            "planner": set(),
            "executor": {"send_money", "update_scheduled_transaction"},
        }

    def run(self, task_id: str = "user_task_0", trace_path: Path | None = None) -> WorkflowRunResult:
        """Execute one clean task from a pristine AgentDojo environment."""

        if task_id not in CLEAN_PILOT_TASK_IDS:
            raise ValueError(f"Unsupported clean baseline task {task_id!r}; use one of {CLEAN_PILOT_TASK_IDS}")
        run_id = f"run_{uuid4().hex[:12]}"
        with TraceRecorder(run_id, trace_path) as trace:
            trace.emit("run_started", task_id=task_id, workflow="clean_multi_agent_workflow", condition="clean")
            context = self.adapter.create_context(task_id)
            store = ArtifactStore(trace)
            gateway = ToolGateway(
                runtime=context.runtime,
                environment=context.environment,
                trace=trace,
                permissions=self._permissions(),
            )
            pre_snapshot = gateway.snapshot("workflow_start")

            # The same three roles execute every task; no agent shares mutable
            # conversational context with another agent.
            evidence = DeterministicResearcher().run(task_id, context.task.PROMPT, store, gateway, trace)
            plan = DeterministicPlanner().run(store, trace)
            receipt, final_message = DeterministicExecutor().run(store, gateway, trace)

            post_snapshot = gateway.snapshot("workflow_end")
            score = self.scorer.score(
                context=context,
                final_message=final_message,
                tool_effects=tuple(gateway.effects),
                pre_state_hash=pre_snapshot.state_hash,
                post_state_hash=post_snapshot.state_hash,
                trace=trace,
            )
            trace.emit(
                "run_finished",
                task_id=task_id,
                task_passed=score.task_success,
                final_message=final_message,
                evidence_artifact_id=evidence.artifact_id,
                plan_artifact_id=plan.artifact_id,
                final_artifact_id=receipt.artifact_id,
                pre_state_hash=pre_snapshot.state_hash,
                post_state_hash=post_snapshot.state_hash,
            )
            return WorkflowRunResult(
                run_id=run_id,
                task_id=task_id,
                task_passed=score.task_success,
                final_message=final_message,
                trace_path=trace_path,
                pre_state_hash=pre_snapshot.state_hash,
                post_state_hash=post_snapshot.state_hash,
                artifacts=store.all_artifacts(),
                tool_effects=tuple(gateway.effects),
                score=score,
            )

    def run_clean_pilot(self, output_dir: Path | None = None) -> CleanPilotSummary:
        """Run the fixed three-task clean pilot, with one independent trace per task."""

        results = []
        for task_id in CLEAN_PILOT_TASK_IDS:
            trace_path = (output_dir / f"clean_{task_id}.jsonl") if output_dir is not None else None
            results.append(self.run(task_id=task_id, trace_path=trace_path))
        # All runs have a score because ``run`` scores before returning.  The
        # assertion protects future workflow edits from silently skipping it.
        scores = [result.score for result in results]
        if any(score is None for score in scores):
            raise RuntimeError("A clean pilot run finished without a final score")
        return summarize_clean_pilot(scores)


class DeterministicPaymentWorkflow(CleanMultiAgentWorkflow):
    """Backward-compatible name for the original UserTask0-only Milestone 1 demo."""

    # Inheriting ``run`` preserves the original default of ``user_task_0``
    # while allowing the demo to benefit from the stronger Milestone 2 scorer.
    pass
