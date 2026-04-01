from __future__ import annotations

import unittest

from scripts.build_ai_review_payload import SCHEMA_VERSION, build_review_payload


class BuildAiReviewPayloadTests(unittest.TestCase):
    def test_build_review_payload_carries_standardized_root_fields(self) -> None:
        payload = build_review_payload(
            source_repo="QuantStrategyLab/BinancePlatform",
            review_kind="execution_runtime",
            issue_context={"number": 9, "title": "Monthly Execution Review: 2026-03"},
            secondary_review={
                "provider": "openai",
                "provider_display_name": "GPT Secondary Review",
                "model": "gpt-5.4-mini",
                "verdict": "agree",
                "risk_level": "low",
                "production_recommendation": "keep_production_as_is",
                "summary": "Looks consistent.",
                "key_findings": ["No blocking issue found."],
                "recommended_actions": [],
                "follow_up_checks": [],
            },
            run_url="https://github.com/example/repo/actions/runs/1",
        )

        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["repo_role"], "execution_runtime_review")
        self.assertEqual(payload["source_issue"]["url"], "https://github.com/QuantStrategyLab/BinancePlatform/issues/9")
        self.assertEqual(payload["secondary_reviewer"]["model"], "gpt-5.4-mini")


if __name__ == "__main__":
    unittest.main()
