"""Phase 1 multi-agent workflow package.

This package deliberately owns the workflow, trace, and fault-facing interfaces.
AgentDojo remains an external task/environment provider rather than being modified.
"""

# Re-export the only public entry point so later experiments have one stable import.
from .workflow import DeterministicPaymentWorkflow

__all__ = ["DeterministicPaymentWorkflow"]
