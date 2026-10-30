"""The sole gateway from an agent step to AgentDojo tools and mutable state.

Centralising tool calls is intentional: it gives every call a permission check,
idempotency key, before/after snapshot, state-diff summary, and trace events.
Those same hooks will later host injected tool failures and recovery logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .models import StateSnapshot, ToolEffect, ToolExecution, stable_hash, to_jsonable
from .trace import TraceRecorder


# These sets are semantic labels for metrics, not security assumptions about
# AgentDojo.  They make each trace explain which environment component a call
# could observe or mutate.
_TOOL_STATE_ACCESS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "read_file": (("filesystem",), ()),
    "get_balance": (("bank_account",), ()),
    "get_iban": (("bank_account",), ()),
    "get_most_recent_transactions": (("bank_account.transactions",), ()),
    "get_scheduled_transactions": (("bank_account.scheduled_transactions",), ()),
    "get_user_info": (("user_account",), ()),
    # AgentDojo's ``send_money`` appends a transaction; it does not deduct the
    # demo account balance, so the declared write set must match that behavior.
    "send_money": (("bank_account",), ("bank_account.transactions",)),
    "schedule_transaction": (("bank_account",), ("bank_account.scheduled_transactions",)),
    "update_scheduled_transaction": (("bank_account.scheduled_transactions",), ("bank_account.scheduled_transactions",)),
    "update_password": (("user_account",), ("user_account",)),
    "update_user_info": (("user_account",), ("user_account",)),
}


def _top_level_diff(before: Any, after: Any) -> dict[str, Any]:
    """Give a readable state diff without storing duplicate full environments."""

    before_json = to_jsonable(before)
    after_json = to_jsonable(after)
    if not isinstance(before_json, dict) or not isinstance(after_json, dict):
        return {"changed": before_json != after_json}
    changed = sorted(
        key for key in set(before_json) | set(after_json) if before_json.get(key) != after_json.get(key)
    )
    return {"changed_top_level_fields": changed}


class UnauthorizedToolError(PermissionError):
    """Raised when an agent asks for a tool outside its declared capabilities."""


class ToolGateway:
    """Run AgentDojo functions while recording auditable effects and snapshots."""

    def __init__(
        self,
        runtime: Any,
        environment: Any,
        trace: TraceRecorder,
        permissions: Mapping[str, set[str]],
    ) -> None:
        self.runtime = runtime
        self.environment = environment
        self.trace = trace
        self.permissions = {agent_id: set(tools) for agent_id, tools in permissions.items()}
        self.effects: list[ToolEffect] = []
        self._executions_by_operation: dict[str, ToolExecution] = {}
        self._call_number = 0
        self._snapshot_number = 0

    def snapshot(self, label: str) -> StateSnapshot:
        """Capture and trace an immutable view of the current environment state."""

        self._snapshot_number += 1
        state = deepcopy(to_jsonable(self.environment))
        snapshot = StateSnapshot(
            snapshot_id=f"state_{self._snapshot_number:04d}",
            label=label,
            state_hash=stable_hash(state),
            state=state,
        )
        self.trace.emit("state_snapshot", snapshot=snapshot)
        return snapshot

    def call(
        self,
        *,
        agent_id: str,
        step_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        operation_id: str,
    ) -> ToolExecution:
        """Authorize, execute (once), and trace a tool request.

        Repeating an existing ``operation_id`` returns the first result without
        executing the underlying side effect again.  That is already useful for
        full-restart experiments and avoids accidentally double-paying a bill.
        """

        allowed_tools = self.permissions.get(agent_id, set())
        if tool_name not in allowed_tools:
            self.trace.emit(
                "tool_call_denied",
                agent_id=agent_id,
                step_id=step_id,
                tool_name=tool_name,
                arguments=arguments,
                reason="tool_not_in_agent_permission_set",
            )
            raise UnauthorizedToolError(f"Agent {agent_id!r} is not allowed to call {tool_name!r}")

        if operation_id in self._executions_by_operation:
            prior = self._executions_by_operation[operation_id]
            self.trace.emit(
                "tool_call_deduplicated",
                tool_call_id=prior.effect.tool_call_id,
                operation_id=operation_id,
                agent_id=agent_id,
                step_id=step_id,
                tool_name=tool_name,
            )
            return prior

        self._call_number += 1
        tool_call_id = f"tool_{self._call_number:04d}"
        pre_snapshot = self.snapshot(f"before:{tool_call_id}")
        read_set, write_set = _TOOL_STATE_ACCESS.get(tool_name, ((), ()))
        self.trace.emit(
            "tool_call_started",
            tool_call_id=tool_call_id,
            operation_id=operation_id,
            agent_id=agent_id,
            step_id=step_id,
            tool_name=tool_name,
            arguments=arguments,
            read_set=read_set,
            write_set=write_set,
        )

        # AgentDojo catches tool exceptions and returns a human-readable error;
        # keeping that convention means future injected failures are comparable.
        raw_result, error = self.runtime.run_function(self.environment, tool_name, arguments)
        post_snapshot = self.snapshot(f"after:{tool_call_id}")
        effect = ToolEffect(
            tool_call_id=tool_call_id,
            operation_id=operation_id,
            agent_id=agent_id,
            step_id=step_id,
            tool_name=tool_name,
            arguments=deepcopy(arguments),
            result=to_jsonable(raw_result),
            error=error,
            read_set=read_set,
            write_set=write_set,
            pre_state_hash=pre_snapshot.state_hash,
            post_state_hash=post_snapshot.state_hash,
            state_changed=pre_snapshot.state_hash != post_snapshot.state_hash,
        )
        execution = ToolExecution(effect=effect, raw_result=raw_result)
        self.effects.append(effect)
        self._executions_by_operation[operation_id] = execution
        self.trace.emit(
            "tool_call_finished",
            effect=effect,
            state_diff=_top_level_diff(pre_snapshot.state, post_snapshot.state),
        )
        return execution
