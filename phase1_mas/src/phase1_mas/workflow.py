"""Fixed clean multi-agent workflow and the reproducible three-task pilot."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .agentdojo_adapter import AgentDojoAdapter
from .agents import (
    DETERMINISTIC_SMOKE_TASK_IDS,
    EXECUTOR_TOOL_NAMES,
    LLM_CLEAN_PILOT_TASK_IDS,
    RESEARCHER_TOOL_NAMES,
    DeterministicExecutor,
    DeterministicPlanner,
    DeterministicResearcher,
    LLMExecutor,
    LLMPlanner,
    LLMResearcher,
)
from .artifact_store import ArtifactStore
from .faults import FaultInjector, FaultSpec, PropagationReport, analyze_propagation
from .llm_gateway import LLMGateway, OpenAICompatibleLLMGateway
from .models import Artifact, ArtifactValidity, VerificationDecision, WorkflowRunResult
from .scoring import AgentDojoScorer, CleanPilotSummary, summarize_clean_pilot
from .tool_gateway import ToolGateway
from .trace import TraceRecorder


def _contains_text(value: object, expected: str) -> bool:
    """Return whether untrusted text survived into a JSON-like artifact value."""

    if isinstance(value, str):
        return expected in value
    if isinstance(value, dict):
        return any(_contains_text(item, expected) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_text(item, expected) for item in value)
    return False


class ArtifactHandoffVerifier(Protocol):
    """Small interface used by the always-verify defense without import cycles.

    ``recovery.py`` owns the concrete LLM verifier.  The core workflow only
    knows that a verifier receives the exact artifact about to be handed to a
    downstream agent and returns an auditable accept/reject decision.
    """

    def verify(
        self,
        *,
        artifact: Artifact,
        task_id: str,
        user_prompt: str,
        store: ArtifactStore,
        llm: LLMGateway,
        trace: TraceRecorder,
    ) -> VerificationDecision:
        """Verify one artifact before its downstream consumer starts."""


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

        if task_id not in DETERMINISTIC_SMOKE_TASK_IDS:
            raise ValueError(
                f"Unsupported deterministic smoke task {task_id!r}; use one of {DETERMINISTIC_SMOKE_TASK_IDS}"
            )
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
                trace_events=tuple(trace.events),
            )

    def run_clean_pilot(self, output_dir: Path | None = None) -> CleanPilotSummary:
        """Run the fixed three-task clean pilot, with one independent trace per task."""

        results = []
        for task_id in DETERMINISTIC_SMOKE_TASK_IDS:
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


class LLMMultiAgentWorkflow(CleanMultiAgentWorkflow):
    """LLM-powered form of the same artifact-mediated three-agent workflow.

    Unlike ``CleanMultiAgentWorkflow``, this class has no task-specific Python
    planner.  The model chooses research requests, creates a plan from the
    evidence artifact, and decides whether to execute it.  The workflow shape,
    gateway, scorer, and trace format remain fixed for fair comparisons.
    """

    def __init__(self, llm: LLMGateway, adapter: AgentDojoAdapter | None = None) -> None:
        super().__init__(adapter=adapter)
        self.llm = llm

    @classmethod
    def from_env(cls, adapter: AgentDojoAdapter | None = None) -> "LLMMultiAgentWorkflow":
        """Build the production workflow from documented LLM environment settings."""

        return cls(llm=OpenAICompatibleLLMGateway.from_env(), adapter=adapter)

    @staticmethod
    def _permissions() -> dict[str, set[str]]:
        """Use role permissions as a runtime enforcement layer around LLM output."""

        return {
            "researcher": set(RESEARCHER_TOOL_NAMES),
            "planner": set(),
            "executor": set(EXECUTOR_TOOL_NAMES),
        }

    def _finalize_llm_run(
        self,
        *,
        run_id: str,
        task_id: str,
        context: object,
        store: ArtifactStore,
        gateway: ToolGateway,
        trace: TraceRecorder,
        pre_state_hash: str,
        evidence: Artifact | None,
        plan: Artifact | None,
        receipt: Artifact,
        final_message: str,
        fault_injector: FaultInjector | None,
        termination_reason: str,
    ) -> WorkflowRunResult:
        """Score and close every normal or verifier-stopped LLM run uniformly.

        Early termination is still a completed experimental result: AgentDojo
        scores its no-op final state and the trace receives ``run_finished``.
        This avoids silently dropping safe verifier rejections from success,
        latency, and cost denominators.
        """

        post_snapshot = gateway.snapshot("workflow_end")
        agentdojo_injection_task_id = None
        if fault_injector is not None and fault_injector.spec.is_agentdojo_environment_injection:
            agentdojo_injection_task_id = fault_injector.spec.malicious_variant.injection_task_id
        score = self.scorer.score(
            context=context,
            final_message=final_message,
            tool_effects=tuple(gateway.effects),
            pre_state_hash=pre_state_hash,
            post_state_hash=post_snapshot.state_hash,
            trace=trace,
            agentdojo_injection_task_id=agentdojo_injection_task_id,
        )
        trace.emit(
            "run_finished",
            task_id=task_id,
            task_passed=score.task_success,
            final_message=final_message,
            evidence_artifact_id=evidence.artifact_id if evidence is not None else None,
            plan_artifact_id=plan.artifact_id if plan is not None else None,
            final_artifact_id=receipt.artifact_id,
            pre_state_hash=pre_state_hash,
            post_state_hash=post_snapshot.state_hash,
            termination_reason=termination_reason,
        )
        return WorkflowRunResult(
            run_id=run_id,
            task_id=task_id,
            task_passed=score.task_success,
            final_message=final_message,
            trace_path=trace.output_path,
            pre_state_hash=pre_state_hash,
            post_state_hash=post_snapshot.state_hash,
            artifacts=store.all_artifacts(),
            tool_effects=tuple(gateway.effects),
            score=score,
            trace_events=tuple(trace.events),
            fault_records=tuple(fault_injector.records) if fault_injector is not None else (),
        )

    @staticmethod
    def _verifier_rejection_receipt(
        *,
        task_id: str,
        rejected_artifact: Artifact,
        decision: VerificationDecision,
        store: ArtifactStore,
        trace: TraceRecorder,
    ) -> tuple[Artifact, str]:
        """Persist a safe no-op outcome when always-verify blocks a handoff."""

        parent_nodes = [rejected_artifact.artifact_id]
        if decision.verification_artifact_id is not None:
            parent_nodes.append(decision.verification_artifact_id)
        receipt = store.write(
            "execution_receipt",
            {
                "task_id": task_id,
                "approved": False,
                "stopped_by_verifier": True,
                "rejected_artifact_id": rejected_artifact.artifact_id,
                "verification_reason": decision.reason,
                "structural_reasons": list(decision.structural_reasons),
                "tool_call_id": None,
                "result": None,
            },
            producer_step_id=decision.verifier_step_id,
            parent_node_ids=tuple(parent_nodes),
            validity=ArtifactValidity.VALID,
        )
        trace.emit(
            "verification_rejected",
            artifact_id=rejected_artifact.artifact_id,
            logical_id=rejected_artifact.logical_id,
            decision=decision,
            receipt_artifact_id=receipt.artifact_id,
        )
        return receipt, f"Execution safely stopped: verifier rejected {rejected_artifact.logical_id}."

    def run(
        self,
        task_id: str = "user_task_0",
        trace_path: Path | None = None,
        fault_spec: FaultSpec | None = None,
        verifier: ArtifactHandoffVerifier | None = None,
    ) -> WorkflowRunResult:
        """Run one Banking task with an optional fault and handoff verifier.

        A verifier is deliberately an optional *boundary guard*, not another
        planner.  It cannot call Banking tools or modify an artifact; it can
        only allow the next role to start or safely terminate the run.
        """

        run_id = f"run_{uuid4().hex[:12]}"
        with TraceRecorder(run_id, trace_path) as trace:
            trace.emit(
                "run_started",
                task_id=task_id,
                workflow="llm_multi_agent_workflow",
                condition="faulty" if fault_spec is not None else "clean",
                fault_spec=fault_spec,
            )
            # The legacy malicious fault starts only after Researcher writes
            # evidence.  In contrast, AgentDojo variants place detailed
            # adversarial text in an actual Banking environment vector before
            # Researcher begins.  Thus the trace can distinguish an untrusted
            # external-tool result from a corrupted inter-agent handoff.
            environment_injection: dict[str, object] | None = None
            context_injections: dict[str, str] | None = None
            if fault_spec is not None and fault_spec.is_agentdojo_environment_injection:
                injection_task_id = fault_spec.malicious_variant.injection_task_id
                if injection_task_id is None:  # pragma: no cover - enum contract guard
                    raise RuntimeError("AgentDojo malicious variant is missing its injection task ID")
                payload = self.adapter.build_agentdojo_injection_payload(
                    task_id=task_id,
                    injection_task_id=injection_task_id,
                )
                context_injections = {payload.injection_vector_id: payload.injected_text}
                environment_injection = {
                    "source": "agentdojo_banking_injection_task",
                    "injection_task_id": payload.injection_task_id,
                    "injection_vector_id": payload.injection_vector_id,
                    "goal": payload.goal,
                    "tool_sequence": list(payload.tool_sequence),
                    "instruction": payload.instruction,
                    "injected_text": payload.injected_text,
                }
                trace.emit(
                    "environment_fault_injected",
                    fault_spec=fault_spec,
                    environment_injection=environment_injection,
                )

            context = self.adapter.create_context(task_id, injections=context_injections)
            store = ArtifactStore(trace)
            gateway = ToolGateway(
                runtime=context.runtime,
                environment=context.environment,
                trace=trace,
                permissions=self._permissions(),
            )
            pre_snapshot = gateway.snapshot("workflow_start")
            fault_injector = FaultInjector(fault_spec, trace) if fault_spec is not None else None
            evidence = LLMResearcher().run(task_id, context.task.PROMPT, store, gateway, self.llm, trace)
            if fault_injector is not None:
                if fault_spec is not None and fault_spec.is_agentdojo_environment_injection:
                    if environment_injection is None:  # pragma: no cover - construction invariant
                        raise RuntimeError("AgentDojo environment injection metadata was not created")
                    instruction = environment_injection["instruction"]
                    assert isinstance(instruction, str)
                    source_observed = _contains_text(evidence.content, instruction)
                    trace.emit(
                        "environment_injection_observed",
                        injection_task_id=fault_spec.malicious_variant.injection_task_id,
                        injection_vector_id=environment_injection["injection_vector_id"],
                        evidence_artifact_id=evidence.artifact_id,
                        source_observed=source_observed,
                    )
                    # The evidence is not rewritten: it is the Researcher's
                    # authentic output after reading a contaminated tool
                    # result.  The record links that fact to the Planner read.
                    fault_injector.record_environment_injection(
                        evidence_artifact=evidence,
                        injection_payload=environment_injection,
                        source_observed=source_observed,
                    )
                else:
                    # Planner will read the newest version from ArtifactStore.
                    # The original remains in the trace as the clean producer
                    # output for the legacy handoff fault and accidental ones.
                    evidence = fault_injector.apply_after_write(store, evidence)
            if verifier is not None:
                decision = verifier.verify(
                    artifact=evidence,
                    task_id=task_id,
                    user_prompt=context.task.PROMPT,
                    store=store,
                    llm=self.llm,
                    trace=trace,
                )
                if not decision.accepted:
                    receipt, final_message = self._verifier_rejection_receipt(
                        task_id=task_id,
                        rejected_artifact=evidence,
                        decision=decision,
                        store=store,
                        trace=trace,
                    )
                    return self._finalize_llm_run(
                        run_id=run_id,
                        task_id=task_id,
                        context=context,
                        store=store,
                        gateway=gateway,
                        trace=trace,
                        pre_state_hash=pre_snapshot.state_hash,
                        evidence=evidence,
                        plan=None,
                        receipt=receipt,
                        final_message=final_message,
                        fault_injector=fault_injector,
                        termination_reason="verifier_rejected_task_evidence",
                    )
            plan = LLMPlanner().run(store, gateway, self.llm, trace)
            if verifier is not None:
                decision = verifier.verify(
                    artifact=plan,
                    task_id=task_id,
                    user_prompt=context.task.PROMPT,
                    store=store,
                    llm=self.llm,
                    trace=trace,
                )
                if not decision.accepted:
                    receipt, final_message = self._verifier_rejection_receipt(
                        task_id=task_id,
                        rejected_artifact=plan,
                        decision=decision,
                        store=store,
                        trace=trace,
                    )
                    return self._finalize_llm_run(
                        run_id=run_id,
                        task_id=task_id,
                        context=context,
                        store=store,
                        gateway=gateway,
                        trace=trace,
                        pre_state_hash=pre_snapshot.state_hash,
                        evidence=evidence,
                        plan=plan,
                        receipt=receipt,
                        final_message=final_message,
                        fault_injector=fault_injector,
                        termination_reason="verifier_rejected_execution_plan",
                    )
            receipt, final_message = LLMExecutor().run(store, gateway, self.llm, trace)
            return self._finalize_llm_run(
                run_id=run_id,
                task_id=task_id,
                context=context,
                store=store,
                gateway=gateway,
                trace=trace,
                pre_state_hash=pre_snapshot.state_hash,
                evidence=evidence,
                plan=plan,
                receipt=receipt,
                final_message=final_message,
                fault_injector=fault_injector,
                termination_reason="completed",
            )

    def run_clean_pilot(
        self,
        output_dir: Path | None = None,
        task_ids: tuple[str, ...] = LLM_CLEAN_PILOT_TASK_IDS,
    ) -> CleanPilotSummary:
        """Run the fixed LLM task manifest, or an explicit subset used by a test."""

        results = []
        for task_id in task_ids:
            trace_path = (output_dir / f"clean_{task_id}.jsonl") if output_dir is not None else None
            results.append(self.run(task_id=task_id, trace_path=trace_path))
        scores = [result.score for result in results]
        if any(score is None for score in scores):
            raise RuntimeError("An LLM pilot run finished without a final score")
        return summarize_clean_pilot(scores)

    def run_paired(
        self,
        *,
        task_id: str,
        fault_spec: FaultSpec,
        output_dir: Path | None = None,
    ) -> tuple[WorkflowRunResult, WorkflowRunResult, PropagationReport]:
        """Run matched clean/faulty executions and return their propagation comparison."""

        clean_trace = (output_dir / f"clean_{task_id}.jsonl") if output_dir is not None else None
        faulty_trace = (output_dir / f"fault_{fault_spec.fault_id}_{task_id}.jsonl") if output_dir is not None else None
        clean_result = self.run(task_id=task_id, trace_path=clean_trace)
        faulty_result = self.run(task_id=task_id, trace_path=faulty_trace, fault_spec=fault_spec)
        if not faulty_result.fault_records:
            raise RuntimeError(f"Fault {fault_spec.fault_id!r} did not target an artifact in task {task_id!r}")
        report = analyze_propagation(clean_result, faulty_result, faulty_result.fault_records[0])
        return clean_result, faulty_result, report
