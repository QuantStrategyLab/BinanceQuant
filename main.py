"""Live strategy orchestration entrypoint.

The live cycle remains here so the execution flow is easy to follow in one file.
Pure strategy math, state normalization, upstream contract handling, exchange
helpers, and live service adapters live in dedicated modules.
"""

import os
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
import traceback
from entrypoints.cli import run_cli_entrypoint
from notify_i18n_support import translate as t
from degraded_mode_support import (
    format_trend_pool_source_logs as dm_format_trend_pool_source_logs,
    load_trend_universe_from_live_pool as dm_load_trend_universe_from_live_pool,
    resolve_trend_pool_source as dm_resolve_trend_pool_source,
    update_trend_pool_state as dm_update_trend_pool_state,
)
from quant_platform_kit.binance import (
    connect_client as qpk_connect_client,
    ensure_asset_available as qpk_ensure_asset_available,
    fetch_btc_market_snapshot as qpk_fetch_btc_market_snapshot,
    fetch_daily_indicators as qpk_fetch_daily_indicators,
    format_qty as qpk_format_qty,
    get_total_balance as qpk_get_total_balance,
    manage_usdt_earn_buffer as qpk_manage_usdt_earn_buffer,
)
from live_services import (
    get_firestore_client as live_get_firestore_client,
    get_state_doc_ref as live_get_state_doc_ref,
    load_trade_state as live_load_trade_state,
    save_trade_state as live_save_trade_state,
    send_tg_msg as live_send_tg_msg,
)
from market_snapshot_support import (
    capture_market_snapshot as ms_capture_market_snapshot,
)
from runtime_support import (
    ExecutionRuntime,
    append_report_error,
    build_execution_report,
    next_order_id,
    runtime_call_client,
    runtime_notify,
    runtime_set_trade_state,
)
from runtime_config_support import (
    build_live_runtime as rc_build_live_runtime,
    get_env_bool as rc_get_env_bool,
    get_env_csv as rc_get_env_csv,
    get_env_int as rc_get_env_int,
    load_cycle_execution_settings as rc_load_cycle_execution_settings,
)
from reporting.status_reports import (
    append_portfolio_report as report_append_portfolio_report,
    append_rotation_summary as report_append_rotation_summary,
    append_trend_symbol_status as report_append_trend_symbol_status,
    build_btc_manual_hint as report_build_btc_manual_hint,
    get_periodic_report_bucket as report_get_periodic_report_bucket,
    maybe_send_periodic_btc_status_report as report_maybe_send_periodic_btc_status_report,
)
from strategy_core import (
    DEFAULT_POOL_SCORE_WEIGHTS,
    allocate_trend_buy_budget as shared_allocate_trend_buy_budget,
    build_stable_quality_pool as shared_build_stable_quality_pool,
    compute_allocation_budgets,
    get_dynamic_btc_base_order as shared_get_dynamic_btc_base_order,
    get_dynamic_btc_target_ratio as shared_get_dynamic_btc_target_ratio,
    rank_normalize as shared_rank_normalize,
    select_rotation_weights as shared_select_rotation_weights,
)
from trade_state_support import (
    build_default_state as ts_build_default_state,
    default_trend_symbol_state as ts_default_trend_symbol_state,
    get_runtime_trend_universe as ts_get_runtime_trend_universe,
    get_symbol_trade_state as ts_get_symbol_trade_state,
    has_active_position as ts_has_active_position,
    infer_base_asset as ts_infer_base_asset,
    is_trend_symbol_state as ts_is_trend_symbol_state,
    normalize_symbol_state as ts_normalize_symbol_state,
    normalize_trade_state as ts_normalize_trade_state,
    record_trend_action as ts_record_trend_action,
    safe_float as ts_safe_float,
    set_symbol_trade_state as ts_set_symbol_trade_state,
    should_skip_duplicate_trend_action as ts_should_skip_duplicate_trend_action,
)
from trend_pool_support import (
    build_static_trend_pool_resolution as tp_build_static_trend_pool_resolution,
    build_trend_pool_resolution as tp_build_trend_pool_resolution,
    extract_trend_pool_symbols as tp_extract_trend_pool_symbols,
    get_default_live_pool_candidates as tp_get_default_live_pool_candidates,
    get_last_known_good_trend_pool as tp_get_last_known_good_trend_pool,
    get_trend_pool_contract_settings as tp_get_trend_pool_contract_settings,
    load_trend_pool_from_file as tp_load_trend_pool_from_file,
    load_trend_pool_from_firestore as tp_load_trend_pool_from_firestore,
    parse_trend_pool_date as tp_parse_trend_pool_date,
    parse_trend_universe_mapping as tp_parse_trend_universe_mapping,
    validate_trend_pool_payload as tp_validate_trend_pool_payload,
)

SEPARATOR = "━━━━━━━━━━━━━━━━━━"


STATIC_TREND_UNIVERSE = {
    "ETHUSDT": {"base_asset": "ETH"},
    "SOLUSDT": {"base_asset": "SOL"},
    "XRPUSDT": {"base_asset": "XRP"},
    "LINKUSDT": {"base_asset": "LINK"},
    "AVAXUSDT": {"base_asset": "AVAX"},
    "ADAUSDT": {"base_asset": "ADA"},
    "DOGEUSDT": {"base_asset": "DOGE"},
    "TRXUSDT": {"base_asset": "TRX"},
    "ATOMUSDT": {"base_asset": "ATOM"},
    "LTCUSDT": {"base_asset": "LTC"},
    "BCHUSDT": {"base_asset": "BCH"},
}
TREND_UNIVERSE = STATIC_TREND_UNIVERSE.copy()

TREND_POOL_SIZE = 5
ROTATION_TOP_N = 2
MIN_HISTORY_DAYS = 365
MIN_AVG_QUOTE_VOL_180 = 8_000_000
POOL_MEMBERSHIP_BONUS = 0.10

BNB_FUEL_SYMBOL = "BNBUSDT"
BNB_FUEL_ASSET = "BNB"

DEFAULT_LIVE_POOL_LEGACY_PATH = (
    Path(__file__).resolve().parents[1]
    / "CryptoLeaderRotation"
    / "data"
    / "output"
    / "live_pool_legacy.json"
)
DEFAULT_TREND_POOL_FIRESTORE_COLLECTION = "strategy"
DEFAULT_TREND_POOL_FIRESTORE_DOCUMENT = "CRYPTO_LEADER_ROTATION_LIVE_POOL"
RETIRED_TREND_POSITIONS_KEY = "retired_trend_positions"
TREND_POOL_LAST_GOOD_PAYLOAD_KEY = "trend_pool_last_good_payload"
TREND_POOL_ACTION_HISTORY_KEY = "trend_action_history"
DEFAULT_TREND_POOL_MAX_AGE_DAYS = 45
DEFAULT_TREND_POOL_ACCEPTABLE_MODES = ("core_major",)


class BalanceFetchError(RuntimeError):
    pass


def get_env_int(name, default):
    return rc_get_env_int(name, default)


def get_env_bool(name, default=False):
    return rc_get_env_bool(name, default)


def get_env_csv(name, default_values):
    return rc_get_env_csv(name, default_values)


def default_trend_symbol_state():
    return ts_default_trend_symbol_state()


def safe_float(value, default=0.0):
    return ts_safe_float(value, default)


def infer_base_asset(symbol):
    return ts_infer_base_asset(symbol)


def is_trend_symbol_state(value):
    return ts_is_trend_symbol_state(value)


def normalize_symbol_state(value):
    return ts_normalize_symbol_state(value)


def has_active_position(position_state):
    return ts_has_active_position(position_state)


def parse_trend_pool_date(value):
    return tp_parse_trend_pool_date(value)


def parse_trend_universe_mapping(payload):
    return tp_parse_trend_universe_mapping(payload)


def extract_trend_pool_symbols(payload, symbol_map):
    return tp_extract_trend_pool_symbols(payload, symbol_map)


def get_trend_pool_contract_settings():
    return tp_get_trend_pool_contract_settings(
        max_age_days_default=DEFAULT_TREND_POOL_MAX_AGE_DAYS,
        acceptable_modes_default=DEFAULT_TREND_POOL_ACCEPTABLE_MODES,
        expected_pool_size_default=TREND_POOL_SIZE,
    )


def validate_trend_pool_payload(
    payload,
    source_label,
    *,
    now_utc=None,
    max_age_days=DEFAULT_TREND_POOL_MAX_AGE_DAYS,
    acceptable_modes=None,
    expected_pool_size=TREND_POOL_SIZE,
    enforce_freshness=True,
):
    return tp_validate_trend_pool_payload(
        payload,
        source_label,
        now_utc=now_utc,
        max_age_days=max_age_days,
        acceptable_modes=acceptable_modes,
        expected_pool_size=expected_pool_size,
        enforce_freshness=enforce_freshness,
    )


def get_default_live_pool_candidates():
    return tp_get_default_live_pool_candidates(DEFAULT_LIVE_POOL_LEGACY_PATH)


def get_firestore_client():
    return live_get_firestore_client()


def get_state_doc_ref():
    return live_get_state_doc_ref(collection="strategy", document="MULTI_ASSET_STATE")


def load_trend_pool_from_firestore(*, now_utc=None, settings=None):
    return tp_load_trend_pool_from_firestore(
        now_utc=now_utc,
        settings=settings or get_trend_pool_contract_settings(),
        default_collection=DEFAULT_TREND_POOL_FIRESTORE_COLLECTION,
        default_document=DEFAULT_TREND_POOL_FIRESTORE_DOCUMENT,
    )


def load_trend_pool_from_file(path, *, now_utc=None, settings=None):
    return tp_load_trend_pool_from_file(
        path,
        now_utc=now_utc,
        settings=settings or get_trend_pool_contract_settings(),
    )


def build_trend_pool_resolution(validated_payload, *, source_kind, degraded, now_utc=None, messages=None):
    return tp_build_trend_pool_resolution(
        validated_payload,
        source_kind=source_kind,
        degraded=degraded,
        now_utc=now_utc,
        messages=messages,
    )


def get_last_known_good_trend_pool(state, *, now_utc=None, settings=None):
    return tp_get_last_known_good_trend_pool(
        state,
        now_utc=now_utc,
        settings=settings or get_trend_pool_contract_settings(),
        last_good_payload_key=TREND_POOL_LAST_GOOD_PAYLOAD_KEY,
    )


def build_static_trend_pool_resolution(*, now_utc=None, messages=None):
    return tp_build_static_trend_pool_resolution(
        now_utc=now_utc,
        messages=messages,
        static_trend_universe=STATIC_TREND_UNIVERSE,
    )


def resolve_trend_pool_source(state=None, *, now_utc=None):
    return dm_resolve_trend_pool_source(
        state=state,
        now_utc=now_utc,
        default_live_pool_legacy_path=DEFAULT_LIVE_POOL_LEGACY_PATH,
        default_firestore_collection=DEFAULT_TREND_POOL_FIRESTORE_COLLECTION,
        default_firestore_document=DEFAULT_TREND_POOL_FIRESTORE_DOCUMENT,
        last_good_payload_key=TREND_POOL_LAST_GOOD_PAYLOAD_KEY,
        static_trend_universe=STATIC_TREND_UNIVERSE,
        max_age_days_default=DEFAULT_TREND_POOL_MAX_AGE_DAYS,
        acceptable_modes_default=DEFAULT_TREND_POOL_ACCEPTABLE_MODES,
        expected_pool_size_default=TREND_POOL_SIZE,
    )


def load_trend_universe_from_live_pool(state=None, *, now_utc=None):
    return dm_load_trend_universe_from_live_pool(
        state=state,
        now_utc=now_utc,
        default_live_pool_legacy_path=DEFAULT_LIVE_POOL_LEGACY_PATH,
        default_firestore_collection=DEFAULT_TREND_POOL_FIRESTORE_COLLECTION,
        default_firestore_document=DEFAULT_TREND_POOL_FIRESTORE_DOCUMENT,
        last_good_payload_key=TREND_POOL_LAST_GOOD_PAYLOAD_KEY,
        static_trend_universe=STATIC_TREND_UNIVERSE,
        max_age_days_default=DEFAULT_TREND_POOL_MAX_AGE_DAYS,
        acceptable_modes_default=DEFAULT_TREND_POOL_ACCEPTABLE_MODES,
        expected_pool_size_default=TREND_POOL_SIZE,
    )


def update_trend_pool_state(state, resolution):
    dm_update_trend_pool_state(
        state,
        resolution,
        last_good_payload_key=TREND_POOL_LAST_GOOD_PAYLOAD_KEY,
    )


def build_default_state():
    return ts_build_default_state(
        trend_universe=TREND_UNIVERSE,
        last_good_payload_key=TREND_POOL_LAST_GOOD_PAYLOAD_KEY,
        action_history_key=TREND_POOL_ACTION_HISTORY_KEY,
        retired_positions_key=RETIRED_TREND_POSITIONS_KEY,
    )


def normalize_trade_state(state):
    return ts_normalize_trade_state(
        state,
        trend_universe=TREND_UNIVERSE,
        last_good_payload_key=TREND_POOL_LAST_GOOD_PAYLOAD_KEY,
        action_history_key=TREND_POOL_ACTION_HISTORY_KEY,
        retired_positions_key=RETIRED_TREND_POSITIONS_KEY,
    )


def get_runtime_trend_universe(state):
    return ts_get_runtime_trend_universe(
        state,
        trend_universe=TREND_UNIVERSE,
        retired_positions_key=RETIRED_TREND_POSITIONS_KEY,
    )


def get_symbol_trade_state(state, symbol):
    return ts_get_symbol_trade_state(
        state,
        symbol,
        trend_universe=TREND_UNIVERSE,
        retired_positions_key=RETIRED_TREND_POSITIONS_KEY,
    )


def set_symbol_trade_state(state, symbol, symbol_state):
    ts_set_symbol_trade_state(
        state,
        symbol,
        symbol_state,
        trend_universe=TREND_UNIVERSE,
        retired_positions_key=RETIRED_TREND_POSITIONS_KEY,
    )


def should_skip_duplicate_trend_action(state, symbol, action, action_date):
    return ts_should_skip_duplicate_trend_action(
        state,
        symbol,
        action,
        action_date,
        action_history_key=TREND_POOL_ACTION_HISTORY_KEY,
    )


def record_trend_action(state, symbol, action, action_date):
    ts_record_trend_action(
        state,
        symbol,
        action,
        action_date,
        action_history_key=TREND_POOL_ACTION_HISTORY_KEY,
    )

# ==========================================
# 1. State persistence and Telegram
# ==========================================
def get_trade_state(normalize=True):
    return live_load_trade_state(
        normalize_fn=normalize_trade_state,
        default_state_factory=build_default_state,
        normalize=normalize,
        collection="strategy",
        document="MULTI_ASSET_STATE",
    )


def set_trade_state(data):
    live_save_trade_state(
        data,
        normalize_fn=normalize_trade_state,
        collection="strategy",
        document="MULTI_ASSET_STATE",
    )


def append_log(log_buffer, message):
    if log_buffer is not None:
        log_buffer.append(str(message))


def send_tg_msg(token, chat_id, text):
    live_send_tg_msg(token, chat_id, text)

# ==========================================
# 2. Earn and balance helpers
# ==========================================
def get_total_balance(client, asset, log_buffer=None):
    """Total balance for asset (spot + flexible earn)."""
    return qpk_get_total_balance(
        client,
        asset,
        on_spot_error=lambda exc: append_log(log_buffer, t("spot_balance_lookup_failed", asset=asset, error=exc)),
        on_earn_error=lambda exc: append_log(log_buffer, t("earn_balance_lookup_failed", asset=asset, error=exc)),
        balance_error_cls=BalanceFetchError,
    )


def log_and_notify(log_buffer, tg_token, tg_chat_id, text):
    append_log(log_buffer, text)
    send_tg_msg(tg_token, tg_chat_id, text)

def ensure_asset_available(client, asset, required_amount, tg_token, tg_chat_id):
    """Redeem from flexible earn if spot balance is below required amount."""
    return qpk_ensure_asset_available(
        client,
        asset,
        required_amount,
        on_redeem=lambda amount: send_tg_msg(
            tg_token,
            tg_chat_id,
            t("execution_spot_short_redeeming_from_earn", asset=asset, amount=amount),
        ),
        on_error=lambda exc: send_tg_msg(
            tg_token,
            tg_chat_id,
            t("execution_redeem_failed_asset", asset=asset, error=exc),
        ),
        sleep_fn=time.sleep,
    )

def manage_usdt_earn_buffer(client, target_buffer, tg_token, tg_chat_id, log_buffer):
    """Keep USDT spot buffer near target by subscribing/redeeming flexible earn."""
    qpk_manage_usdt_earn_buffer(
        client,
        target_buffer,
        on_subscribe=lambda amount: append_log(log_buffer, t("cash_manager_subscribed_to_earn", amount=amount)),
        on_redeem=lambda amount: append_log(log_buffer, t("cash_manager_redeeming_to_spot", amount=amount)),
        on_error=lambda exc: append_log(log_buffer, t("usdt_earn_buffer_maintenance_failed", error=exc)),
    )

def format_qty(client, symbol, qty):
    """Round quantity to exchange LOT_SIZE to avoid filter errors."""
    return qpk_format_qty(client, symbol, qty)

def get_dynamic_btc_target_ratio(total_equity):
    """BTC target weight increases with equity; no hard minimum."""
    return shared_get_dynamic_btc_target_ratio(total_equity)


def get_dynamic_btc_base_order(total_equity):
    """Daily DCA base order scales with total equity."""
    return shared_get_dynamic_btc_base_order(total_equity)


def get_periodic_report_bucket(now_utc, interval_hours):
    return report_get_periodic_report_bucket(now_utc, interval_hours)


def build_btc_manual_hint(btc_snapshot):
    return report_build_btc_manual_hint(btc_snapshot, translate_fn=t)


def maybe_send_periodic_btc_status_report(
    state,
    tg_token,
    tg_chat_id,
    now_utc,
    interval_hours,
    total_equity,
    trend_holdings_equity,
    trend_daily_pnl,
    btc_price,
    btc_snapshot,
    btc_target_ratio,
    notifier_fn=None,
):
    return report_maybe_send_periodic_btc_status_report(
        state,
        tg_token,
        tg_chat_id,
        now_utc,
        interval_hours,
        total_equity,
        trend_holdings_equity,
        trend_daily_pnl,
        btc_price,
        btc_snapshot,
        btc_target_ratio,
        translate_fn=t,
        separator=SEPARATOR,
        notifier_fn=notifier_fn,
        send_tg_msg_fn=send_tg_msg,
    )


def fetch_daily_indicators(client, symbol, lookback_days=420):
    """Daily indicators for one symbol (rotation and risk)."""
    return qpk_fetch_daily_indicators(client, symbol, lookback_days=lookback_days)


def fetch_btc_market_snapshot(client, btc_price, lookback_days=700, log_buffer=None):
    """Single BTC daily series for DCA and trend gate."""
    return qpk_fetch_btc_market_snapshot(
        client,
        btc_price,
        lookback_days=lookback_days,
        on_fetch_error=lambda exc: log_buffer.append(t("btc_daily_fetch_failed", error=exc)) if log_buffer is not None else None,
        on_empty=lambda: log_buffer.append(t("btc_daily_data_empty")) if log_buffer is not None else None,
        on_insufficient=lambda length, last_time: log_buffer.append(
            t("btc_data_insufficient", length=length, last_time=last_time)
        )
        if log_buffer is not None
        else None,
    )


def rank_normalize(values):
    return shared_rank_normalize(values)


def build_stable_quality_pool(indicators_map, btc_snapshot, previous_pool):
    return shared_build_stable_quality_pool(
        indicators_map,
        btc_snapshot,
        previous_pool,
        pool_size=TREND_POOL_SIZE,
        min_history_days=MIN_HISTORY_DAYS,
        min_avg_quote_vol_180=MIN_AVG_QUOTE_VOL_180,
        membership_bonus=POOL_MEMBERSHIP_BONUS,
        score_weights=DEFAULT_POOL_SCORE_WEIGHTS,
    )


def _set_rotation_pool_lock(state, *, source_version, source_as_of_date, now_utc):
    locked_version = str(source_version or "").strip()
    locked_as_of_date = str(source_as_of_date or "").strip()
    state["rotation_pool_source_version"] = locked_version
    state["rotation_pool_source_as_of_date"] = locked_as_of_date
    if locked_as_of_date:
        state["rotation_pool_last_month"] = locked_as_of_date[:7]
    else:
        state["rotation_pool_last_month"] = (now_utc or datetime.now(timezone.utc)).strftime("%Y-%m")


def refresh_rotation_pool(state, indicators_map, btc_snapshot, allow_refresh=True, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    available_symbols = set(TREND_UNIVERSE)
    cached_pool = [symbol for symbol in state.get("rotation_pool_symbols", []) if symbol in available_symbols]
    current_source_version = str(state.get("trend_pool_version", "")).strip()
    current_source_as_of_date = str(state.get("trend_pool_as_of_date", "")).strip()
    locked_source_version = str(state.get("rotation_pool_source_version", "")).strip()
    locked_source_as_of_date = str(state.get("rotation_pool_source_as_of_date", "")).strip()
    current_source_month = current_source_as_of_date[:7] if current_source_as_of_date else ""
    legacy_locked_month = str(state.get("rotation_pool_last_month", "")).strip()

    if not allow_refresh and cached_pool:
        _set_rotation_pool_lock(
            state,
            source_version=current_source_version,
            source_as_of_date=current_source_as_of_date,
            now_utc=now_utc,
        )
        state["rotation_pool_symbols"] = cached_pool
        return cached_pool, []

    if (
        cached_pool
        and (locked_source_version or locked_source_as_of_date)
        and locked_source_version == current_source_version
        and locked_source_as_of_date == current_source_as_of_date
    ):
        return cached_pool, []

    if (
        cached_pool
        and not locked_source_version
        and not locked_source_as_of_date
        and legacy_locked_month
        and current_source_month
        and legacy_locked_month == current_source_month
    ):
        _set_rotation_pool_lock(
            state,
            source_version=current_source_version,
            source_as_of_date=current_source_as_of_date,
            now_utc=now_utc,
        )
        state["rotation_pool_symbols"] = cached_pool
        return cached_pool, []

    selected_pool, ranking = build_stable_quality_pool(
        indicators_map,
        btc_snapshot,
        set(cached_pool),
    )
    if selected_pool:
        _set_rotation_pool_lock(
            state,
            source_version=current_source_version,
            source_as_of_date=current_source_as_of_date,
            now_utc=now_utc,
        )
        state["rotation_pool_symbols"] = selected_pool
        return selected_pool, ranking

    fallback_pool = cached_pool if cached_pool else list(TREND_UNIVERSE.keys())[:TREND_POOL_SIZE]
    _set_rotation_pool_lock(
        state,
        source_version=current_source_version,
        source_as_of_date=current_source_as_of_date,
        now_utc=now_utc,
    )
    state["rotation_pool_symbols"] = fallback_pool
    return fallback_pool, []


def select_rotation_weights(indicators_map, prices, btc_snapshot, candidate_pool, top_n):
    """Pick final trend holdings from monthly pool by relative BTC strength."""
    return shared_select_rotation_weights(
        indicators_map,
        prices,
        btc_snapshot,
        candidate_pool,
        top_n,
        weight_mode="inverse_vol",
    )


def allocate_trend_buy_budget(selected_candidates, buyable_symbols, total_budget):
    return shared_allocate_trend_buy_budget(selected_candidates, buyable_symbols, total_budget)


def resolve_runtime_trend_pool(runtime, raw_state):
    if runtime.trend_pool_payload is None:
        return load_trend_universe_from_live_pool(state=raw_state, now_utc=runtime.now_utc)

    settings = get_trend_pool_contract_settings()
    validated = validate_trend_pool_payload(
        runtime.trend_pool_payload,
        source_label="runtime:trend_pool_payload",
        now_utc=runtime.now_utc,
        max_age_days=settings["max_age_days"],
        acceptable_modes=settings["acceptable_modes"],
        expected_pool_size=settings["expected_pool_size"],
        enforce_freshness=True,
    )
    if validated.get("ok"):
        resolution = build_trend_pool_resolution(
            validated,
            source_kind="fresh_upstream",
            degraded=False,
            now_utc=runtime.now_utc,
            messages=[t("trend_pool_loaded_runtime_payload")],
        )
        return resolution["symbol_map"], resolution
    raise ValueError(
        "Injected trend_pool_payload failed validation: "
        + "; ".join(validated.get("errors", []) or ["unknown validation error"])
    )


def resolve_runtime_btc_snapshot(runtime, btc_price, log_buffer):
    if runtime.btc_market_snapshot is not None:
        return dict(runtime.btc_market_snapshot)
    return fetch_btc_market_snapshot(runtime.client, btc_price, log_buffer=log_buffer)


def resolve_runtime_trend_indicators(runtime):
    if runtime.trend_indicator_snapshots is None:
        trend_indicators = {}
        for symbol in TREND_UNIVERSE:
            trend_indicators[symbol] = fetch_daily_indicators(runtime.client, symbol)
        return trend_indicators
    return {symbol: runtime.trend_indicator_snapshots.get(symbol) for symbol in TREND_UNIVERSE}


def ensure_asset_available_runtime(runtime, report, asset, required_amount, log_buffer):
    try:
        spot_free = float(runtime.client.get_asset_balance(asset=asset)["free"])
        if spot_free >= required_amount:
            return True

        shortfall = required_amount - spot_free
        earn_positions = runtime.client.get_simple_earn_flexible_product_position(asset=asset)
        if earn_positions and "rows" in earn_positions and len(earn_positions["rows"]) > 0:
            row = earn_positions["rows"][0]
            product_id = row["productId"]
            earn_free = float(row["totalAmount"])
            if earn_free > 0:
                redeem_amt = round(min(shortfall * 1.001, earn_free), 8)
                intent = {
                    "asset": str(asset),
                    "action": "redeem",
                    "product_id": str(product_id),
                    "amount": float(redeem_amt),
                    "reason": "asset_availability",
                }
                report["redemption_subscription_intents"].append(intent)
                runtime_call_client(
                    runtime,
                    report,
                    method_name="redeem_simple_earn_flexible_product",
                    payload={"productId": product_id, "amount": redeem_amt},
                    effect_type="earn_redeem",
                )
                append_log(
                    log_buffer,
                    t("execution_spot_short_redeeming_from_earn", asset=asset, amount=redeem_amt),
                )
                if not runtime.dry_run:
                    time.sleep(3)
                return True
    except Exception as exc:
        runtime_notify(runtime, report,
            f"{t('redeem_failed')} {asset}\n"
            f"{t('error_label')}: {exc}")
    return False


def manage_usdt_earn_buffer_runtime(runtime, report, target_buffer, log_buffer, spot_free_override=None):
    try:
        asset = "USDT"
        if spot_free_override is None:
            spot_free = float(runtime.client.get_asset_balance(asset=asset)["free"])
        else:
            spot_free = max(0.0, float(spot_free_override))

        earn_list = runtime.client.get_simple_earn_flexible_product_list(asset=asset)
        if not earn_list or "rows" not in earn_list or len(earn_list["rows"]) == 0:
            return
        product_id = earn_list["rows"][0]["productId"]

        if spot_free > target_buffer + 5.0:
            excess = round(spot_free - target_buffer, 4)
            if excess >= 0.1:
                report["redemption_subscription_intents"].append(
                    {
                        "asset": asset,
                        "action": "subscribe",
                        "product_id": str(product_id),
                        "amount": float(excess),
                        "reason": "maintain_usdt_buffer",
                    }
                )
                runtime_call_client(
                    runtime,
                    report,
                    method_name="subscribe_simple_earn_flexible_product",
                    payload={"productId": product_id, "amount": excess},
                    effect_type="earn_subscribe",
                )
                append_log(log_buffer, t("cash_manager_subscribed_to_earn", amount=excess))
        elif spot_free < target_buffer - 5.0:
            shortfall = round(target_buffer - spot_free, 4)
            earn_positions = runtime.client.get_simple_earn_flexible_product_position(asset=asset)
            if earn_positions and "rows" in earn_positions and len(earn_positions["rows"]) > 0:
                earn_free = float(earn_positions["rows"][0]["totalAmount"])
                if earn_free > 0:
                    redeem_amt = round(min(shortfall, earn_free), 8)
                    report["redemption_subscription_intents"].append(
                        {
                            "asset": asset,
                            "action": "redeem",
                            "product_id": str(product_id),
                            "amount": float(redeem_amt),
                            "reason": "maintain_usdt_buffer",
                        }
                    )
                    runtime_call_client(
                        runtime,
                        report,
                        method_name="redeem_simple_earn_flexible_product",
                        payload={"productId": product_id, "amount": redeem_amt},
                        effect_type="earn_redeem",
                    )
                    append_log(log_buffer, t("cash_manager_redeeming_to_spot", amount=redeem_amt))
    except Exception as exc:
        append_log(log_buffer, t("usdt_earn_buffer_maintenance_failed", error=exc))


def get_tradable_qty(symbol, total_qty, prices, min_bnb_value):
    """Reserve BNB for fees; rest is tradable."""
    if symbol != "BNBUSDT":
        return max(0.0, total_qty)

    bnb_price = prices.get("BNBUSDT", 0.0)
    if bnb_price <= 0:
        return 0.0

    reserve_qty = (min_bnb_value * 1.2) / bnb_price
    return max(0.0, total_qty - reserve_qty)

# ==========================================
# 3. Core strategy
# ==========================================
def build_live_runtime(now_utc=None):
    return rc_build_live_runtime(
        now_utc=now_utc,
        state_loader=get_trade_state,
        state_writer=set_trade_state,
        notifier=lambda **kwargs: send_tg_msg(kwargs["token"], kwargs["chat_id"], kwargs["text"]),
    )


def _ensure_runtime_client(runtime, report):
    if runtime.client is not None:
        return True

    max_retries = 3
    for attempt in range(max_retries):
        try:
            runtime.client = qpk_connect_client(runtime.api_key, runtime.api_secret, timeout=30)
            return True
        except Exception as exc:
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            append_report_error(report, f"Unable to connect Binance API: {exc}", stage="client")
            report["status"] = "aborted"
            runtime_notify(runtime, report,
                f"{t('api_error')}\n"
                f"{t('error_label')}: {str(exc)}")
            return False

    return False


def _load_cycle_state(runtime, report, allow_new_trend_entries_on_degraded):
    global TREND_UNIVERSE

    raw_state = runtime.state_loader(normalize=False)
    if raw_state is None:
        append_report_error(
            report,
            "Failed to load Firestore state. Check GCP credentials (GCP_SA_KEY / GOOGLE_APPLICATION_CREDENTIALS), service account validity, and Firestore API enablement.",
            stage="state_load",
        )
        report["status"] = "aborted"
        return None

    TREND_UNIVERSE, trend_pool_resolution = resolve_runtime_trend_pool(runtime, raw_state)
    state = normalize_trade_state(raw_state)
    update_trend_pool_state(state, trend_pool_resolution)
    runtime_set_trade_state(runtime, report, state, reason="trend_pool_metadata_refresh")
    runtime_trend_universe = get_runtime_trend_universe(state)
    allow_new_trend_entries = (not trend_pool_resolution["degraded"]) or allow_new_trend_entries_on_degraded
    return state, trend_pool_resolution, runtime_trend_universe, allow_new_trend_entries


def _append_trend_pool_source_logs(log_buffer, trend_pool_resolution, allow_new_trend_entries):
    for line in dm_format_trend_pool_source_logs(
        trend_pool_resolution,
        allow_new_trend_entries=allow_new_trend_entries,
    ):
        append_log(log_buffer, line)


def _capture_market_snapshot(runtime, report, runtime_trend_universe, log_buffer, min_bnb_value, buy_bnb_amount):
    return ms_capture_market_snapshot(
        runtime,
        report,
        runtime_trend_universe,
        log_buffer,
        min_bnb_value,
        buy_bnb_amount,
        get_total_balance_fn=get_total_balance,
        ensure_asset_available_fn=ensure_asset_available_runtime,
        runtime_call_client_fn=runtime_call_client,
        runtime_notify_fn=runtime_notify,
        append_log_fn=append_log,
        resolve_btc_snapshot_fn=resolve_runtime_btc_snapshot,
        resolve_trend_indicators_fn=resolve_runtime_trend_indicators,
        bnb_fuel_symbol=BNB_FUEL_SYMBOL,
        bnb_fuel_asset=BNB_FUEL_ASSET,
    )


def _compute_portfolio_allocation(runtime_trend_universe, balances, prices, u_total, fuel_val):
    trend_val = sum(balances[symbol] * prices[symbol] for symbol in runtime_trend_universe)
    dca_val = balances["BTCUSDT"] * prices["BTCUSDT"]
    total_equity = u_total + fuel_val + trend_val + dca_val
    allocation = compute_allocation_budgets(total_equity, u_total, trend_val, dca_val)
    allocation.update(
        {
            "trend_val": trend_val,
            "dca_val": dca_val,
            "total_equity": total_equity,
        }
    )
    return allocation


def _maybe_reset_daily_state(state, runtime, report, today_utc, total_equity, trend_val_equity):
    """
    Circuit breaker uses trend daily PnL.
    PnL basis is ONLY the real trend holdings value (`trend_val`),
    so manual "USDT 零用钱" won't affect the trend circuit breaker.
    """
    desired_basis = "trend_val"
    last_reset_date = state.get("last_reset_date")
    pnl_basis = state.get("daily_trend_pnl_basis")

    if last_reset_date != today_utc:
        state.update(
            {
                "daily_equity_base": total_equity,
                "daily_trend_equity_base": trend_val_equity,
                "daily_trend_pnl_basis": desired_basis,
                "last_reset_date": today_utc,
                "is_circuit_broken": False,
            }
        )
        runtime_set_trade_state(runtime, report, state, reason="daily_reset")
        return

    # Basis migration within the same day (e.g. after deploy):
    # update the trend PnL base but keep circuit breaker latch.
    if pnl_basis != desired_basis:
        state.update(
            {
                "daily_trend_equity_base": trend_val_equity,
                "daily_trend_pnl_basis": desired_basis,
            }
        )
        runtime_set_trade_state(runtime, report, state, reason="trend_pnl_basis_migrate")


def _compute_daily_pnls(state, total_equity, trend_equity):
    daily_pnl = (
        (total_equity - state["daily_equity_base"]) / state["daily_equity_base"]
        if state.get("daily_equity_base", 0) > 0
        else 0.0
    )
    trend_daily_pnl = (
        (trend_equity - state["daily_trend_equity_base"]) / state["daily_trend_equity_base"]
        if state.get("daily_trend_equity_base", 0) > 0
        else 0.0
    )
    return daily_pnl, trend_daily_pnl


def _append_portfolio_report(log_buffer, allocation, fuel_val, daily_pnl, trend_daily_pnl, btc_snapshot):
    return report_append_portfolio_report(
        log_buffer,
        allocation,
        fuel_val,
        daily_pnl,
        trend_daily_pnl,
        btc_snapshot,
        append_log_fn=append_log,
        translate_fn=t,
        separator="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    )


def _run_daily_circuit_breaker(
    runtime,
    report,
    state,
    runtime_trend_universe,
    balances,
    prices,
    trend_daily_pnl,
    circuit_breaker_pct,
    log_buffer,
):
    if trend_daily_pnl > circuit_breaker_pct:
        return False

    for symbol, config in runtime_trend_universe.items():
        tradable_qty = balances[symbol]
        if tradable_qty * prices[symbol] <= 10:
            continue
        qty = format_qty(runtime.client, symbol, tradable_qty)
        report["buy_sell_intents"].append(
            {
                "category": "trend",
                "action": "sell",
                "symbol": symbol,
                "reason": "daily_circuit_breaker",
                "quantity": float(qty),
            }
        )
        try:
            if qty <= 0:
                runtime_notify(runtime, report,
                    f"{t('circuit_breaker_sell_skipped')} {symbol}\n"
                    f"{t('qty_zero_msg')}")
                continue
            if not ensure_asset_available_runtime(runtime, report, config["base_asset"], qty, log_buffer):
                raise RuntimeError(
                    t("asset_unavailable_for_circuit_breaker_sell", asset=config["base_asset"])
                )
            runtime_call_client(
                runtime,
                report,
                method_name="order_market_sell",
                payload={"symbol": symbol, "quantity": qty},
                effect_type="order_sell",
            )
            balances[symbol] = max(0.0, balances[symbol] - qty)
            set_symbol_trade_state(
                state,
                symbol,
                {"is_holding": False, "entry_price": 0.0, "highest_price": 0.0},
            )
        except Exception as exc:
            runtime_notify(runtime, report,
                f"{t('circuit_breaker_sell_failed')} {symbol}\n"
                f"{t('error_label')}: {exc}")

    state.update({"is_circuit_broken": True})
    report["circuit_breaker_triggered"] = True
    runtime_set_trade_state(runtime, report, state, reason="daily_circuit_breaker")
    runtime_notify(runtime, report,
        f"{t('circuit_breaker')}\n"
        f"{t('circuit_msg', pnl=f'{trend_daily_pnl:.2%}')}")
    return True


def _append_rotation_summary(log_buffer, official_trend_pool, active_trend_pool, selected_candidates):
    return report_append_rotation_summary(
        log_buffer,
        official_trend_pool,
        active_trend_pool,
        selected_candidates,
        append_log_fn=append_log,
        translate_fn=t,
    )


def _get_trend_sell_reason(state, symbol, curr_price, indicators, selected_candidates, atr_multiplier):
    st = get_symbol_trade_state(state, symbol)
    if not st["is_holding"]:
        return ""

    sell_reason = ""
    stop_price = None
    if not indicators:
        sell_reason = t("trend_sell_reason_missing_indicators")
    else:
        st["highest_price"] = max(st["highest_price"], curr_price)
        set_symbol_trade_state(state, symbol, st)
        stop_price = st["highest_price"] - (atr_multiplier * indicators["atr14"])

    if symbol not in selected_candidates and not sell_reason:
        sell_reason = t("trend_sell_reason_rotated_out")
    elif indicators and curr_price < indicators["sma60"]:
        sell_reason = t("trend_sell_reason_below_sma60")
    elif stop_price is not None and curr_price < stop_price:
        sell_reason = t("trend_sell_reason_atr_stop", stop_price=stop_price)
    return sell_reason


def _execute_trend_sells(
    runtime,
    report,
    state,
    runtime_trend_universe,
    selected_candidates,
    trend_indicators,
    prices,
    balances,
    u_total,
    log_buffer,
    today_id_str,
    atr_multiplier,
):
    for symbol, config in runtime_trend_universe.items():
        curr_price = prices[symbol]
        sell_reason = _get_trend_sell_reason(
            state,
            symbol,
            curr_price,
            trend_indicators.get(symbol),
            selected_candidates,
            atr_multiplier,
        )
        if not sell_reason:
            continue

        if should_skip_duplicate_trend_action(state, symbol, "sell", today_id_str):
            append_log(log_buffer, t("duplicate_sell_skipped", symbol=symbol))
            continue

        qty = format_qty(runtime.client, symbol, balances[symbol])
        report["buy_sell_intents"].append(
            {
                "category": "trend",
                "action": "sell",
                "symbol": symbol,
                "reason": sell_reason,
                "quantity": float(qty),
                "price": float(curr_price),
            }
        )
        try:
            if qty <= 0:
                runtime_notify(runtime, report,
                    f"{t('trend_sell_skipped')} {symbol}\n"
                    f"{t('reason_label')}: {sell_reason}\n"
                    f"{t('qty_zero_msg')}")
                continue
            if not ensure_asset_available_runtime(runtime, report, config["base_asset"], qty, log_buffer):
                raise RuntimeError(t("asset_unavailable_for_trend_sell", asset=config["base_asset"]))
            runtime_call_client(
                runtime,
                report,
                method_name="order_market_sell",
                payload={
                    "symbol": symbol,
                    "quantity": qty,
                    "newClientOrderId": next_order_id(runtime, "T_SELL", symbol),
                },
                effect_type="order_sell",
            )
            balances[symbol] = max(0.0, balances[symbol] - qty)
            u_total += qty * curr_price
            set_symbol_trade_state(
                state,
                symbol,
                {"is_holding": False, "entry_price": 0.0, "highest_price": 0.0},
            )
            record_trend_action(state, symbol, "sell", today_id_str)
            runtime_set_trade_state(runtime, report, state, reason=f"trend_sell:{symbol}")
            runtime_notify(runtime, report,
                f"{t('trend_sell')} {symbol}\n"
                f"{t('reason_label')}: {sell_reason}\n"
                f"{t('price_label')}: ${curr_price:.2f}")
        except Exception as exc:
            runtime_notify(runtime, report,
                f"{t('trend_sell_failed')} {symbol}\n"
                f"{t('reason_label')}: {sell_reason}\n"
                f"{t('error_label')}: {exc}")

    return u_total


def _plan_trend_buys(
    state,
    runtime_trend_universe,
    selected_candidates,
    trend_indicators,
    prices,
    available_trend_buy_budget,
    allow_new_trend_entries,
):
    eligible_buy_symbols = []
    for symbol in runtime_trend_universe:
        if get_symbol_trade_state(state, symbol)["is_holding"]:
            continue
        curr_price = prices[symbol]
        indicators = trend_indicators.get(symbol)
        candidate_meta = selected_candidates.get(symbol)
        can_open_new_position = (
            allow_new_trend_entries
            and indicators
            and candidate_meta
            and curr_price > indicators["sma20"]
            and curr_price > indicators["sma60"]
            and curr_price > indicators["sma200"]
        )
        if can_open_new_position:
            eligible_buy_symbols.append(symbol)

    planned_trend_buys = allocate_trend_buy_budget(
        selected_candidates,
        eligible_buy_symbols,
        available_trend_buy_budget,
    )
    return eligible_buy_symbols, planned_trend_buys


def _execute_trend_buys(
    runtime,
    report,
    state,
    selected_candidates,
    eligible_buy_symbols,
    planned_trend_buys,
    prices,
    balances,
    u_total,
    log_buffer,
    today_id_str,
):
    for symbol in eligible_buy_symbols:
        curr_price = prices[symbol]
        candidate_meta = selected_candidates[symbol]
        buy_u = planned_trend_buys.get(symbol, 0.0)
        if buy_u <= 15:
            continue

        if should_skip_duplicate_trend_action(state, symbol, "buy", today_id_str):
            append_log(log_buffer, t("duplicate_buy_skipped", symbol=symbol))
            continue

        qty = format_qty(runtime.client, symbol, buy_u * 0.985 / curr_price)
        usdt_cost = qty * curr_price
        report["buy_sell_intents"].append(
            {
                "category": "trend",
                "action": "buy",
                "symbol": symbol,
                "quantity": float(qty),
                "budget": float(buy_u),
                "weight": float(candidate_meta["weight"]),
                "relative_score": float(candidate_meta["relative_score"]),
            }
        )
        try:
            if qty <= 0 or usdt_cost <= 0:
                runtime_notify(runtime, report,
                    f"{t('trend_buy_skipped')} {symbol}\n"
                    f"{t('budget_label')}: ${buy_u:.2f}\n"
                    f"{t('qty_zero_msg')}")
                continue
            if not ensure_asset_available_runtime(runtime, report, "USDT", usdt_cost, log_buffer):
                raise RuntimeError(t("usdt_unavailable_for_trend_buy"))
            runtime_call_client(
                runtime,
                report,
                method_name="order_market_buy",
                payload={
                    "symbol": symbol,
                    "quantity": qty,
                    "newClientOrderId": next_order_id(runtime, "T_BUY", symbol),
                },
                effect_type="order_buy",
            )
            set_symbol_trade_state(
                state,
                symbol,
                {"is_holding": True, "entry_price": curr_price, "highest_price": curr_price},
            )
            balances[symbol] += qty
            u_total -= usdt_cost
            record_trend_action(state, symbol, "buy", today_id_str)
            runtime_set_trade_state(runtime, report, state, reason=f"trend_buy:{symbol}")
            runtime_notify(runtime, report,
                f"{t('trend_buy')} {symbol}\n"
                f"{t('price_label')}: ${curr_price:.2f}\n"
                f"{t('budget_label')}: ${buy_u:.2f}\n"
                f"{t('weight_label')}: {candidate_meta['weight']:.0%}\n"
                f"{t('rel_score_label')}: {candidate_meta['relative_score']:.2f}")
        except Exception as exc:
            runtime_notify(runtime, report,
                f"{t('trend_buy_failed')} {symbol}\n"
                f"{t('budget_label')}: ${buy_u:.2f}\n"
                f"{t('error_label')}: {exc}")

    return u_total


def _append_trend_symbol_status(log_buffer, runtime_trend_universe, prices, trend_indicators, state, btc_snapshot):
    return report_append_trend_symbol_status(
        log_buffer,
        runtime_trend_universe,
        prices,
        trend_indicators,
        state,
        btc_snapshot,
        append_log_fn=append_log,
        translate_fn=t,
        get_symbol_trade_state_fn=get_symbol_trade_state,
    )


def _execute_trend_rotation(
    runtime,
    report,
    state,
    runtime_trend_universe,
    trend_indicators,
    btc_snapshot,
    prices,
    balances,
    u_total,
    fuel_val,
    log_buffer,
    today_id_str,
    allow_new_trend_entries,
    allow_pool_refresh,
    atr_multiplier,
):
    active_trend_pool, _ = refresh_rotation_pool(
        state,
        trend_indicators,
        btc_snapshot,
        allow_refresh=allow_pool_refresh,
        now_utc=runtime.now_utc,
    )
    selected_candidates = select_rotation_weights(
        trend_indicators,
        prices,
        btc_snapshot,
        active_trend_pool,
        ROTATION_TOP_N,
    )
    report["selected_symbols"]["active_trend_pool"] = list(active_trend_pool)
    report["selected_symbols"]["selected_candidates"] = list(selected_candidates.keys())

    _append_rotation_summary(
        log_buffer,
        list(TREND_UNIVERSE.keys()),
        active_trend_pool,
        selected_candidates,
    )
    u_total = _execute_trend_sells(
        runtime,
        report,
        state,
        runtime_trend_universe,
        selected_candidates,
        trend_indicators,
        prices,
        balances,
        u_total,
        log_buffer,
        today_id_str,
        atr_multiplier,
    )

    current_allocation = _compute_portfolio_allocation(runtime_trend_universe, balances, prices, u_total, fuel_val)
    eligible_buy_symbols, planned_trend_buys = _plan_trend_buys(
        state,
        runtime_trend_universe,
        selected_candidates,
        trend_indicators,
        prices,
        current_allocation["trend_usdt_pool"],
        allow_new_trend_entries,
    )
    u_total = _execute_trend_buys(
        runtime,
        report,
        state,
        selected_candidates,
        eligible_buy_symbols,
        planned_trend_buys,
        prices,
        balances,
        u_total,
        log_buffer,
        today_id_str,
    )
    _append_trend_symbol_status(log_buffer, runtime_trend_universe, prices, trend_indicators, state, btc_snapshot)
    return u_total


def _execute_btc_dca_cycle(
    runtime,
    report,
    state,
    balances,
    prices,
    u_total,
    total_equity,
    dca_usdt_pool,
    dca_val,
    btc_snapshot,
    btc_target_ratio,
    today_id_str,
    log_buffer,
):
    if dca_usdt_pool <= 10 and dca_val <= 10:
        return u_total

    btc_price = prices["BTCUSDT"]
    ahr = btc_snapshot["ahr999"]
    zscore = btc_snapshot["zscore"]
    sell_trigger = btc_snapshot["sell_trigger"]
    append_log(log_buffer, t("btc_accumulation_radar_line", ahr=ahr, zscore=zscore, sell_trigger=sell_trigger))

    base_order = get_dynamic_btc_base_order(total_equity)
    multiplier = 0
    if ahr < 0.45:
        multiplier = 5
    elif ahr < 0.8:
        multiplier = 2
    elif ahr < 1.2:
        multiplier = 1

    if multiplier > 0 and dca_usdt_pool > 15 and state.get("dca_last_buy_date") != today_id_str:
        budget = min(dca_usdt_pool, base_order * multiplier)
        qty = format_qty(runtime.client, "BTCUSDT", budget * 0.985 / btc_price)
        buy_cost = qty * btc_price
        report["btc_dca_intents"].append(
            {
                "action": "buy",
                "symbol": "BTCUSDT",
                "quantity": float(qty),
                "budget": float(budget),
                "ahr999": float(ahr),
            }
        )
        try:
            if qty <= 0 or buy_cost <= 0:
                runtime_notify(runtime, report,
                    f"{t('btc_dca_buy_skipped')}\n"
                    f"{t('qty_zero_msg')}")
            else:
                if not ensure_asset_available_runtime(runtime, report, "USDT", buy_cost, log_buffer):
                    raise RuntimeError(t("usdt_unavailable_for_btc_dca_buy"))
                runtime_call_client(
                    runtime,
                    report,
                    method_name="order_market_buy",
                    payload={
                        "symbol": "BTCUSDT",
                        "quantity": qty,
                        "newClientOrderId": next_order_id(runtime, "D_BUY", "BTCUSDT"),
                    },
                    effect_type="order_buy",
                )
                balances["BTCUSDT"] += qty
                u_total -= buy_cost
                state["dca_last_buy_date"] = today_id_str
                runtime_notify(runtime, report,
                    f"{t('btc_dca_buy')} BTC\n"
                    f"{t('ahr999')}: {ahr:.2f}\n"
                    f"{t('target_alloc_label')}: {btc_target_ratio:.1%}\n"
                    f"{t('quantity_label')}: {qty} BTC")
                runtime_set_trade_state(runtime, report, state, reason="btc_dca_buy")
        except Exception as exc:
            runtime_notify(runtime, report,
                f"{t('btc_dca_buy_failed')} BTC\n"
                f"{t('error_label')}: {exc}")

    if zscore > sell_trigger and dca_val > 20 and state.get("dca_last_sell_date") != today_id_str:
        sell_pct = 0.1
        if zscore > 4.0:
            sell_pct = 0.3
        if zscore > 5.0:
            sell_pct = 0.5
        qty = format_qty(runtime.client, "BTCUSDT", balances["BTCUSDT"] * sell_pct)
        report["btc_dca_intents"].append(
            {
                "action": "sell",
                "symbol": "BTCUSDT",
                "quantity": float(qty),
                "sell_pct": float(sell_pct),
                "zscore": float(zscore),
            }
        )
        try:
            if qty <= 0:
                runtime_notify(runtime, report,
                    f"{t('btc_dca_trim_skipped')}\n"
                    f"{t('qty_zero_msg')}")
            else:
                if not ensure_asset_available_runtime(runtime, report, "BTC", qty, log_buffer):
                    raise RuntimeError(t("btc_unavailable_for_dca_sell"))
                runtime_call_client(
                    runtime,
                    report,
                    method_name="order_market_sell",
                    payload={
                        "symbol": "BTCUSDT",
                        "quantity": qty,
                        "newClientOrderId": next_order_id(runtime, "D_SELL", "BTCUSDT"),
                    },
                    effect_type="order_sell",
                )
                balances["BTCUSDT"] = max(0.0, balances["BTCUSDT"] - qty)
                u_total += qty * btc_price
                state["dca_last_sell_date"] = today_id_str
                runtime_notify(runtime, report,
                    f"{t('btc_dca_trim')} BTC\n"
                    f"{t('ratio_label')}: {sell_pct*100}%\n"
                    f"{t('quantity_label')}: {qty} BTC")
                runtime_set_trade_state(runtime, report, state, reason="btc_dca_sell")
        except Exception as exc:
            runtime_notify(runtime, report,
                f"{t('btc_dca_trim_failed')} BTC\n"
                f"{t('error_label')}: {exc}")

    return u_total


def execute_cycle(runtime):
    global TREND_UNIVERSE

    atr_multiplier = 2.5
    circuit_breaker_pct = -0.05
    min_bnb_value, buy_bnb_amount = 10.0, 15.0
    cycle_settings = rc_load_cycle_execution_settings()
    btc_status_report_interval_hours = cycle_settings.btc_status_report_interval_hours
    allow_new_trend_entries_on_degraded = cycle_settings.allow_new_trend_entries_on_degraded

    report = build_execution_report(runtime)
    log_buffer = []
    previous_trend_universe = {symbol: meta.copy() for symbol, meta in TREND_UNIVERSE.items()}

    try:
        if not _ensure_runtime_client(runtime, report):
            return report

        cycle_state = _load_cycle_state(runtime, report, allow_new_trend_entries_on_degraded)
        if cycle_state is None:
            return report

        state, trend_pool_resolution, runtime_trend_universe, allow_new_trend_entries = cycle_state
        _append_trend_pool_source_logs(log_buffer, trend_pool_resolution, allow_new_trend_entries)

        report["upstream_pool_symbols"] = list(runtime_trend_universe.keys())
        if trend_pool_resolution["degraded"]:
            report["degraded_mode_level"] = trend_pool_resolution.get("source", "unknown")

        market_snapshot = _capture_market_snapshot(
            runtime,
            report,
            runtime_trend_universe,
            log_buffer,
            min_bnb_value,
            buy_bnb_amount,
        )
        u_total = market_snapshot["u_total"]
        fuel_val = market_snapshot["fuel_val"]
        dynamic_usdt_buffer = market_snapshot["dynamic_usdt_buffer"]
        prices = market_snapshot["prices"]
        balances = market_snapshot["balances"]
        btc_snapshot = market_snapshot["btc_snapshot"]
        trend_indicators = market_snapshot["trend_indicators"]

        allocation = _compute_portfolio_allocation(runtime_trend_universe, balances, prices, u_total, fuel_val)
        total_equity = allocation["total_equity"]
        trend_layer_equity = allocation["trend_layer_equity"]
        trend_val_equity = allocation["trend_val"]

        report["total_equity_usdt"] = total_equity
        report["trend_equity_usdt"] = trend_val_equity

        now_utc = runtime.now_utc
        today_utc = now_utc.strftime("%Y-%m-%d")
        today_id_str = now_utc.strftime("%Y%m%d")

        _maybe_reset_daily_state(state, runtime, report, today_utc, total_equity, trend_val_equity)
        daily_pnl, trend_daily_pnl = _compute_daily_pnls(state, total_equity, trend_val_equity)
        _append_portfolio_report(log_buffer, allocation, fuel_val, daily_pnl, trend_daily_pnl, btc_snapshot)

        if state.get("is_circuit_broken"):
            log_buffer.insert(0, t("circuit_breaker_latched_line", total_equity=total_equity))
            return report

        if _run_daily_circuit_breaker(
            runtime,
            report,
            state,
            runtime_trend_universe,
            balances,
            prices,
            trend_daily_pnl,
            circuit_breaker_pct,
            log_buffer,
        ):
            return report

        u_total = _execute_trend_rotation(
            runtime,
            report,
            state,
            runtime_trend_universe,
            trend_indicators,
            btc_snapshot,
            prices,
            balances,
            u_total,
            fuel_val,
            log_buffer,
            today_id_str,
            allow_new_trend_entries,
            allow_pool_refresh=not trend_pool_resolution["degraded"],
            atr_multiplier=atr_multiplier,
        )

        post_trade_allocation = _compute_portfolio_allocation(runtime_trend_universe, balances, prices, u_total, fuel_val)
        total_equity = post_trade_allocation["total_equity"]
        trend_layer_equity = post_trade_allocation["trend_layer_equity"]
        trend_val_equity = post_trade_allocation["trend_val"]

        report["total_equity_usdt"] = total_equity
        report["trend_equity_usdt"] = trend_val_equity

        btc_target_ratio = post_trade_allocation["btc_target_ratio"]
        dca_usdt_pool = post_trade_allocation["dca_usdt_pool"]
        dca_val = post_trade_allocation["dca_val"]
        _, trend_daily_pnl = _compute_daily_pnls(state, total_equity, trend_val_equity)

        u_total = _execute_btc_dca_cycle(
            runtime,
            report,
            state,
            balances,
            prices,
            u_total,
            total_equity,
            dca_usdt_pool,
            dca_val,
            btc_snapshot,
            btc_target_ratio,
            today_id_str,
            log_buffer,
        )

        manage_usdt_earn_buffer_runtime(
            runtime,
            report,
            dynamic_usdt_buffer,
            log_buffer,
            spot_free_override=u_total if runtime.dry_run else None,
        )

        maybe_send_periodic_btc_status_report(
            state,
            runtime.tg_token,
            runtime.tg_chat_id,
            now_utc,
            btc_status_report_interval_hours,
            total_equity,
            trend_val_equity,
            trend_daily_pnl,
            prices["BTCUSDT"],
            btc_snapshot,
            btc_target_ratio,
            notifier_fn=lambda text: runtime_notify(runtime, report, text),
        )

        runtime_set_trade_state(runtime, report, state, reason="cycle_complete")

    except Exception as exc:
        report["status"] = "error"
        append_report_error(report, str(exc), stage="execute_cycle")
        if runtime.print_traceback:
            traceback.print_exc()
        try:
            runtime_notify(runtime, report,
                f"{t('system_crash')}\n"
                f"{str(exc)[:200]}")
        except Exception:
            pass
    finally:
        TREND_UNIVERSE = previous_trend_universe
        report["log_lines"] = list(log_buffer)

    return report


def main():
    return run_cli_entrypoint(
        runtime_builder=build_live_runtime,
        execute_cycle=execute_cycle,
        output_printer=print,
        exit_fn=sys.exit,
    )


if __name__ == "__main__":
    main()
