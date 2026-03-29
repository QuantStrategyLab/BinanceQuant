"""Strategy-level trend rotation rules for BinancePlatform."""

from __future__ import annotations

from datetime import datetime, timezone


def _set_rotation_pool_lock(state, *, source_version, source_as_of_date, now_utc):
    locked_version = str(source_version or "").strip()
    locked_as_of_date = str(source_as_of_date or "").strip()
    state["rotation_pool_source_version"] = locked_version
    state["rotation_pool_source_as_of_date"] = locked_as_of_date
    if locked_as_of_date:
        state["rotation_pool_last_month"] = locked_as_of_date[:7]
    else:
        state["rotation_pool_last_month"] = (now_utc or datetime.now(timezone.utc)).strftime("%Y-%m")


def refresh_rotation_pool(
    state,
    indicators_map,
    btc_snapshot,
    *,
    trend_universe_symbols,
    trend_pool_size,
    build_stable_quality_pool_fn,
    allow_refresh=True,
    now_utc=None,
):
    now_utc = now_utc or datetime.now(timezone.utc)
    trend_universe_symbols = list(trend_universe_symbols)
    available_symbols = set(trend_universe_symbols)
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

    selected_pool, ranking = build_stable_quality_pool_fn(
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

    fallback_pool = cached_pool if cached_pool else trend_universe_symbols[:trend_pool_size]
    _set_rotation_pool_lock(
        state,
        source_version=current_source_version,
        source_as_of_date=current_source_as_of_date,
        now_utc=now_utc,
    )
    state["rotation_pool_symbols"] = fallback_pool
    return fallback_pool, []


def get_trend_sell_reason(
    state,
    symbol,
    curr_price,
    indicators,
    selected_candidates,
    atr_multiplier,
    *,
    get_symbol_trade_state_fn,
    set_symbol_trade_state_fn,
    translate_fn,
):
    symbol_state = get_symbol_trade_state_fn(state, symbol)
    if not symbol_state["is_holding"]:
        return ""

    sell_reason = ""
    stop_price = None
    if not indicators:
        sell_reason = translate_fn("trend_sell_reason_missing_indicators")
    else:
        symbol_state["highest_price"] = max(symbol_state["highest_price"], curr_price)
        set_symbol_trade_state_fn(state, symbol, symbol_state)
        stop_price = symbol_state["highest_price"] - (atr_multiplier * indicators["atr14"])

    if symbol not in selected_candidates and not sell_reason:
        sell_reason = translate_fn("trend_sell_reason_rotated_out")
    elif indicators and curr_price < indicators["sma60"]:
        sell_reason = translate_fn("trend_sell_reason_below_sma60")
    elif stop_price is not None and curr_price < stop_price:
        sell_reason = translate_fn("trend_sell_reason_atr_stop", stop_price=stop_price)
    return sell_reason


def plan_trend_buys(
    state,
    runtime_trend_universe,
    selected_candidates,
    trend_indicators,
    prices,
    available_trend_buy_budget,
    allow_new_trend_entries,
    *,
    get_symbol_trade_state_fn,
    allocate_trend_buy_budget_fn,
):
    eligible_buy_symbols = []
    for symbol in runtime_trend_universe:
        if get_symbol_trade_state_fn(state, symbol)["is_holding"]:
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

    planned_trend_buys = allocate_trend_buy_budget_fn(
        selected_candidates,
        eligible_buy_symbols,
        available_trend_buy_budget,
    )
    return eligible_buy_symbols, planned_trend_buys
