"""Versioned artifact storage and explicit artifact dependency recording."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from .models import Artifact, ArtifactValidity, stable_hash
from .trace import TraceRecorder


class ArtifactNotFoundError(KeyError):
    """Raised when a consumer requests an artifact/version that does not exist."""


class ArtifactStore:
    """Store immutable artifact versions and expose their producer/consumer links.

    ``logical_id`` (for example, ``payment_plan``) is stable while
    ``artifact_id`` (for example, ``payment_plan:v1``) identifies one exact
    version.  This distinction is necessary for stale-state experiments later.
    """

    def __init__(self, trace: TraceRecorder) -> None:
        self.trace = trace
        self._by_logical_id: dict[str, list[Artifact]] = defaultdict(list)
        self._consumer_steps: dict[str, set[str]] = defaultdict(set)

    def write(
        self,
        logical_id: str,
        content: Any,
        producer_step_id: str,
        *,
        parent_node_ids: tuple[str, ...] = (),
        validity: ArtifactValidity = ArtifactValidity.UNKNOWN,
    ) -> Artifact:
        """Create a new immutable version and record incoming dependencies."""

        version = len(self._by_logical_id[logical_id]) + 1
        artifact = Artifact(
            artifact_id=f"{logical_id}:v{version}",
            logical_id=logical_id,
            version=version,
            producer_step_id=producer_step_id,
            # A copy protects the historical trace when a caller mutates its
            # local dict after writing it.
            content=deepcopy(content),
            content_hash=stable_hash(content),
            parent_node_ids=tuple(parent_node_ids),
            validity=validity,
        )
        self._by_logical_id[logical_id].append(artifact)
        self.trace.emit(
            "artifact_written",
            artifact=artifact,
            dependency_edges=[{"from": parent, "to": artifact.artifact_id} for parent in parent_node_ids],
        )
        return artifact

    def read(
        self,
        logical_id: str,
        consumer_step_id: str,
        *,
        version: int | None = None,
    ) -> Artifact:
        """Read the latest version by default and trace which consumer used it."""

        versions = self._by_logical_id.get(logical_id, [])
        if not versions:
            raise ArtifactNotFoundError(f"No artifact exists for logical id {logical_id!r}")
        if version is None:
            artifact = versions[-1]
        elif 1 <= version <= len(versions):
            artifact = versions[version - 1]
        else:
            raise ArtifactNotFoundError(f"No version {version} exists for artifact {logical_id!r}")

        self._consumer_steps[artifact.artifact_id].add(consumer_step_id)
        self.trace.emit(
            "artifact_read",
            artifact_id=artifact.artifact_id,
            logical_id=logical_id,
            version=artifact.version,
            consumer_step_id=consumer_step_id,
            producer_step_id=artifact.producer_step_id,
        )
        return artifact

    def all_artifacts(self) -> tuple[Artifact, ...]:
        """Return artifacts in write order for result summaries and tests."""

        return tuple(artifact for versions in self._by_logical_id.values() for artifact in versions)

    def consumers_of(self, artifact_id: str) -> tuple[str, ...]:
        """Return known consumer steps; useful for Phase 1 propagation metrics."""

        return tuple(sorted(self._consumer_steps.get(artifact_id, set())))

    def dependency_edges(self) -> list[dict[str, str]]:
        """Expose artifact-to-artifact/tool links in a simple graph-friendly form."""

        edges: list[dict[str, str]] = []
        for artifact in self.all_artifacts():
            edges.extend({"from": parent, "to": artifact.artifact_id} for parent in artifact.parent_node_ids)
        return edges
