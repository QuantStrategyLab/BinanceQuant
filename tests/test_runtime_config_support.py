import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from runtime_config_support import build_live_runtime, load_cycle_execution_settings


class RuntimeConfigSupportTests(unittest.TestCase):
    def test_load_cycle_execution_settings_clamps_interval_and_reads_degraded_flag(self):
        with patch.dict(
            os.environ,
            {
                "BTC_STATUS_REPORT_INTERVAL_HOURS": "48",
                "TREND_POOL_ALLOW_NEW_ENTRIES_ON_DEGRADED": "1",
            },
            clear=False,
        ):
            settings = load_cycle_execution_settings()

        self.assertEqual(settings.btc_status_report_interval_hours, 24)
        self.assertTrue(settings.allow_new_trend_entries_on_degraded)

    def test_build_live_runtime_reads_env_and_preserves_injected_hooks(self):
        sentinel_now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        state_loader = object()
        state_writer = object()
        notifier = object()
        with patch.dict(
            os.environ,
            {
                "BINANCE_API_KEY": "api-key",
                "BINANCE_API_SECRET": "api-secret",
                "TG_TOKEN": "tg-token",
                "TG_CHAT_ID": "chat-id",
            },
            clear=False,
        ):
            runtime = build_live_runtime(
                now_utc=sentinel_now,
                state_loader=state_loader,
                state_writer=state_writer,
                notifier=notifier,
            )

        self.assertEqual(runtime.now_utc, sentinel_now)
        self.assertEqual(runtime.api_key, "api-key")
        self.assertEqual(runtime.api_secret, "api-secret")
        self.assertEqual(runtime.tg_token, "tg-token")
        self.assertEqual(runtime.tg_chat_id, "chat-id")
        self.assertIs(runtime.state_loader, state_loader)
        self.assertIs(runtime.state_writer, state_writer)
        self.assertIs(runtime.notifier, notifier)


if __name__ == "__main__":
    unittest.main()
