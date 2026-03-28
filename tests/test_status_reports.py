import unittest
from types import SimpleNamespace

from reporting.status_reports import (
    append_rotation_summary,
    build_btc_manual_hint,
    get_periodic_report_bucket,
)


def fake_translate(key, **kwargs):
    if key == "rotation_no_upstream_pool":
        return "no upstream pool"
    if key == "rotation_no_execution_pool":
        return "no execution pool"
    if key == "rotation_no_candidates":
        return "no candidates"
    if key == "rotation_upstream_official_monthly_pool":
        return f"upstream={kwargs['pool_text']}"
    if key == "rotation_current_execution_pool":
        return f"execution={kwargs['pool_text']}"
    if key == "rotation_current_execution_pool_size":
        return f"size={kwargs['pool_size']}"
    if key == "rotation_current_execution_targets":
        return f"targets={kwargs['target_text']}"
    if key == "manual_hint_deep_value":
        return "deep"
    if key == "manual_hint_low_value":
        return "low"
    if key == "manual_hint_profit_taking":
        return "take-profit"
    if key == "manual_hint_near_profit_taking":
        return "near-profit"
    if key == "manual_hint_neutral":
        return "neutral"
    raise KeyError(key)


class StatusReportTests(unittest.TestCase):
    def test_get_periodic_report_bucket_respects_interval(self):
        now_utc = SimpleNamespace(hour=8, strftime=lambda fmt: "20260329")
        self.assertEqual(get_periodic_report_bucket(now_utc, 4), "2026032908")
        self.assertEqual(get_periodic_report_bucket(now_utc, 3), "")

    def test_build_btc_manual_hint_uses_thresholds(self):
        self.assertEqual(
            build_btc_manual_hint(
                {"ahr999": 0.3, "zscore": 1.0, "sell_trigger": 3.0},
                translate_fn=fake_translate,
            ),
            "deep",
        )
        self.assertEqual(
            build_btc_manual_hint(
                {"ahr999": 0.7, "zscore": 1.0, "sell_trigger": 3.0},
                translate_fn=fake_translate,
            ),
            "low",
        )
        self.assertEqual(
            build_btc_manual_hint(
                {"ahr999": 1.2, "zscore": 3.1, "sell_trigger": 3.0},
                translate_fn=fake_translate,
            ),
            "take-profit",
        )

    def test_append_rotation_summary_formats_expected_lines(self):
        log_buffer = []
        append_rotation_summary(
            log_buffer,
            ["ETHUSDT", "SOLUSDT"],
            ["ETHUSDT"],
            {},
            append_log_fn=lambda buffer, text: buffer.append(text),
            translate_fn=fake_translate,
        )
        self.assertEqual(
            log_buffer,
            [
                "upstream=ETHUSDT, SOLUSDT",
                "execution=ETHUSDT",
                "size=1",
                "targets=no candidates",
            ],
        )


if __name__ == "__main__":
    unittest.main()
