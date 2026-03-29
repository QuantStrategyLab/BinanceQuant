"""Application-level portfolio and daily-state helpers for BinancePlatform."""

from __future__ import annotations


def compute_portfolio_allocation(
    runtime_trend_universe,
    balances,
    prices,
    u_total,
    fuel_val,
    *,
    compute_allocation_budgets_fn,
):
    trend_val = sum(balances[symbol] * prices[symbol] for symbol in runtime_trend_universe)
    dca_val = balances["BTCUSDT"] * prices["BTCUSDT"]
    total_equity = u_total + fuel_val + trend_val + dca_val
    allocation = compute_allocation_budgets_fn(total_equity, u_total, trend_val, dca_val)
    allocation.update(
        {
            "trend_val": trend_val,
            "dca_val": dca_val,
            "total_equity": total_equity,
        }
    )
    return allocation


def maybe_reset_daily_state(
    state,
    runtime,
    report,
    today_utc,
    total_equity,
    trend_val_equity,
    *,
    runtime_set_trade_state_fn,
):
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
        runtime_set_trade_state_fn(runtime, report, state, reason="daily_reset")
        return

    if pnl_basis != desired_basis:
        state.update(
            {
                "daily_trend_equity_base": trend_val_equity,
                "daily_trend_pnl_basis": desired_basis,
            }
        )
        runtime_set_trade_state_fn(runtime, report, state, reason="trend_pnl_basis_migrate")


def compute_daily_pnls(state, total_equity, trend_equity):
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


def append_portfolio_report(
    log_buffer,
    allocation,
    fuel_val,
    daily_pnl,
    trend_daily_pnl,
    btc_snapshot,
    *,
    append_portfolio_report_fn,
    append_log_fn,
    translate_fn,
    separator,
):
    return append_portfolio_report_fn(
        log_buffer,
        allocation,
        fuel_val,
        daily_pnl,
        trend_daily_pnl,
        btc_snapshot,
        append_log_fn=append_log_fn,
        translate_fn=translate_fn,
        separator=separator,
    )
