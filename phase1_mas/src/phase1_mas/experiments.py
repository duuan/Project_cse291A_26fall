"""Command-line entry point for Phase 1 post-experiment metric aggregation.

This command performs no LLM inference and does not mutate AgentDojo state.  It
only reads the paired trace/report files already produced by ``fault_pilot``;
therefore it is safe to rerun after changing a report figure or table.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .metrics import MetricInputError, aggregate_fault_experiment, write_metric_outputs


def _terminal_summary(summary: dict[str, object], paths: dict[str, Path]) -> dict[str, object]:
    """Return a small console view while keeping the full data in JSON files."""

    overall = summary["overall"]
    assert isinstance(overall, dict)  # Internal contract of summarize_experiment_rows.
    clean = overall["clean"]
    faulty = overall["faulty"]
    propagation = overall["failure_propagation"]
    assert isinstance(clean, dict) and isinstance(faulty, dict) and isinstance(propagation, dict)
    return {
        "paired_comparison_count": overall["paired_comparison_count"],
        "clean_task_success": {
            "count": clean["task_success_count"],
            "observed_count": clean["task_success_observed_count"],
            "rate": clean["task_success_rate"],
        },
        "faulty_task_success": {
            "count": faulty["task_success_count"],
            "observed_count": faulty["task_success_observed_count"],
            "rate": faulty["task_success_rate"],
        },
        "failure_propagation": propagation,
        "metric_paths": {name: str(path) for name, path in paths.items()},
    }


def main() -> None:
    """Aggregate an existing fault grid into report-ready Phase 1 measurements."""

    parser = argparse.ArgumentParser(description="Aggregate paired Phase 1 fault-experiment metrics")
    parser.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        help="Fault-pilot output directory containing fault_pilot_summary.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write metrics; defaults to <input-dir>/metrics.",
    )
    args = parser.parse_args()
    output_dir = args.output_dir or args.input_dir / "metrics"
    try:
        rows, summary = aggregate_fault_experiment(args.input_dir)
        paths = write_metric_outputs(rows=rows, summary=summary, output_dir=output_dir)
    except MetricInputError as error:
        parser.error(str(error))
    print(json.dumps(_terminal_summary(summary, paths), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
