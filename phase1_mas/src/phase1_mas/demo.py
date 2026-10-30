"""Command-line entry point for the Milestone 1 clean deterministic run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .workflow import DeterministicPaymentWorkflow


def main() -> None:
    """Run UserTask0 and print only the small summary needed by a human."""

    parser = argparse.ArgumentParser(description="Run the Phase 1 deterministic Banking skeleton")
    parser.add_argument(
        "--trace",
        type=Path,
        default=Path("phase1_mas/runs/milestone1_user_task_0.jsonl"),
        help="Where to write the append-only JSONL execution trace.",
    )
    args = parser.parse_args()
    # The workflow returns all evidence programmatically; the CLI prints only a
    # concise human-readable success summary while the JSONL file has details.
    result = DeterministicPaymentWorkflow().run(trace_path=args.trace)
    print(
        json.dumps(
            {
                "task_id": result.task_id,
                "task_passed": result.task_passed,
                "tool_calls": [effect.tool_name for effect in result.tool_effects],
                "environment_changed": result.pre_state_hash != result.post_state_hash,
                "trace": str(args.trace),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
