"""Narrow adapter around AgentDojo's Banking suite.

Only this module knows AgentDojo import paths.  The rest of the workflow works
with plain Python values, so replacing or upgrading AgentDojo does not require
rewriting the multi-agent orchestration and trace code.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .models import ToolEffect


@dataclass
class BankingTaskContext:
    """The live task, mutable environment, and runtime for one isolated run."""

    task_id: str
    task: Any
    environment: Any
    pre_environment: Any
    runtime: Any


class AgentDojoAdapter:
    """Load one Banking task and evaluate it using AgentDojo's original oracle."""

    def __init__(self, benchmark_version: str = "v1.2.2") -> None:
        self.benchmark_version = benchmark_version

    @staticmethod
    def _imports() -> tuple[Any, Any, Any]:
        """Import AgentDojo lazily so non-integration tests need no LLM packages."""

        try:
            from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime
            from agentdojo.task_suite.load_suites import get_suite
        except ImportError as error:
            raise RuntimeError(
                "AgentDojo is not importable. Add AgentDojo/agentdojo/src to PYTHONPATH "
                "and install its dependencies before running the Banking integration demo."
            ) from error
        return FunctionCall, FunctionsRuntime, get_suite

    def create_context(
        self, task_id: str = "user_task_0", injections: dict[str, str] | None = None
    ) -> BankingTaskContext:
        """Create a pristine environment; never reuse state across runs."""

        _, FunctionsRuntime, get_suite = self._imports()
        suite = get_suite(self.benchmark_version, "banking")
        task = suite.get_user_task_by_id(task_id)
        environment = task.init_environment(suite.load_and_inject_default_environment(injections or {}))

        # Pydantic's deep copy retains the exact environment type expected by
        # AgentDojo's built-in utility functions.  ``deepcopy`` is a fallback
        # for a future non-Pydantic benchmark adapter.
        if hasattr(environment, "model_copy"):
            pre_environment = environment.model_copy(deep=True)
        else:
            pre_environment = deepcopy(environment)
        return BankingTaskContext(
            task_id=task_id,
            task=task,
            environment=environment,
            pre_environment=pre_environment,
            runtime=FunctionsRuntime(suite.tools),
        )

    def evaluate(
        self,
        context: BankingTaskContext,
        final_message: str,
        tool_effects: tuple[ToolEffect, ...],
    ) -> bool:
        """Score with AgentDojo's unchanged task oracle and function-call trace."""

        FunctionCall, _, _ = self._imports()
        function_calls = [
            FunctionCall(function=effect.tool_name, args=effect.arguments, id=effect.tool_call_id)
            for effect in tool_effects
            if effect.error is None
        ]
        trace_verdict = context.task.utility_from_traces(
            final_message,
            context.pre_environment,
            context.environment,
            function_calls,
        )
        if trace_verdict is not None:
            return bool(trace_verdict)
        return bool(context.task.utility(final_message, context.pre_environment, context.environment))
