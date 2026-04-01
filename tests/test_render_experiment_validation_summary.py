from __future__ import annotations

import unittest

from scripts.render_experiment_validation_summary import build_summary_markdown


class RenderExperimentValidationSummaryTests(unittest.TestCase):
    def test_build_summary_includes_replay_details(self) -> None:
        payload = {
            "issue_number": 15,
            "issue_title": "Monthly Optimization Tasks · BinancePlatform",
            "should_run": True,
            "experiment_task_count": 1,
            "experiment_actions": [
                {
                    "risk_level": "medium",
                    "title": "Add downstream liquidity validation for TAOUSDT-class assets",
                    "flags": ["experiment-only"],
                    "summary": "Ensure BinancePlatform applies its own ADV/spread checks.",
                }
            ],
            "skip_reason": "",
        }
        replay_report = {
            "status": "ok",
            "dry_run": True,
            "side_effect_summary": {"executed_call_count": 0, "suppressed_call_count": 3},
            "gating_summary": {"trend_buy_below_min_budget": 1},
            "selected_symbols": {"selected_candidates": ["ETHUSDT", "SOLUSDT"]},
        }

        summary = build_summary_markdown(payload, replay_report)

        self.assertIn("Monthly Experiment Validation", summary)
        self.assertIn("Replay status", summary)
        self.assertIn("trend_buy_below_min_budget", summary)
        self.assertIn("ETHUSDT, SOLUSDT", summary)


if __name__ == "__main__":
    unittest.main()
