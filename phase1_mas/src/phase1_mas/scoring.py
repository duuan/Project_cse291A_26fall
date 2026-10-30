"""Post-run scoring for the clean baseline.

This module is deliberately called only after the Executor has finished.  It
keeps AgentDojo's task utility separate from the agents, so runtime planning
cannot inspect ``ground_truth`` or final utility conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .agentdojo_adapter import AgentDojoAdapter, BankingTaskContext
from .models import ToolEffect
from .trace import TraceRecorder


@dataclass(frozen=True)
class CleanTaskScore:
    """Machine-readable outcome of one no-fault baseline run."""

    task_id: str
    task_success: bool
    scorer: str
    tool_call_count: int
    tool_errors: tuple[str, ...]
    denied_tool_call_count: int
    environment_changed: bool

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready view used by the pilot command and later metrics."""

        return {
            "task_id": self.task_id,
            "task_success": self.task_success,
            "scorer": self.scorer,
            "tool_call_count": self.tool_call_count,
            "tool_errors": list(self.tool_errors),
            "denied_tool_call_count": self.denied_tool_call_count,
            "environment_changed": self.environment_changed,
        }


@dataclass(frozen=True)
class CleanPilotSummary:
    """Aggregate result for a fixed clean task set, not a fault experiment."""

    scores: tuple[CleanTaskScore, ...]
    success_count: int
    task_count: int
    task_success_rate: float

    def as_dict(self) -> dict[str, Any]:
        """Return a stable, report-friendly representation of the pilot."""

        return {
            "task_count": self.task_count,
            "success_count": self.success_count,
            "task_success_rate": self.task_success_rate,
            "scores": [score.as_dict() for score in self.scores],
        }


class AgentDojoScorer:
    """Use the unchanged AgentDojo utility as the sole task-success oracle."""

    def __init__(self, adapter: AgentDojoAdapter) -> None:
        self.adapter = adapter

    def score(
        self,
        context: BankingTaskContext,
        final_message: str,
        tool_effects: tuple[ToolEffect, ...],
        pre_state_hash: str,
        post_state_hash: str,
        trace: TraceRecorder,
    ) -> CleanTaskScore:
        """Evaluate final utility and summarize observable tool-policy outcomes."""

        # This is the only call path to AgentDojo's hidden success condition.
        # Nothing returned from this method is fed back to an agent during work.
        task_success = self.adapter.evaluate(context, final_message, tool_effects)
        tool_errors = tuple(
            f"{effect.tool_name}: {effect.error}" for effect in tool_effects if effect.error is not None
        )
        denied_count = sum(1 for event in trace.events if event["event_type"] == "tool_call_denied")
        score = CleanTaskScore(
            task_id=context.task_id,
            task_success=task_success,
            scorer="agentdojo_user_task_utility",
            tool_call_count=len(tool_effects),
            tool_errors=tool_errors,
            denied_tool_call_count=denied_count,
            environment_changed=pre_state_hash != post_state_hash,
        )
        trace.emit("run_scored", score=score)
        return score


def summarize_clean_pilot(scores: Sequence[CleanTaskScore]) -> CleanPilotSummary:
    """Compute task success rate without mixing clean and faulted outcomes."""

    frozen_scores = tuple(scores)
    success_count = sum(score.task_success for score in frozen_scores)
    task_count = len(frozen_scores)
    return CleanPilotSummary(
        scores=frozen_scores,
        success_count=success_count,
        task_count=task_count,
        task_success_rate=(success_count / task_count) if task_count else 0.0,
    )
