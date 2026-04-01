from __future__ import annotations

import unittest

from scripts.prepare_auto_optimization_pr import build_payload, parse_actions, render_pr_body


class PrepareAutoOptimizationPrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issue_context = {
            "number": 15,
            "title": "Monthly Optimization Tasks · BinancePlatform: 2026-04-01 / 2026-03",
            "body": """# Monthly Optimization Tasks · BinancePlatform

## Actions
- [ ] `high` Reconcile March cash flows and open-position state
  - Summary: Pull Binance transaction history for March.
  - Source: [QuantStrategyLab/BinancePlatform #9](https://github.com/QuantStrategyLab/BinancePlatform/issues/9)
- [ ] `low` Add zero-trade diagnostics to the report [auto-pr-safe]
  - Summary: Include the top failed gating reason counts.
  - Source: [QuantStrategyLab/BinancePlatform #9](https://github.com/QuantStrategyLab/BinancePlatform/issues/9)
- [ ] `low` Add a boundary tracker [auto-pr-safe, experiment-only]
  - Summary: Track near-cutoff symbols monthly.
  - Source: [QuantStrategyLab/CryptoLeaderRotation #11](https://github.com/QuantStrategyLab/CryptoLeaderRotation/issues/11)
""",
        }

    def test_parse_actions_preserves_risk_flags_and_source(self) -> None:
        actions = parse_actions(self.issue_context["body"])

        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0]["risk_level"], "high")
        self.assertEqual(actions[1]["flags"], ["auto-pr-safe"])
        self.assertEqual(actions[2]["flags"], ["auto-pr-safe", "experiment-only"])
        self.assertEqual(actions[2]["source_label"], "QuantStrategyLab/CryptoLeaderRotation #11")

    def test_build_payload_selects_only_low_auto_pr_safe_actions(self) -> None:
        payload = build_payload(self.issue_context)

        self.assertTrue(payload["should_run"])
        self.assertEqual(payload["safe_task_count"], 2)
        self.assertEqual(payload["branch_name"], "automation/monthly-optimization-issue-15")
        self.assertEqual(payload["safe_actions"][0]["title"], "Add zero-trade diagnostics to the report")

    def test_render_pr_body_contains_marker_and_issue_reference(self) -> None:
        payload = build_payload(self.issue_context)
        body = render_pr_body(payload)

        self.assertIn("<!-- auto-optimization-pr:issue-15 -->", body)
        self.assertIn("Add zero-trade diagnostics to the report", body)
        self.assertIn("Refs #15", body)


if __name__ == "__main__":
    unittest.main()
