"""Small, dependency-free data models shared by the Phase 1 workflow.

The project needs evidence that can be inspected after a run.  These dataclasses
represent that evidence without tying the workflow itself to an LLM SDK or to
AgentDojo's Pydantic models.  Keeping the models plain also makes JSONL traces
stable and straightforward to use again in Phase 2.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    """Return a timezone-aware timestamp suitable for a trace record."""

    return datetime.now(timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    """Convert Python, dataclass, and Pydantic-like values into JSON-safe data.

    AgentDojo environments are Pydantic objects.  We intentionally use duck
    typing for ``model_dump`` so this module does not import AgentDojo/Pydantic.
    """

    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_jsonable(item) for item in value]
    # ``json.dumps(default=str)`` would also work, but making the conversion
    # explicit keeps each trace event independently serializable.
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value


def canonical_json(value: Any) -> str:
    """Return deterministic JSON so hashes are repeatable across a run."""

    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Any) -> str:
    """Hash a JSON-safe snapshot, artifact, or tool result."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ArtifactValidity(str, Enum):
    """Validity label reserved for later verification and recovery baselines."""

    VALID = "valid"
    UNKNOWN = "unknown"
    INVALID = "invalid"


@dataclass(frozen=True)
class Artifact:
    """A versioned, immutable value passed from one agent step to another."""

    artifact_id: str
    logical_id: str
    version: int
    producer_step_id: str
    content: Any
    content_hash: str
    parent_node_ids: tuple[str, ...] = ()
    validity: ArtifactValidity = ArtifactValidity.UNKNOWN
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True)
class StateSnapshot:
    """A lightweight record of the environment immediately before or after work."""

    snapshot_id: str
    label: str
    state_hash: str
    state: Any
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True)
class ToolEffect:
    """The observable effect of one request made through the tool gateway."""

    tool_call_id: str
    operation_id: str
    agent_id: str
    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: Any
    error: str | None
    read_set: tuple[str, ...]
    write_set: tuple[str, ...]
    pre_state_hash: str
    post_state_hash: str
    state_changed: bool
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True)
class ToolExecution:
    """Return value of the gateway, retaining both raw result and traceable effect."""

    effect: ToolEffect
    raw_result: Any


@dataclass(frozen=True)
class LLMCallRecord:
    """Auditable provider usage and timing for one agent-model interaction."""

    llm_call_id: str
    agent_id: str
    step_id: str
    provider: str
    model: str
    temperature: float
    seed: int | None
    prompt_hash: str
    response_hash: str
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    started_at: str
    ended_at: str
    latency_ms: float
    timeout_seconds: float | None
    retry_count: int
    error: str | None = None


@dataclass(frozen=True)
class StepRecord:
    """A compact summary of a deterministic agent step for later analysis."""

    step_id: str
    agent_id: str
    input_artifact_ids: tuple[str, ...]
    output_artifact_ids: tuple[str, ...]
    started_at: str
    ended_at: str
    status: str


@dataclass(frozen=True)
class WorkflowRunResult:
    """The programmatic result returned by one clean multi-agent workflow run."""

    run_id: str
    task_id: str
    task_passed: bool
    final_message: str
    trace_path: Path | None
    pre_state_hash: str
    post_state_hash: str
    artifacts: tuple[Artifact, ...]
    tool_effects: tuple[ToolEffect, ...]
    # ``score`` is intentionally generic here to avoid a models↔scoring import
    # cycle.  At runtime it is a ``CleanTaskScore`` from ``scoring.py``.
    score: Any | None = None
