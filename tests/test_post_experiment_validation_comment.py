from __future__ import annotations

import unittest

from scripts.post_experiment_validation_comment import COMMENT_MARKER, build_comment_body


class PostExperimentValidationCommentTests(unittest.TestCase):
    def test_build_comment_body_includes_marker_and_run_link(self) -> None:
        body = build_comment_body(
            "Validation content",
            "https://github.com/example/repo/actions/runs/123",
        )

        self.assertIn(COMMENT_MARKER, body)
        self.assertIn("## Monthly Experiment Validation", body)
        self.assertIn("Validation content", body)
        self.assertIn("actions/runs/123", body)


if __name__ == "__main__":
    unittest.main()
