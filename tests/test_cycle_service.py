import json
import tempfile
import unittest

from application.cycle_service import run_live_cycle, write_execution_report


class CycleServiceTests(unittest.TestCase):
    def test_write_execution_report_persists_json(self):
        report = {"status": "ok", "log_lines": ["hello"], "value": 1}
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = write_execution_report(report, reports_dir=tmp_dir, filename="report.json")
            with open(output_path, "r") as handle:
                payload = json.load(handle)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["value"], 1)

    def test_run_live_cycle_writes_report_and_prints_logs(self):
        observed = {"printed": [], "built": 0}

        def fake_runtime_builder():
            observed["built"] += 1
            return object()

        def fake_execute_cycle(runtime):
            self.assertIsNotNone(runtime)
            return {"status": "ok", "log_lines": ["line-1", "line-2"]}

        with tempfile.TemporaryDirectory() as tmp_dir:
            report, output_path = run_live_cycle(
                runtime_builder=fake_runtime_builder,
                execute_cycle=fake_execute_cycle,
                output_printer=lambda text: observed["printed"].append(text),
                report_writer=lambda report: write_execution_report(
                    report,
                    reports_dir=tmp_dir,
                    filename="execution_report.json",
                ),
            )
            with open(output_path, "r") as handle:
                payload = json.load(handle)

        self.assertEqual(observed["built"], 1)
        self.assertEqual(observed["printed"], ["line-1\nline-2"])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(payload["log_lines"], ["line-1", "line-2"])

    def test_run_live_cycle_calls_exit_on_error(self):
        observed = {"exit_code": None}

        def fake_execute_cycle(_runtime):
            return {"status": "error", "log_lines": []}

        def fake_exit(code):
            observed["exit_code"] = code

        with tempfile.TemporaryDirectory() as tmp_dir:
            run_live_cycle(
                runtime_builder=lambda: object(),
                execute_cycle=fake_execute_cycle,
                output_printer=lambda _text: None,
                report_writer=lambda report: write_execution_report(
                    report,
                    reports_dir=tmp_dir,
                    filename="execution_report.json",
                ),
                exit_fn=fake_exit,
            )

        self.assertEqual(observed["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
