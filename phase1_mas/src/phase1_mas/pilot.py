"""Command-line runner for Milestone 2's fixed three-task clean pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .workflow import CleanMultiAgentWorkflow


def main() -> None:
    """Run all pilot tasks and persist both per-run traces and a summary JSON file."""

    parser = argparse.ArgumentParser(description="Run the Phase 1 clean multi-agent pilot")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("phase1_mas/runs/clean_pilot"),
        help="Directory for one JSONL trace per task and clean_pilot_summary.json.",
    )
    args = parser.parse_args()
    # Traces are isolated by task so a future paired fault run can use the same
    # task ID and compare its own trace against exactly one clean counterpart.
    summary = CleanMultiAgentWorkflow().run_clean_pilot(output_dir=args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "clean_pilot_summary.json"
    summary_path.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**summary.as_dict(), "summary_path": str(summary_path)}, ensure_ascii=False, indent=2))
    if summary.success_count != summary.task_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
