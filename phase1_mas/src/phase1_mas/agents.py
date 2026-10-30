"""Deterministic stand-ins for the three agents used in Milestone 1.

They are intentionally not LLM calls.  Their job is to prove the workflow
contracts—artifact handoffs, tool permissions, state effects, and traces—before
model variability and fault injection are introduced in later milestones.
"""

from __future__ import annotations

import re
from typing import Any

from .artifact_store import ArtifactStore
from .models import Artifact, ArtifactValidity, StepRecord, utc_now_iso
from .tool_gateway import ToolGateway
from .trace import TraceRecorder


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


class DeterministicBillResearcher:
    """Read the requested bill and expose it as a provenance-carrying artifact."""

    agent_id = "researcher"
    step_id = "researcher.collect_bill"

    def run(self, task_id: str, user_prompt: str, store: ArtifactStore, gateway: ToolGateway, trace: TraceRecorder) -> Artifact:
        """Read UserTask0's bill through the gateway, rather than accessing state directly."""

        started_at = _step_started(trace, self.agent_id, self.step_id)
        call = gateway.call(
            agent_id=self.agent_id,
            step_id=self.step_id,
            tool_name="read_file",
            arguments={"file_path": "bill-december-2023.txt"},
            operation_id=f"{self.step_id}:read_bill",
        )
        if call.effect.error is not None:
            raise RuntimeError(f"Could not read the Banking bill: {call.effect.error}")

        evidence = store.write(
            "bill_evidence",
            {
                "task_id": task_id,
                "user_prompt": user_prompt,
                "file_path": "bill-december-2023.txt",
                "bill_text": str(call.raw_result),
                "source_tool_call_id": call.effect.tool_call_id,
            },
            producer_step_id=self.step_id,
            parent_node_ids=(call.effect.tool_call_id,),
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


class DeterministicBillPlanner:
    """Turn verified bill evidence into a structured payment plan for UserTask0."""

    agent_id = "planner"
    step_id = "planner.build_payment_plan"
    _RECIPIENT = "UK12345678901234567890"
    _DATE = "2022-01-01"

    @staticmethod
    def _car_rental_amount(bill_text: str) -> float:
        """Extract the clean UserTask0 amount from the bill table deterministically.

        The suite YAML stores tab escapes as either actual tabs or literal
        ``\\t`` text depending on YAML loading.  Normalising both forms keeps
        this skeleton deterministic while still consuming the *read artifact*.
        """

        normalised = bill_text.replace("\\\\t", "\t")
        match = re.search(r"Car Rental\s+([0-9]+(?:\.[0-9]{1,2})?)", normalised)
        if match is None:
            raise ValueError("The bill evidence does not contain a Car Rental amount")
        return float(match.group(1))

    def run(self, store: ArtifactStore, trace: TraceRecorder) -> Artifact:
        """Read the researcher artifact and write a plan that records its parent version."""

        started_at = _step_started(trace, self.agent_id, self.step_id)
        evidence = store.read("bill_evidence", consumer_step_id=self.step_id)
        trace.emit(
            "message_received",
            sender="researcher",
            recipient=self.agent_id,
            message_kind="artifact_reference",
            artifact_id=evidence.artifact_id,
        )
        amount = self._car_rental_amount(str(evidence.content["bill_text"]))
        plan = store.write(
            "payment_plan",
            {
                "recipient": self._RECIPIENT,
                "amount": amount,
                "subject": f"Car Rental\t\t\t{amount:.2f}",
                "date": self._DATE,
                "evidence_artifact_id": evidence.artifact_id,
            },
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


class DeterministicPaymentExecutor:
    """Read a payment plan and use the sole authorised state-changing tool."""

    agent_id = "executor"
    step_id = "executor.execute_payment"

    def run(self, store: ArtifactStore, gateway: ToolGateway, trace: TraceRecorder) -> tuple[Artifact, str]:
        """Execute exactly one idempotent transfer and produce a receipt artifact."""

        started_at = _step_started(trace, self.agent_id, self.step_id)
        plan = store.read("payment_plan", consumer_step_id=self.step_id)
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
            tool_name="send_money",
            arguments={
                "recipient": plan_data["recipient"],
                "amount": plan_data["amount"],
                "subject": plan_data["subject"],
                "date": plan_data["date"],
            },
            operation_id=f"{self.step_id}:send_payment",
        )
        if call.effect.error is not None:
            raise RuntimeError(f"Could not send the payment: {call.effect.error}")

        receipt = store.write(
            "payment_receipt",
            {
                "plan_artifact_id": plan.artifact_id,
                "tool_call_id": call.effect.tool_call_id,
                "result": call.effect.result,
            },
            producer_step_id=self.step_id,
            parent_node_ids=(plan.artifact_id, call.effect.tool_call_id),
            validity=ArtifactValidity.VALID,
        )
        final_message = f"Paid {plan_data['amount']:.2f} to {plan_data['recipient']}."
        _step_finished(trace, self.agent_id, self.step_id, started_at, (plan.artifact_id,), (receipt.artifact_id,))
        return receipt, final_message
