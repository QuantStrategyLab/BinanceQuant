from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONTHLY_REPORT_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "monthly_report.yml"
AI_REVIEW_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ai_review.yml"


class MonthlyReportWorkflowConfigTests(unittest.TestCase):
    def test_monthly_report_workflow_passes_hourly_dir_and_dispatches_ai_review(self) -> None:
        workflow = MONTHLY_REPORT_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("actions: write", workflow)
        self.assertIn('--hourly-dir "hourly/${{ steps.month.outputs.month }}"', workflow)
        self.assertIn("gh label create monthly-review", workflow)
        self.assertIn("gh workflow run ai_review.yml", workflow)
        self.assertIn('issue_number="${{ steps.issue.outputs.issue_number }}"', workflow)

    def test_ai_review_workflow_supports_manual_dispatch(self) -> None:
        workflow = AI_REVIEW_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("issue_number:", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("github_token: ${{ secrets.GITHUB_TOKEN }}", workflow)
        self.assertIn("${{ inputs.issue_number || github.event.issue.number }}", workflow)
        self.assertIn("Post your final bilingual review as a comment on that issue.", workflow)


if __name__ == "__main__":
    unittest.main()
