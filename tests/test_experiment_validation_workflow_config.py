from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "experiment_validation.yml"


class ExperimentValidationWorkflowConfigTests(unittest.TestCase):
    def test_workflow_runs_cycle_replay_and_posts_comment(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("issues:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("issue_number:", workflow)
        self.assertIn("prepare_experiment_validation.py", workflow)
        self.assertIn("run_cycle_replay.py", workflow)
        self.assertIn("render_experiment_validation_summary.py", workflow)
        self.assertIn("post_experiment_validation_comment.py", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("monthly-optimization-task", workflow)


if __name__ == "__main__":
    unittest.main()
