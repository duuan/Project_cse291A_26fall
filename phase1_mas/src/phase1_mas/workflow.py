"""The deterministic Researcher → Planner → Executor Milestone 1 workflow."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .agentdojo_adapter import AgentDojoAdapter
from .agents import DeterministicBillPlanner, DeterministicBillResearcher, DeterministicPaymentExecutor
from .artifact_store import ArtifactStore
from .models import WorkflowRunResult
from .tool_gateway import ToolGateway
from .trace import TraceRecorder


class DeterministicPaymentWorkflow:
    """Run AgentDojo Banking ``user_task_0`` without an LLM.

    This narrow workflow is deliberately fixed.  It establishes an auditable
    baseline first; Phase 2 can extend it with dynamic recovery policies without
    changing the artifact, tool, or trace contracts defined here.
    """

    def __init__(self, adapter: AgentDojoAdapter | None = None) -> None:
        self.adapter = adapter or AgentDojoAdapter()

    def run(self, task_id: str = "user_task_0", trace_path: Path | None = None) -> WorkflowRunResult:
        """Execute a clean task and return the benchmark verdict plus its evidence."""

        run_id = f"run_{uuid4().hex[:12]}"
        with TraceRecorder(run_id, trace_path) as trace:
            trace.emit("run_started", task_id=task_id, workflow="deterministic_payment_workflow")
            context = self.adapter.create_context(task_id)
            store = ArtifactStore(trace)
            gateway = ToolGateway(
                runtime=context.runtime,
                environment=context.environment,
                trace=trace,
                # Least privilege makes an unauthorised action measurable even
                # in this simple clean-run skeleton.
                permissions={"researcher": {"read_file"}, "planner": set(), "executor": {"send_money"}},
            )
            pre_snapshot = gateway.snapshot("workflow_start")

            researcher = DeterministicBillResearcher()
            planner = DeterministicBillPlanner()
            executor = DeterministicPaymentExecutor()
            researcher.run(task_id, context.task.PROMPT, store, gateway, trace)
            planner.run(store, trace)
            receipt, final_message = executor.run(store, gateway, trace)

            post_snapshot = gateway.snapshot("workflow_end")
            task_passed = self.adapter.evaluate(context, final_message, tuple(gateway.effects))
            trace.emit(
                "run_finished",
                task_id=task_id,
                task_passed=task_passed,
                final_message=final_message,
                final_artifact_id=receipt.artifact_id,
                pre_state_hash=pre_snapshot.state_hash,
                post_state_hash=post_snapshot.state_hash,
            )
            return WorkflowRunResult(
                run_id=run_id,
                task_id=task_id,
                task_passed=task_passed,
                final_message=final_message,
                trace_path=trace_path,
                pre_state_hash=pre_snapshot.state_hash,
                post_state_hash=post_snapshot.state_hash,
                artifacts=store.all_artifacts(),
                tool_effects=tuple(gateway.effects),
            )
