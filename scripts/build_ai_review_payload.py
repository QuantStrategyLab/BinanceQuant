from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "2026-04-02"
REPO_ROLE_BY_KIND = {
    "upstream_selector": "upstream_selector_review",
    "execution_runtime": "execution_runtime_review",
}


def build_review_payload(
    *,
    source_repo: str,
    review_kind: str,
    issue_context: dict[str, Any],
    secondary_review: dict[str, Any],
    run_url: str,
) -> dict[str, Any]:
    if review_kind not in REPO_ROLE_BY_KIND:
        raise ValueError(f"Unsupported review kind: {review_kind}")

    issue_number = int(issue_context["number"])
    issue_title = str(issue_context["title"]).strip()
    issue_url = f"https://github.com/{source_repo}/issues/{issue_number}"

    return {
        "schema_version": SCHEMA_VERSION,
        "source_repo": source_repo,
        "repo_role": REPO_ROLE_BY_KIND[review_kind],
        "review_kind": review_kind,
        "source_issue": {
            "number": issue_number,
            "title": issue_title,
            "url": issue_url,
        },
        "run_url": run_url.strip(),
        "primary_reviewer": {
            "provider": "anthropic",
            "display_name": "Claude Primary Review",
        },
        "secondary_reviewer": {
            "provider": secondary_review["provider"],
            "display_name": secondary_review["provider_display_name"],
            "model": secondary_review["model"],
        },
        "verdict": secondary_review["verdict"],
        "risk_level": secondary_review["risk_level"],
        "production_recommendation": secondary_review["production_recommendation"],
        "summary": secondary_review["summary"],
        "key_findings": list(secondary_review.get("key_findings", [])),
        "recommended_actions": list(secondary_review.get("recommended_actions", [])),
        "follow_up_checks": list(secondary_review.get("follow_up_checks", [])),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the normalized final AI review payload used by downstream planners.",
    )
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--review-kind", required=True, choices=sorted(REPO_ROLE_BY_KIND))
    parser.add_argument("--issue-context-file", required=True, type=Path)
    parser.add_argument("--secondary-review-file", required=True, type=Path)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--output-file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    issue_context = json.loads(args.issue_context_file.read_text(encoding="utf-8"))
    secondary_review = json.loads(args.secondary_review_file.read_text(encoding="utf-8"))
    payload = build_review_payload(
        source_repo=args.source_repo,
        review_kind=args.review_kind,
        issue_context=issue_context,
        secondary_review=secondary_review,
        run_url=args.run_url,
    )
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"final_review_payload={args.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
