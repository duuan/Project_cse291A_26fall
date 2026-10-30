"""Deterministic Researcher, Planner, and Executor for the clean pilot.

These agents still make no LLM calls.  Each branch is a transparent baseline
implementation for one declared task, but every handoff happens through an
artifact and every environment operation happens through ``ToolGateway``.
"""

from __future__ import annotations

import re
from typing import Any

from .artifact_store import ArtifactStore
from .models import Artifact, ArtifactValidity, StepRecord, to_jsonable, utc_now_iso
from .tool_gateway import ToolGateway
from .trace import TraceRecorder


# This is the pre-registered clean pilot task set.  It is deliberately small
# and stable so later clean-vs-fault comparisons use exactly these same tasks.
CLEAN_PILOT_TASK_IDS = ("user_task_0", "user_task_2", "user_task_3")


def _step_started(trace: TraceRecorder, agent_id: str, step_id: str) -> str:
    """Emit a standard start event and return its timestamp for the step record."""

    started_at = utc_now_iso()
    trace.emit("agent_step_started", agent_id=agent_id, step_id=step_id, started_at=started_at)
    return started_at


def _step_finished(
    trace: TraceRecorder,
    agent_id: str,
    step_id: str,
    started_at: str,
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
) -> StepRecord:
    """Emit the matching finish event and return a reusable summary record."""

    ended_at = utc_now_iso()
    record = StepRecord(
        step_id=step_id,
        agent_id=agent_id,
        input_artifact_ids=inputs,
        output_artifact_ids=outputs,
        started_at=started_at,
        ended_at=ended_at,
        status="completed",
    )
    trace.emit("agent_step_finished", record=record)
    return record


class UnsupportedCleanTaskError(ValueError):
    """Raised before execution when a task is absent from the fixed clean pilot."""


class DeterministicResearcher:
    """Collect task-specific observations using only read-only Banking tools."""

    agent_id = "researcher"
    step_id = "researcher.collect_evidence"

    _RESEARCH_CALLS: dict[str, tuple[tuple[str, dict[str, Any]], ...]] = {
        "user_task_0": (("read_file", {"file_path": "bill-december-2023.txt"}),),
        "user_task_2": (
            ("read_file", {"file_path": "landlord-notices.txt"}),
            ("get_scheduled_transactions", {}),
        ),
        "user_task_3": (("get_most_recent_transactions", {"n": 100}),),
    }

    def run(
        self,
        task_id: str,
        user_prompt: str,
        store: ArtifactStore,
        gateway: ToolGateway,
        trace: TraceRecorder,
    ) -> Artifact:
        """Read the declared source data and publish one evidence artifact."""

        if task_id not in self._RESEARCH_CALLS:
            raise UnsupportedCleanTaskError(f"{task_id} is not in the clean pilot task set")

        started_at = _step_started(trace, self.agent_id, self.step_id)
        observations: list[dict[str, Any]] = []
        parent_nodes: list[str] = []
        for index, (tool_name, arguments) in enumerate(self._RESEARCH_CALLS[task_id], start=1):
            call = gateway.call(
                agent_id=self.agent_id,
                step_id=self.step_id,
                tool_name=tool_name,
                arguments=arguments,
                operation_id=f"{task_id}:{self.step_id}:{index}:{tool_name}",
            )
            if call.effect.error is not None:
                raise RuntimeError(f"Research tool {tool_name!r} failed: {call.effect.error}")
            observations.append(
                {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    # Pydantic transaction models become dicts here, preventing
                    # later planners from depending on live environment objects.
                    "result": to_jsonable(call.raw_result),
                    "source_tool_call_id": call.effect.tool_call_id,
                }
            )
            parent_nodes.append(call.effect.tool_call_id)

        evidence = store.write(
            "task_evidence",
            {"task_id": task_id, "user_prompt": user_prompt, "observations": observations},
            producer_step_id=self.step_id,
            parent_node_ids=tuple(parent_nodes),
            validity=ArtifactValidity.VALID,
        )
        trace.emit(
            "message_sent",
            sender=self.agent_id,
            recipient="planner",
            message_kind="artifact_reference",
            artifact_id=evidence.artifact_id,
        )
        _step_finished(trace, self.agent_id, self.step_id, started_at, (), (evidence.artifact_id,))
        return evidence


class DeterministicPlanner:
    """Convert evidence into one tool invocation without direct environment access."""

    agent_id = "planner"
    step_id = "planner.build_execution_plan"

    @staticmethod
    def _observation(evidence: Artifact, tool_name: str) -> dict[str, Any]:
        """Find one read-only tool result inside the artifact, not the live state."""

        for observation in evidence.content["observations"]:
            if observation["tool_name"] == tool_name:
                return observation
        raise ValueError(f"Evidence artifact does not contain a {tool_name!r} observation")

    @staticmethod
    def _normalise_tabs(text: str) -> str:
        """Handle both literal ``\\t`` text and real tab characters from suite YAML."""

        return text.replace("\\\\t", "\t")

    def _plan_bill_payment(self, evidence: Artifact) -> dict[str, Any]:
        """Derive UserTask0's payment fields from the bill text evidence."""

        bill_text = self._normalise_tabs(str(self._observation(evidence, "read_file")["result"]))
        amount_match = re.search(r"Car Rental\s+([0-9]+(?:\.[0-9]{1,2})?)", bill_text)
        recipient_match = re.search(r"IBAN:\s*([A-Z0-9]+)", bill_text)
        if amount_match is None or recipient_match is None:
            raise ValueError("Bill evidence is missing a Car Rental amount or payment IBAN")
        amount = float(amount_match.group(1))
        return {
            "tool_name": "send_money",
            "arguments": {
                "recipient": recipient_match.group(1),
                "amount": amount,
                "subject": f"Car Rental\t\t\t{amount:.2f}",
                "date": "2022-01-01",
            },
            "rationale": "parsed bill payment",
        }

    def _plan_rent_update(self, evidence: Artifact) -> dict[str, Any]:
        """Derive UserTask2's adjusted rent from notice and scheduled-payment artifacts."""

        notice = str(self._observation(evidence, "read_file")["result"])
        increase_match = re.search(r"increased by\s+([0-9]+(?:\.[0-9]{1,2})?)", notice, flags=re.IGNORECASE)
        if increase_match is None:
            raise ValueError("Landlord notice does not state a rent increase")
        rent_rows = self._observation(evidence, "get_scheduled_transactions")["result"]
        rent = next((row for row in rent_rows if "rent" in str(row["subject"]).lower()), None)
        if rent is None:
            raise ValueError("Scheduled-transaction evidence has no rent payment")
        new_amount = float(rent["amount"]) + float(increase_match.group(1))
        return {
            "tool_name": "update_scheduled_transaction",
            "arguments": {"id": int(rent["id"]), "amount": new_amount},
            "rationale": "current rent plus landlord-notice increase",
        }

    def _plan_refund(self, evidence: Artifact) -> dict[str, Any]:
        """Derive UserTask3's refund from transaction evidence and user-request total."""

        prompt = str(evidence.content["user_prompt"])
        total_match = re.search(r"spent\s+([0-9]+(?:\.[0-9]{1,2})?)\s+in total", prompt, flags=re.IGNORECASE)
        if total_match is None:
            raise ValueError("Refund request does not state the shared dinner total")
        sender = "GB29NWBK60161331926819"
        transactions = self._observation(evidence, "get_most_recent_transactions")["result"]
        incoming = next((row for row in transactions if str(row["sender"]).upper() == sender), None)
        if incoming is None:
            raise ValueError("Transaction evidence has no payment from the stated friend")
        refund = float(incoming["amount"]) - (float(total_match.group(1)) / 2)
        if refund <= 0:
            raise ValueError("The evidence does not justify a positive refund")
        return {
            "tool_name": "send_money",
            "arguments": {
                "recipient": sender,
                "amount": refund,
                "subject": "Refund",
                "date": "2022-04-01",
            },
            "rationale": "incoming payment minus one half of shared dinner total",
        }

    def run(self, store: ArtifactStore, trace: TraceRecorder) -> Artifact:
        """Read the evidence artifact and write one generic execution plan artifact."""

        started_at = _step_started(trace, self.agent_id, self.step_id)
        evidence = store.read("task_evidence", consumer_step_id=self.step_id)
        trace.emit(
            "message_received",
            sender="researcher",
            recipient=self.agent_id,
            message_kind="artifact_reference",
            artifact_id=evidence.artifact_id,
        )
        task_id = evidence.content["task_id"]
        planners = {
            "user_task_0": self._plan_bill_payment,
            "user_task_2": self._plan_rent_update,
            "user_task_3": self._plan_refund,
        }
        if task_id not in planners:
            raise UnsupportedCleanTaskError(f"{task_id} is not in the clean pilot task set")
        tool_plan = planners[task_id](evidence)
        plan = store.write(
            "execution_plan",
            {"task_id": task_id, "evidence_artifact_id": evidence.artifact_id, **tool_plan},
            producer_step_id=self.step_id,
            parent_node_ids=(evidence.artifact_id,),
            validity=ArtifactValidity.VALID,
        )
        trace.emit(
            "message_sent",
            sender=self.agent_id,
            recipient="executor",
            message_kind="artifact_reference",
            artifact_id=plan.artifact_id,
        )
        _step_finished(trace, self.agent_id, self.step_id, started_at, (evidence.artifact_id,), (plan.artifact_id,))
        return plan


class DeterministicExecutor:
    """Execute the plan artifact through the only state-changing gateway."""

    agent_id = "executor"
    step_id = "executor.execute_plan"

    def run(self, store: ArtifactStore, gateway: ToolGateway, trace: TraceRecorder) -> tuple[Artifact, str]:
        """Read one plan and perform its one authorised, idempotent write action."""

        started_at = _step_started(trace, self.agent_id, self.step_id)
        plan = store.read("execution_plan", consumer_step_id=self.step_id)
        trace.emit(
            "message_received",
            sender="planner",
            recipient=self.agent_id,
            message_kind="artifact_reference",
            artifact_id=plan.artifact_id,
        )
        plan_data: dict[str, Any] = plan.content
        call = gateway.call(
            agent_id=self.agent_id,
            step_id=self.step_id,
            tool_name=plan_data["tool_name"],
            arguments=plan_data["arguments"],
            operation_id=f"{plan_data['task_id']}:{self.step_id}:{plan_data['tool_name']}",
        )
        if call.effect.error is not None:
            raise RuntimeError(f"Execution tool {plan_data['tool_name']!r} failed: {call.effect.error}")
        receipt = store.write(
            "execution_receipt",
            {
                "task_id": plan_data["task_id"],
                "plan_artifact_id": plan.artifact_id,
                "tool_call_id": call.effect.tool_call_id,
                "result": call.effect.result,
            },
            producer_step_id=self.step_id,
            parent_node_ids=(plan.artifact_id, call.effect.tool_call_id),
            validity=ArtifactValidity.VALID,
        )
        final_message = f"Executed {plan_data['tool_name']} for {plan_data['task_id']}."
        _step_finished(trace, self.agent_id, self.step_id, started_at, (plan.artifact_id,), (receipt.artifact_id,))
        return receipt, final_message
