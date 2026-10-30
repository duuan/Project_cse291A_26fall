"""One traceable, configurable entry point for every LLM inference call.

The implementation uses the OpenAI-compatible chat-completions protocol.  It
therefore works with OpenAI when ``PHASE1_MAS_BASE_URL`` is unset and with a
local vLLM-compatible service when a base URL is supplied.  No provider client
is imported until a real gateway is constructed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .models import LLMCallRecord, stable_hash, utc_now_iso
from .trace import TraceRecorder


class LLMConfigurationError(RuntimeError):
    """Raised when required model configuration is absent or invalid."""


class LLMRequestError(RuntimeError):
    """Raised after an LLM provider request fails and its trace is recorded."""


@dataclass(frozen=True)
class LLMCompletion:
    """Text returned by a model plus the provider-usage trace record."""

    content: str
    record: LLMCallRecord


class LLMGateway(Protocol):
    """Minimal interface that production and test gateways both satisfy."""

    def complete(
        self,
        *,
        agent_id: str,
        step_id: str,
        messages: Sequence[dict[str, str]],
        trace: TraceRecorder,
    ) -> LLMCompletion:
        """Return one completion and emit its LLM trace events."""


@dataclass(frozen=True)
class LLMGatewaySettings:
    """Experiment-relevant LLM settings, supplied explicitly or from environment."""

    provider: str
    model: str
    api_key: str
    base_url: str | None
    temperature: float
    timeout_seconds: float
    max_tokens: int | None
    seed: int | None
    input_usd_per_million_tokens: float | None
    output_usd_per_million_tokens: float | None

    @staticmethod
    def _optional_float(name: str) -> float | None:
        """Parse an optional numeric environment value with a clear error."""

        value = os.getenv(name)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except ValueError as error:
            raise LLMConfigurationError(f"{name} must be a number, got {value!r}") from error

    @classmethod
    def from_env(cls) -> "LLMGatewaySettings":
        """Load reproducible settings without embedding a provider/model choice in code."""

        model = os.getenv("PHASE1_MAS_MODEL")
        if not model:
            raise LLMConfigurationError(
                "Set PHASE1_MAS_MODEL (for example, your OpenAI model name or local vLLM model ID)."
            )
        api_key = os.getenv("PHASE1_MAS_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMConfigurationError(
                "Set PHASE1_MAS_API_KEY or OPENAI_API_KEY. For a local OpenAI-compatible server, use a placeholder such as EMPTY."
            )
        try:
            temperature = float(os.getenv("PHASE1_MAS_TEMPERATURE", "0"))
            timeout_seconds = float(os.getenv("PHASE1_MAS_TIMEOUT_SECONDS", "60"))
            max_tokens_raw = os.getenv("PHASE1_MAS_MAX_TOKENS")
            max_tokens = int(max_tokens_raw) if max_tokens_raw else None
            seed_raw = os.getenv("PHASE1_MAS_SEED")
            seed = int(seed_raw) if seed_raw else None
        except ValueError as error:
            raise LLMConfigurationError("Temperature, timeout, max tokens, and seed must be numeric.") from error
        return cls(
            provider=os.getenv("PHASE1_MAS_PROVIDER", "openai_compatible"),
            model=model,
            api_key=api_key,
            base_url=os.getenv("PHASE1_MAS_BASE_URL") or None,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            seed=seed,
            input_usd_per_million_tokens=cls._optional_float("PHASE1_MAS_INPUT_USD_PER_MILLION_TOKENS"),
            output_usd_per_million_tokens=cls._optional_float("PHASE1_MAS_OUTPUT_USD_PER_MILLION_TOKENS"),
        )


class OpenAICompatibleLLMGateway:
    """Execute chat completions and trace messages, tokens, latency, and cost."""

    def __init__(self, settings: LLMGatewaySettings) -> None:
        self.settings = settings
        self._call_number = 0
        try:
            from openai import OpenAI
        except ImportError as error:
            raise LLMConfigurationError("Install the phase1_mas package dependencies: python -m pip install -e ./phase1_mas") from error
        # ``base_url=None`` selects the normal OpenAI endpoint; supplying one
        # makes this equally usable with a local OpenAI-compatible model server.
        self._client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=settings.timeout_seconds)

    @classmethod
    def from_env(cls) -> "OpenAICompatibleLLMGateway":
        """Construct the production gateway using only documented environment variables."""

        return cls(LLMGatewaySettings.from_env())

    def _estimated_cost(self, input_tokens: int | None, output_tokens: int | None) -> float | None:
        """Estimate cost only when the experiment manifest provides both token prices."""

        if (
            input_tokens is None
            or output_tokens is None
            or self.settings.input_usd_per_million_tokens is None
            or self.settings.output_usd_per_million_tokens is None
        ):
            return None
        return (
            input_tokens * self.settings.input_usd_per_million_tokens
            + output_tokens * self.settings.output_usd_per_million_tokens
        ) / 1_000_000

    def complete(
        self,
        *,
        agent_id: str,
        step_id: str,
        messages: Sequence[dict[str, str]],
        trace: TraceRecorder,
    ) -> LLMCompletion:
        """Call the configured provider once and write start/finish trace events."""

        self._call_number += 1
        llm_call_id = f"llm_{self._call_number:04d}"
        started_at = utc_now_iso()
        started_clock = time.perf_counter()
        prompt_hash = stable_hash(messages)
        trace.emit(
            "llm_call_started",
            llm_call_id=llm_call_id,
            agent_id=agent_id,
            step_id=step_id,
            provider=self.settings.provider,
            model=self.settings.model,
            temperature=self.settings.temperature,
            seed=self.settings.seed,
            prompt_hash=prompt_hash,
            # Record actual messages because Phase 1 requires message-level
            # traces; hashes additionally simplify paired-run comparisons.
            messages=messages,
        )
        request_kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": list(messages),
            "temperature": self.settings.temperature,
        }
        if self.settings.max_tokens is not None:
            request_kwargs["max_tokens"] = self.settings.max_tokens
        if self.settings.seed is not None:
            request_kwargs["seed"] = self.settings.seed
        try:
            response = self._client.chat.completions.create(**request_kwargs)
            content = response.choices[0].message.content or ""
            usage = response.usage
            input_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
            error = None
        except Exception as caught_error:
            content = ""
            input_tokens = None
            output_tokens = None
            error = f"{type(caught_error).__name__}: {caught_error}"
        ended_at = utc_now_iso()
        latency_ms = (time.perf_counter() - started_clock) * 1_000
        record = LLMCallRecord(
            llm_call_id=llm_call_id,
            agent_id=agent_id,
            step_id=step_id,
            provider=self.settings.provider,
            model=self.settings.model,
            temperature=self.settings.temperature,
            seed=self.settings.seed,
            prompt_hash=prompt_hash,
            response_hash=stable_hash(content),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=self._estimated_cost(input_tokens, output_tokens),
            started_at=started_at,
            ended_at=ended_at,
            latency_ms=latency_ms,
            timeout_seconds=self.settings.timeout_seconds,
            retry_count=0,
            error=error,
        )
        trace.emit("llm_call_finished", record=record, response=content)
        if error is not None:
            raise LLMRequestError(f"LLM call {llm_call_id} failed: {error}")
        return LLMCompletion(content=content, record=record)


class ScriptedLLMGateway:
    """A trace-producing fake gateway for offline contract tests, never experiments."""

    def __init__(self, responses: Sequence[str], model: str = "scripted-test-model") -> None:
        self._responses = iter(responses)
        self.model = model
        self._call_number = 0

    def complete(
        self,
        *,
        agent_id: str,
        step_id: str,
        messages: Sequence[dict[str, str]],
        trace: TraceRecorder,
    ) -> LLMCompletion:
        """Return a pre-supplied response while exercising the same trace contract."""

        self._call_number += 1
        llm_call_id = f"llm_{self._call_number:04d}"
        started_at = utc_now_iso()
        try:
            content = next(self._responses)
        except StopIteration as error:
            raise LLMRequestError("ScriptedLLMGateway ran out of responses") from error
        ended_at = utc_now_iso()
        record = LLMCallRecord(
            llm_call_id=llm_call_id,
            agent_id=agent_id,
            step_id=step_id,
            provider="scripted",
            model=self.model,
            temperature=0.0,
            seed=0,
            prompt_hash=stable_hash(messages),
            response_hash=stable_hash(content),
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=0.0,
            started_at=started_at,
            ended_at=ended_at,
            latency_ms=0.0,
            timeout_seconds=None,
            retry_count=0,
        )
        trace.emit("llm_call_started", llm_call_id=llm_call_id, agent_id=agent_id, step_id=step_id, messages=messages)
        trace.emit("llm_call_finished", record=record, response=content)
        return LLMCompletion(content=content, record=record)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse one model-produced JSON object, tolerating Markdown code fences."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise LLMRequestError(f"Model did not return valid JSON: {error.msg}") from error
    if not isinstance(value, dict):
        raise LLMRequestError("Model response must be a JSON object")
    return value
