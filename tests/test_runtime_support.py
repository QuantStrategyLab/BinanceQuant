import unittest
from runtime_support import ExecutionRuntime, build_execution_report


class TestBuildExecutionReport(unittest.TestCase):
    def test_report_contains_enrichment_fields(self):
        runtime = ExecutionRuntime(dry_run=True, run_id="test-001")
        report = build_execution_report(runtime)
        self.assertIsNone(report["total_equity_usdt"])
        self.assertIsNone(report["trend_equity_usdt"])
        self.assertFalse(report["circuit_breaker_triggered"])
        self.assertIsNone(report["degraded_mode_level"])
        self.assertEqual(report["upstream_pool_symbols"], [])

    def test_report_preserves_existing_fields(self):
        runtime = ExecutionRuntime(dry_run=False, run_id="test-002")
        report = build_execution_report(runtime)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["run_id"], "test-002")
        self.assertFalse(report["dry_run"])
        self.assertIn("buy_sell_intents", report)
        self.assertIn("log_lines", report)
