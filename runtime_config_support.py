from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from runtime_support import ExecutionRuntime


def get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)


def get_env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def get_env_csv(name: str, default_values: list[str] | tuple[str, ...]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return list(default_values)
    return [item.strip() for item in str(raw).split(",") if item.strip()]


@dataclass(frozen=True)
class CycleExecutionSettings:
    btc_status_report_interval_hours: int
    allow_new_trend_entries_on_degraded: bool


def load_cycle_execution_settings() -> CycleExecutionSettings:
    return CycleExecutionSettings(
        btc_status_report_interval_hours=max(1, min(24, get_env_int("BTC_STATUS_REPORT_INTERVAL_HOURS", 24))),
        allow_new_trend_entries_on_degraded=get_env_bool(
            "TREND_POOL_ALLOW_NEW_ENTRIES_ON_DEGRADED",
            False,
        ),
    )


def build_live_runtime(
    *,
    now_utc: datetime | None = None,
    state_loader: Callable[..., Any] | None = None,
    state_writer: Callable[[dict[str, Any]], Any] | None = None,
    notifier: Callable[..., Any] | None = None,
) -> ExecutionRuntime:
    runtime_now = now_utc or datetime.now(timezone.utc)
    return ExecutionRuntime(
        dry_run=False,
        now_utc=runtime_now,
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        tg_token=os.getenv("TG_TOKEN", ""),
        tg_chat_id=os.getenv("TG_CHAT_ID", ""),
        state_loader=state_loader,
        state_writer=state_writer,
        notifier=notifier,
    )
