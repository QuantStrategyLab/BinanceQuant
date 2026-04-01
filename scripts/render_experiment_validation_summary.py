from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any



def load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))



def build_summary_markdown(payload: dict[str, Any], replay_report: dict[str, Any] | None) -> str:
    lines = [
        "## Monthly Experiment Validation",
        "",
        f"- Issue: #{payload['issue_number']} {payload['issue_title']}",
        f"- Experiment-only tasks: `{payload['experiment_task_count']}`",
        f"- Validation executed: `{'yes' if payload['should_run'] else 'no'}`",
    ]
    if payload.get("skip_reason"):
        lines.append(f"- Skip reason: {payload['skip_reason']}")

    actions = payload.get("experiment_actions", [])
    if actions:
        lines.extend(["", "### Selected Tasks"])
        for action in actions:
            flags = f" [{', '.join(action['flags'])}]" if action.get("flags") else ""
            lines.extend(
                [
                    f"- `{action['risk_level']}` {action['title']}{flags}",
                    f"  - Summary: {action.get('summary', 'No summary provided.')}",
                ]
            )

    if replay_report is not None:
        side_effects = replay_report.get("side_effect_summary", {})
        gating_summary = replay_report.get("gating_summary", {})
        lines.extend(
            [
                "",
                "### Validation Results",
                f"- Replay status: `{replay_report.get('status', 'unknown')}`",
                f"- Dry run: `{str(replay_report.get('dry_run', False)).lower()}`",
                f"- Executed side effects: `{side_effects.get('executed_call_count', 0)}`",
                f"- Suppressed side effects: `{side_effects.get('suppressed_call_count', 0)}`",
                f"- Logged gating reasons: `{len(gating_summary)}`",
            ]
        )
        selected_symbols = replay_report.get("selected_symbols", {}).get("selected_candidates", [])
        if selected_symbols:
            lines.append(f"- Selected candidates: `{', '.join(selected_symbols)}`")
        if gating_summary:
            lines.extend(["", "### Gating Summary"])
            for gate, count in sorted(gating_summary.items()):
                lines.append(f"- `{gate}`: `{count}`")
    elif payload.get("should_run"):
        lines.extend(["", "### Validation Results", "- No replay report artifact was found."])

    return "\n".join(lines).strip() + "\n"



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render experiment validation markdown for the monthly optimization task issue.")
    parser.add_argument("--payload-file", required=True, type=Path)
    parser.add_argument("--output-file", required=True, type=Path)
    parser.add_argument("--replay-report-file", type=Path)
    return parser.parse_args()



def main() -> int:
    args = parse_args()
    payload = json.loads(args.payload_file.read_text(encoding="utf-8"))
    replay_report = load_optional_json(args.replay_report_file)
    args.output_file.write_text(build_summary_markdown(payload, replay_report), encoding="utf-8")
    print(f"summary_file={args.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
