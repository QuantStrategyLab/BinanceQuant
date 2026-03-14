import sys
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import run_monthly_ai_briefing


class MonthlyAiBriefingTests(unittest.TestCase):
    def build_inputs(self):
        return {
            "upstream": {
                "monthly_shadow_build_summary": {
                    "as_of_date": "2026-03-13",
                    "official_baseline": {
                        "version": "2026-03-13-core_major",
                        "mode": "core_major",
                    },
                },
                "upstream_track_summary": pd.DataFrame(
                    [
                        {
                            "track_id": "official_baseline",
                            "profile_name": "baseline_blended_rank",
                            "source_track": "official_baseline",
                            "candidate_status": "official_reference",
                            "release_count": 64,
                            "last_as_of_date": "2026-03-13",
                            "release_index_path": "official/release_index.csv",
                        },
                        {
                            "track_id": "challenger_topk_60",
                            "profile_name": "challenger_topk_60",
                            "source_track": "shadow_candidate",
                            "candidate_status": "shadow_candidate",
                            "release_count": 64,
                            "last_as_of_date": "2026-03-13",
                            "release_index_path": "challenger/release_index.csv",
                        },
                    ]
                ),
                "live_pool": {
                    "source_project": "crypto-leader-rotation",
                    "version": "2026-03-13-core_major",
                    "mode": "core_major",
                    "pool_size": 5,
                },
                "release_manifest": {"version": "2026-03-13-core_major"},
                "paths": {
                    "monthly_shadow_build_summary": "upstream/monthly_shadow_build_summary.json",
                    "upstream_track_summary": "upstream/track_summary.csv",
                    "live_pool": "upstream/live_pool.json",
                    "release_manifest": "upstream/release_manifest.json",
                },
            },
            "downstream": {
                "track_summary": pd.DataFrame(
                    [
                        {
                            "track_id": "official_baseline",
                            "static_pct": 0.2,
                            "last_known_good_pct": 0.0,
                        },
                        {
                            "track_id": "challenger_topk_60",
                            "static_pct": 0.2,
                            "last_known_good_pct": 0.0,
                        },
                    ]
                ),
                "side_by_side": pd.DataFrame(
                    [
                        {
                            "baseline_cagr": 0.06,
                            "challenger_cagr": 0.34,
                            "baseline_sharpe": 0.37,
                            "challenger_sharpe": 0.81,
                            "baseline_max_drawdown": -0.81,
                            "challenger_max_drawdown": -0.63,
                            "baseline_turnover": 26.0,
                            "challenger_turnover": 25.9,
                            "delta_cagr": 0.28,
                            "delta_sharpe": 0.44,
                            "delta_max_drawdown": 0.18,
                            "delta_turnover": -0.1,
                        }
                    ]
                ),
                "watchlist": pd.DataFrame(
                    [
                        {
                            "recent_12_month_outperformance_rate": 0.33,
                            "recent_6_month_outperformance_rate": 0.17,
                            "top_5_positive_excess_share": 0.67,
                            "risk_off_excess_vs_baseline": 0.32,
                            "lag_sensitivity_status": "pass",
                            "friction_sensitivity_status": "pass",
                            "recommendation": "continue observation",
                        }
                    ]
                ),
                "sensitivity": pd.DataFrame(
                    [
                        {"scenario": "lag_1", "delta_cagr": 0.28, "delta_sharpe": 0.44},
                        {"scenario": "cost_10bps", "delta_cagr": 0.20, "delta_sharpe": 0.30},
                    ]
                ),
                "concentration": pd.DataFrame(
                    [
                        {
                            "profile": "challenger_topk_60",
                            "months_outperforming": 24,
                            "months_compared": 63,
                            "top_5_positive_excess_share": 0.67,
                        }
                    ]
                ),
                "regime": pd.DataFrame(),
                "paths": {
                    "track_summary": "reports/shadow_candidate_track_summary.csv",
                    "side_by_side": "reports/shadow_candidate_side_by_side_summary.csv",
                    "watchlist": "reports/shadow_candidate_promotion_watchlist.csv",
                    "sensitivity": "reports/shadow_candidate_sensitivity_summary.csv",
                    "concentration": "reports/shadow_candidate_concentration_summary.csv",
                    "regime": "reports/shadow_candidate_regime_summary.csv",
                },
            },
        }

    def test_build_briefing_payload_marks_shadow_only_and_reporting_only(self):
        payload = run_monthly_ai_briefing.build_briefing_payload(self.build_inputs())

        self.assertEqual(payload["system_status"]["baseline_role"], "official_live_reference")
        self.assertEqual(payload["system_status"]["challenger_role"], "shadow_only_candidate")
        self.assertEqual(payload["recommendation"]["briefing_category"], "continue observation")
        self.assertTrue(payload["recommendation"]["reporting_only"])
        self.assertEqual(payload["overall_status"], "caution_observation")

    def test_rendered_outputs_include_required_guardrails(self):
        payload = run_monthly_ai_briefing.build_briefing_payload(self.build_inputs())

        review_md = run_monthly_ai_briefing.render_review_markdown(payload)
        prompt_md = run_monthly_ai_briefing.render_chatgpt_prompt(payload)

        self.assertIn("Baseline remains the official/live reference.", review_md)
        self.assertIn("`challenger_topk_60` remains shadow-only.", review_md)
        self.assertIn("This is reporting-only. It is not a switch instruction.", review_md)
        self.assertIn("No production switch has happened.", prompt_md)
        self.assertIn("The current output is reporting-only", prompt_md)


if __name__ == "__main__":
    unittest.main()
