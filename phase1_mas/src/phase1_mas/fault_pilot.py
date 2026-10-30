"""CLI for matched clean-versus-fault Phase 1 propagation experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .faults import FaultSpec, FaultType
from .agents import LLM_CLEAN_PILOT_TASK_IDS
from .metrics import aggregate_fault_experiment, write_metric_outputs
from .workflow import LLMMultiAgentWorkflow


def _parse_payload(value: str | None) -> dict[str, Any] | None:
    """Accept an optional JSON object without requiring a separate config parser yet."""

    if value is None:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("--payload must be a JSON object") from error
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("--payload must decode to a JSON object")
    return payload


def _run_one(
    workflow: LLMMultiAgentWorkflow,
    output_dir: Path,
    task_id: str,
    fault_type: FaultType,
    seed: int,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Execute one fair clean/fault pair and persist its propagation report."""

    fault_id = f"{fault_type.value}_seed_{seed}"
    spec = FaultSpec(fault_id=fault_id, fault_type=fault_type, payload=payload, seed=seed)
    clean, faulty, report = workflow.run_paired(task_id=task_id, fault_spec=spec, output_dir=output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "propagation_report.json"
    report_path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "fault_id": fault_id,
        "fault_type": fault_type.value,
        "category": fault_type.category,
        "clean_task_success": clean.task_passed,
        "faulty_task_success": faulty.task_passed,
        "propagation_report": report.as_dict(),
        "report_path": str(report_path),
    }


def main() -> None:
    """Run one required fault family or all four using identical clean/fault mechanics."""

    parser = argparse.ArgumentParser(description="Run paired Phase 1 fault-propagation experiments")
    parser.add_argument(
        "--task-id",
        default="user_task_0",
        help="AgentDojo Banking user task ID, or 'all' for the fixed 8-task LLM manifest.",
    )
    parser.add_argument(
        "--fault-type",
        choices=[fault_type.value for fault_type in FaultType] + ["all"],
        required=True,
        help="One required fault family, or all four families with default payloads.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Recorded fault-schedule seed.")
    parser.add_argument("--payload", help="Optional JSON object overriding the selected fault's default payload.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("phase1_mas/results/fault_pilot"),
        help="Directory for paired traces and propagation reports.",
    )
    args = parser.parse_args()
    payload = _parse_payload(args.payload)
    if args.fault_type == "all" and payload is not None:
        parser.error("--payload is supported only with one specific --fault-type")

    workflow = LLMMultiAgentWorkflow.from_env()
    types = list(FaultType) if args.fault_type == "all" else [FaultType(args.fault_type)]
    task_ids = LLM_CLEAN_PILOT_TASK_IDS if args.task_id == "all" else (args.task_id,)
    reports = []
    for task_index, task_id in enumerate(task_ids):
        for fault_index, fault_type in enumerate(types):
            # A distinct recorded seed identifies every point in the grid,
            # while the gateway keeps the same model configuration everywhere.
            reports.append(
                _run_one(
                    workflow,
                    args.output_dir / task_id / fault_type.value,
                    task_id,
                    fault_type,
                    args.seed + task_index * len(types) + fault_index,
                    payload,
                )
            )
    summary_path = args.output_dir / "fault_pilot_summary.json"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"task_ids": list(task_ids), "reports": reports}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Metric aggregation is purely post-hoc: it re-reads the completed JSONL
    # traces and therefore cannot influence this run's LLM behavior.  Keeping
    # it here means every newly completed fault grid immediately has the Phase
    # 1 report numbers, while ``python -m phase1_mas.experiments`` can still
    # re-aggregate an already-run grid without paying for new model calls.
    metric_rows, metric_summary = aggregate_fault_experiment(args.output_dir)
    metric_paths = write_metric_outputs(
        rows=metric_rows,
        summary=metric_summary,
        output_dir=args.output_dir / "metrics",
    )
    overall = metric_summary["overall"]
    print(
        json.dumps(
            {
                "task_ids": list(task_ids),
                "paired_comparison_count": len(reports),
                "summary_path": str(summary_path),
                "clean_task_success": overall["clean"],
                "faulty_task_success": overall["faulty"],
                "failure_propagation": overall["failure_propagation"],
                "metric_paths": {name: str(path) for name, path in metric_paths.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
