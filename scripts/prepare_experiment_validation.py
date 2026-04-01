from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts.prepare_auto_optimization_pr import parse_actions
except ModuleNotFoundError:  # pragma: no cover - script execution fallback
    from prepare_auto_optimization_pr import parse_actions


REPLAY_MARKERS = (
    "liquidity",
    "spread",
    "adv",
    "dca",
    "rotation",
    "gating",
    "replay",
)


def _combined_text(action: dict[str, object]) -> str:
    return f"{action.get('title', '')} {action.get('summary', '')}".lower()



def build_payload(issue_context: dict[str, object]) -> dict[str, object]:
    issue_number = int(issue_context["number"])
    issue_title = str(issue_context["title"]).strip()
    parsed_actions = parse_actions(str(issue_context.get("body", "")))
    experiment_actions = [action for action in parsed_actions if "experiment-only" in action.get("flags", [])]
    run_cycle_replay = bool(experiment_actions) and any(
        any(marker in _combined_text(action) for marker in REPLAY_MARKERS) for action in experiment_actions
    )

    should_run = bool(experiment_actions) and run_cycle_replay
    skip_reason = ""
    if not experiment_actions:
        skip_reason = "No experiment-only tasks were found in this monthly optimization issue."
    elif not should_run:
        skip_reason = "No supported downstream experiment validation target was found in the selected tasks."

    return {
        "issue_number": issue_number,
        "issue_title": issue_title,
        "should_run": should_run,
        "experiment_task_count": len(experiment_actions),
        "run_cycle_replay": run_cycle_replay,
        "experiment_actions": experiment_actions,
        "skip_reason": skip_reason,
    }



def render_task_summary(payload: dict[str, object]) -> str:
    lines = [
        "# Experiment Validation Candidate Tasks",
        "",
        f"- Issue: #{payload['issue_number']} {payload['issue_title']}",
        f"- Experiment-only tasks: `{payload['experiment_task_count']}`",
        f"- Cycle replay selected: `{str(payload['run_cycle_replay']).lower()}`",
    ]
    actions = payload["experiment_actions"]
    if not actions:
        lines.extend(["", payload["skip_reason"]])
        return "\n".join(lines).strip() + "\n"

    lines.extend(["", "## Selected Tasks"])
    for action in actions:
        flag_suffix = f" [{', '.join(action['flags'])}]" if action.get("flags") else ""
        lines.extend(
            [
                f"- `{action['risk_level']}` {action['title']}{flag_suffix}",
                f"  - Summary: {action.get('summary', 'No summary provided.')}",
                f"  - Source: {action.get('source_label', 'Unknown source')} ({action.get('source_url', 'n/a')})",
            ]
        )

    if payload["skip_reason"]:
        lines.extend(["", payload["skip_reason"]])
    return "\n".join(lines).strip() + "\n"



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare metadata for experiment-only monthly optimization validation.")
    parser.add_argument("--issue-context-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()



def main() -> int:
    args = parse_args()
    issue_context = json.loads(args.issue_context_file.read_text(encoding="utf-8"))
    payload = build_payload(issue_context)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload_file = args.output_dir / "payload.json"
    task_summary_file = args.output_dir / "task_summary.md"
    if payload["skip_reason"]:
        (args.output_dir / "skip_reason.txt").write_text(str(payload["skip_reason"]) + "\n", encoding="utf-8")
    payload_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    task_summary_file.write_text(render_task_summary(payload), encoding="utf-8")
    print(f"should_run={'true' if payload['should_run'] else 'false'}")
    print(f"issue_number={payload['issue_number']}")
    print(f"run_cycle_replay={'true' if payload['run_cycle_replay'] else 'false'}")
    print(f"payload_file={payload_file}")
    print(f"task_summary_file={task_summary_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
