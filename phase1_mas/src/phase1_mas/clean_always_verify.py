"""Run the eight clean Banking tasks with the existing handoff verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from .agents import LLM_CLEAN_PILOT_TASK_IDS
from .metrics import WorkSlice, summarize_trace
from .recovery import AlwaysVerifyArtifactVerifier, _public_llm_configuration, _verifier_work
from .trace import read_jsonl
from .workflow import LLMMultiAgentWorkflow


DEFAULT_REFERENCE = Path("phase1_mas/phase1_results_248_seed42")


def _run_outcome(events: tuple[dict[str, Any], ...] | list[dict[str, Any]], task_success: bool) -> dict[str, Any]:
    """Keep utility, completed user action, and safe verifier stop distinct."""

    finished = next((event for event in reversed(events) if event.get("event_type") == "run_finished"), {})
    receipts = [
        event["artifact"]["content"]
        for event in events
        if event.get("event_type") == "artifact_written"
        and event.get("artifact", {}).get("logical_id") == "execution_receipt"
    ]
    receipt = receipts[-1] if receipts else {}
    executor_write = any(
        event.get("event_type") == "tool_call_finished"
        and event.get("effect", {}).get("agent_id") == "executor"
        and event.get("effect", {}).get("state_changed") is True
        for event in events
    )
    reason = finished.get("termination_reason")
    return {
        "task_utility_success": task_success,
        "correct_task_completion": bool(task_success and reason == "completed" and receipt.get("approved") is True and executor_write),
        "safe_stopping": reason in {"verifier_rejected_task_evidence", "verifier_rejected_execution_plan"},
        "termination_reason": reason,
        "executor_state_changing_action": executor_write,
    }


def _summary(rows: list[dict[str, Any]], config: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    total = WorkSlice()
    verifier_total = WorkSlice()
    handoffs: dict[str, dict[str, int]] = {
        "researcher_to_planner": {"accepted": 0, "rejected": 0},
        "planner_to_executor": {"accepted": 0, "rejected": 0},
    }
    latencies = []
    for row in rows:
        total = total.plus(row["work_object"])
        verifier_total = verifier_total.plus(row["verifier_work_object"])
        if row["end_to_end_latency_ms"] is not None:
            latencies.append(row["end_to_end_latency_ms"])
        for decision in row["verifier_decisions"]:
            handoff = (
                "researcher_to_planner" if decision["logical_id"] == "task_evidence" else "planner_to_executor"
            )
            handoffs[handoff]["accepted" if decision["accepted"] else "rejected"] += 1
    serializable_rows = [
        {key: value for key, value in row.items() if key not in {"work_object", "verifier_work_object"}}
        for row in rows
    ]
    rejection_count = sum(value["rejected"] for value in handoffs.values())
    decision_count = sum(sum(value.values()) for value in handoffs.values())
    for value in handoffs.values():
        observed = value["accepted"] + value["rejected"]
        value["rejection_rate_on_clean_handoffs"] = value["rejected"] / observed if observed else None
    reference_work = reference["total_work"]
    return {
        "condition": "clean_always_verify",
        "task_ids": list(LLM_CLEAN_PILOT_TASK_IDS),
        "llm_configuration": config,
        "attempt_count": len(rows),
        "task_utility_success_count": sum(row["outcome"]["task_utility_success"] for row in rows),
        "task_utility_success_rate": sum(row["outcome"]["task_utility_success"] for row in rows) / len(rows) if rows else None,
        "correct_task_completion_count": sum(row["outcome"]["correct_task_completion"] for row in rows),
        "correct_task_completion_rate": sum(row["outcome"]["correct_task_completion"] for row in rows) / len(rows) if rows else None,
        "safe_stopping_count": sum(row["outcome"]["safe_stopping"] for row in rows),
        "verification": {
            "by_handoff": handoffs,
            "clean_handoff_rejection_count": rejection_count,
            "clean_handoff_rejection_rate": rejection_count / decision_count if decision_count else None,
            "clean_task_rejection_rate": sum(row["outcome"]["safe_stopping"] for row in rows) / len(rows) if rows else None,
            "interpretation": "No injected fault; a rejection is a false-rejection proxy, not proof that the artifact was semantically correct.",
        },
        "total_work": total.as_dict(),
        "verifier_only_work": verifier_total.as_dict(),
        "end_to_end_latency_ms": {"observed_count": len(latencies), "mean": mean(latencies) if latencies else None, "sum": sum(latencies) if latencies else None},
        "reference_clean_baseline": reference,
        "comparison_with_clean_baseline": {
            "llm_call_delta": total.llm_call_count - reference_work["llm_call_count"],
            "input_token_delta": total.input_tokens_known - reference_work["input_tokens_known"],
            "output_token_delta": total.output_tokens_known - reference_work["output_tokens_known"],
            "estimated_cost_usd_delta": total.estimated_cost_usd_known - reference_work["estimated_cost_usd_known"],
            "mean_end_to_end_latency_ms_delta": (
                mean(latencies) - reference["end_to_end_latency_ms"]["mean"]
                if latencies and reference["end_to_end_latency_ms"]["mean"] is not None else None
            ),
        },
        "runs": serializable_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run only the eight clean + always-verify Banking tasks")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error(f"Refusing to overwrite existing directory: {args.output_dir}")

    reference_config_path = args.reference_dir / "03_full_restart/signaled/recovery_pilot_summary.json"
    reference_clean_dir = args.reference_dir / "01_clean_baseline"
    reference_config = json.loads(reference_config_path.read_text(encoding="utf-8"))["llm_configuration"]
    workflow = LLMMultiAgentWorkflow.from_env()
    config = _public_llm_configuration(workflow)
    if config != reference_config or config.get("seed") != 42:
        parser.error("Current public LLM configuration must exactly match the seed-42 reference experiment")

    baseline_rows = []
    for task_id in LLM_CLEAN_PILOT_TASK_IDS:
        trace_path = reference_clean_dir / f"clean_{task_id}.jsonl"
        baseline_rows.append(summarize_trace(trace_path).as_dict())
    baseline_work = WorkSlice()
    baseline_completed = 0
    for row in baseline_rows:
        work = row["work"]
        baseline_work = baseline_work.plus(WorkSlice(**work))
        events = read_jsonl(Path(row["trace_path"]))
        baseline_completed += _run_outcome(events, row["task_success"] is True)["correct_task_completion"]
    baseline_latencies = [row["end_to_end_latency_ms"] for row in baseline_rows if row["end_to_end_latency_ms"] is not None]
    reference = {
        "task_utility_success_count": sum(row["task_success"] is True for row in baseline_rows),
        "correct_task_completion_count": baseline_completed,
        "total_work": baseline_work.as_dict(),
        "end_to_end_latency_ms": {"observed_count": len(baseline_latencies), "mean": mean(baseline_latencies) if baseline_latencies else None, "sum": sum(baseline_latencies)},
        "source": str(reference_clean_dir),
    }

    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    for task_id in LLM_CLEAN_PILOT_TASK_IDS:
        trace_path = args.output_dir / f"clean_always_verify_{task_id}.jsonl"
        result = workflow.run(task_id=task_id, trace_path=trace_path, verifier=AlwaysVerifyArtifactVerifier())
        measured = summarize_trace(trace_path)
        decisions = [event["decision"] for event in result.trace_events if event.get("event_type") == "verification_finished"]
        verifier_work = _verifier_work(result)
        rows.append({
            "task_id": task_id,
            "trace_path": str(trace_path),
            "outcome": _run_outcome(result.trace_events, result.task_passed),
            "verifier_decisions": decisions,
            "work": measured.work.as_dict(),
            "verifier_work": verifier_work.as_dict(),
            "end_to_end_latency_ms": measured.end_to_end_latency_ms,
            "work_object": measured.work,
            "verifier_work_object": verifier_work,
        })
        summary = _summary(rows, config, reference)
        (args.output_dir / "clean_always_verify_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{task_id}: utility={result.task_passed}, completion={rows[-1]['outcome']['correct_task_completion']}", flush=True)
    print(f"Summary: {args.output_dir / 'clean_always_verify_summary.json'}")


if __name__ == "__main__":
    main()
