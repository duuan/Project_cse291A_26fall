"""Phase 1 multi-agent workflow package.

This package deliberately owns the workflow, trace, and fault-facing interfaces.
AgentDojo remains an external task/environment provider rather than being modified.
"""

# Re-export stable workflow entry points so scripts do not import internals.
from .workflow import CleanMultiAgentWorkflow, DeterministicPaymentWorkflow

__all__ = ["CleanMultiAgentWorkflow", "DeterministicPaymentWorkflow"]
