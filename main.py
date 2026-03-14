import os
import json
import requests
import pandas as pd
import math
import numpy as np
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from binance.client import Client
from binance.exceptions import BinanceAPIException
from google.cloud import firestore
import traceback

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


def get_env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)


def default_trend_symbol_state():
    return {"is_holding": False, "entry_price": 0.0, "highest_price": 0.0}


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def infer_base_asset(symbol):
    return symbol[:-4] if isinstance(symbol, str) and symbol.endswith("USDT") else symbol


def is_trend_symbol_state(value):
    return isinstance(value, dict) and any(
        key in value for key in ("is_holding", "entry_price", "highest_price")
    )


def normalize_symbol_state(value):
    state = default_trend_symbol_state()
    if not isinstance(value, dict):
        return state
    state["is_holding"] = bool(value.get("is_holding", state["is_holding"]))
    state["entry_price"] = safe_float(value.get("entry_price", state["entry_price"]))
    state["highest_price"] = safe_float(value.get("highest_price", state["highest_price"]))
    return state


def has_active_position(position_state):
    return bool(
        position_state.get("is_holding")
        or safe_float(position_state.get("entry_price", 0.0)) > 0.0
        or safe_float(position_state.get("highest_price", 0.0)) > 0.0
    )


def parse_trend_universe_mapping(payload):
    if not isinstance(payload, dict):
        return {}

    symbols = payload.get("symbol_map")
    if not isinstance(symbols, dict):
        symbols = payload.get("symbols")
    if not isinstance(symbols, dict):
        return {}

    parsed = {}
    for symbol, meta in symbols.items():
        if not isinstance(symbol, str) or not symbol.endswith("USDT"):
            continue
        if not isinstance(meta, dict):
            continue
        base_asset = meta.get("base_asset")
        if not isinstance(base_asset, str) or not base_asset:
            continue
        parsed[symbol] = {"base_asset": base_asset}
    return parsed


def get_default_live_pool_candidates():
    candidates = []
    search_roots = {
        Path(__file__).resolve().parents[1],
        Path.cwd().resolve(),
        Path.home(),
        Path("/home/ubuntu"),
    }
    repo_names = ("CryptoLeaderRotation", "crypto-leader-rotation")

    for root in search_roots:
        for repo_name in repo_names:
            candidate = root / repo_name / "data" / "output" / "live_pool_legacy.json"
            if candidate not in candidates:
                candidates.append(candidate)

    if DEFAULT_LIVE_POOL_LEGACY_PATH not in candidates:
        candidates.insert(0, DEFAULT_LIVE_POOL_LEGACY_PATH)
    return candidates


def load_trend_universe_from_firestore():
    collection = os.getenv("TREND_POOL_FIRESTORE_COLLECTION", DEFAULT_TREND_POOL_FIRESTORE_COLLECTION)
    document = os.getenv("TREND_POOL_FIRESTORE_DOCUMENT", DEFAULT_TREND_POOL_FIRESTORE_DOCUMENT)

    try:
        payload = db.collection(collection).document(document).get()
        if not payload.exists:
            print(f"Trend pool Firestore doc missing, trying local: {collection}/{document}")
            return {}

        parsed = parse_trend_universe_mapping(payload.to_dict())
        if not parsed:
            print(f"Trend pool Firestore payload invalid, trying local: {collection}/{document}")
            return {}

        print(f"Loaded trend pool from Firestore: {collection}/{document} | symbols={','.join(parsed.keys())}")
        return parsed
    except Exception as exc:
        print(f"Firestore trend pool read failed, trying local: {exc}")
        return {}


def load_trend_universe_from_live_pool():
    """Prefer the published live pool, but safely fall back to the static universe."""
    configured_path = os.getenv("TREND_POOL_FILE")
    fallback = STATIC_TREND_UNIVERSE.copy()

    try:
        if configured_path:
            configured_pool_path = Path(configured_path).expanduser()
            if not configured_pool_path.exists():
                print(f"TREND_POOL_FILE not found, fallback to static: {configured_pool_path}")
                return fallback

            payload = json.loads(configured_pool_path.read_text(encoding="utf-8"))
            parsed = parse_trend_universe_mapping(payload)
            if not parsed:
                print(f"TREND_POOL_FILE invalid, fallback to static: {configured_pool_path}")
                return fallback

            print(f"Loaded trend pool file: {configured_pool_path} | symbols={','.join(parsed.keys())}")
            return parsed

        firestore_pool = load_trend_universe_from_firestore()
        if firestore_pool:
            return firestore_pool

        for pool_path in get_default_live_pool_candidates():
            if not pool_path.exists():
                continue

            payload = json.loads(pool_path.read_text(encoding="utf-8"))
            parsed = parse_trend_universe_mapping(payload)
            if not parsed:
                print(f"Pool file invalid, trying next: {pool_path}")
                continue

            print(f"Loaded trend pool file: {pool_path} | symbols={','.join(parsed.keys())}")
            return parsed

        print("No upstream trend pool found, using static TREND_UNIVERSE")
        return fallback
    except Exception as exc:
        print(f"Trend pool load failed, using static: {exc}")
        return fallback


def build_default_state():
    state = {
        "BTCUSDT": {"holding_qty": 0.0, "avg_cost": 0.0},
        "daily_equity_base": 0.0,
        "daily_trend_equity_base": 0.0,
        "last_reset_date": "",
        "is_circuit_broken": False,
        "dca_last_buy_date": "",
        "dca_last_sell_date": "",
        "rotation_pool_last_month": "",
        "rotation_pool_symbols": [],
        "last_btc_status_report_bucket": "",
        RETIRED_TREND_POSITIONS_KEY: {},
    }
    for symbol in TREND_UNIVERSE:
        state[symbol] = default_trend_symbol_state()
    return state


def normalize_trade_state(state):
    normalized = build_default_state()
    if not isinstance(state, dict):
        return normalized

    for key, value in normalized.items():
        if key in TREND_UNIVERSE or key == RETIRED_TREND_POSITIONS_KEY:
            continue
        if isinstance(value, dict):
            current = state.get(key, {})
            merged = value.copy()
            if isinstance(current, dict):
                merged.update(current)
            normalized[key] = merged
        else:
            normalized[key] = state.get(key, value)

    existing_retired = state.get(RETIRED_TREND_POSITIONS_KEY, {})
    retired_positions = {}

    for symbol in TREND_UNIVERSE:
        merged_source = {}
        if isinstance(existing_retired, dict) and is_trend_symbol_state(existing_retired.get(symbol)):
            merged_source.update(existing_retired.get(symbol, {}))
        if is_trend_symbol_state(state.get(symbol)):
            merged_source.update(state.get(symbol, {}))
        normalized[symbol] = normalize_symbol_state(merged_source)

    if isinstance(existing_retired, dict):
        for symbol, payload in existing_retired.items():
            if symbol in TREND_UNIVERSE or not is_trend_symbol_state(payload):
                continue
            merged = normalize_symbol_state(payload)
            if has_active_position(merged):
                retired_positions[symbol] = {
                    **merged,
                    "base_asset": str(payload.get("base_asset") or infer_base_asset(symbol)),
                }

    for symbol, payload in state.items():
        if (
            symbol in normalized
            or symbol == RETIRED_TREND_POSITIONS_KEY
            or not isinstance(symbol, str)
            or not symbol.endswith("USDT")
            or not is_trend_symbol_state(payload)
        ):
            continue
        merged = normalize_symbol_state(payload)
        if has_active_position(merged):
            retired_positions[symbol] = {
                **merged,
                "base_asset": str(payload.get("base_asset") or infer_base_asset(symbol)),
            }

    normalized[RETIRED_TREND_POSITIONS_KEY] = retired_positions
    return normalized


def get_runtime_trend_universe(state):
    runtime = {symbol: meta.copy() for symbol, meta in TREND_UNIVERSE.items()}
    retired_positions = state.get(RETIRED_TREND_POSITIONS_KEY, {})
    if isinstance(retired_positions, dict):
        for symbol, payload in retired_positions.items():
            if symbol in runtime:
                continue
            runtime[symbol] = {
                "base_asset": str(payload.get("base_asset") or infer_base_asset(symbol)),
                "retired": True,
            }
    return runtime


def get_symbol_trade_state(state, symbol):
    if symbol in TREND_UNIVERSE:
        return normalize_symbol_state(state.get(symbol, {}))
    retired_positions = state.get(RETIRED_TREND_POSITIONS_KEY, {})
    if isinstance(retired_positions, dict):
        return normalize_symbol_state(retired_positions.get(symbol, {}))
    return default_trend_symbol_state()


def set_symbol_trade_state(state, symbol, symbol_state):
    normalized_symbol_state = normalize_symbol_state(symbol_state)
    retired_positions = state.setdefault(RETIRED_TREND_POSITIONS_KEY, {})
    if symbol in TREND_UNIVERSE:
        state[symbol] = normalized_symbol_state
        if isinstance(retired_positions, dict):
            retired_positions.pop(symbol, None)
        return

    if not isinstance(retired_positions, dict):
        retired_positions = {}
        state[RETIRED_TREND_POSITIONS_KEY] = retired_positions

    if has_active_position(normalized_symbol_state):
        existing = retired_positions.get(symbol, {})
        retired_positions[symbol] = {
            **normalized_symbol_state,
            "base_asset": str(existing.get("base_asset") or infer_base_asset(symbol)),
        }
    else:
        retired_positions.pop(symbol, None)

# ==========================================
# 1. State persistence and Telegram
# ==========================================
db = firestore.Client()
doc_ref = db.collection('strategy').document('MULTI_ASSET_STATE')

def get_trade_state():
    try:
        doc = doc_ref.get()
        if doc.exists:
            return normalize_trade_state(doc.to_dict())
        return build_default_state()
    except Exception as e:
        print(f"Firestore get state failed: {e}")
        return None

def set_trade_state(data):
    try:
        persisted_state = normalize_trade_state(data)
        doc_ref.set(persisted_state)
    except Exception as e:
        print(f"Firestore write failed: {e}")

def send_tg_msg(token, chat_id, text):
    if not token or not chat_id: return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": f"🤖 加密货币量化助手：\n{text}"}, timeout=10)
    except:
        print("Telegram send failed")

# ==========================================
# 2. Earn and balance helpers
# ==========================================
def get_total_balance(client, asset):
    """Total balance for asset (spot + flexible earn)."""
    total = 0.0
    try:
        spot_info = client.get_asset_balance(asset=asset)
        total += float(spot_info['free']) + float(spot_info['locked'])
    except: pass
    try:
        earn_positions = client.get_simple_earn_flexible_product_position(asset=asset)
        if earn_positions and 'rows' in earn_positions and len(earn_positions['rows']) > 0:
            total += float(earn_positions['rows'][0]['totalAmount'])
    except: pass
    return total

def ensure_asset_available(client, asset, required_amount, tg_token, tg_chat_id):
    """Redeem from flexible earn if spot balance is below required amount."""
    try:
        spot_free = float(client.get_asset_balance(asset=asset)['free'])
        if spot_free >= required_amount: 
            return True 
            
        shortfall = required_amount - spot_free
        earn_positions = client.get_simple_earn_flexible_product_position(asset=asset)
        
        if earn_positions and 'rows' in earn_positions and len(earn_positions['rows']) > 0:
            row = earn_positions['rows'][0]
            product_id = row['productId']
            earn_free = float(row['totalAmount'])
            
            if earn_free > 0:
                redeem_amt = min(shortfall * 1.001, earn_free)  # precision buffer
                redeem_amt = round(redeem_amt, 8)
                client.redeem_simple_earn_flexible_product(productId=product_id, amount=redeem_amt)
                send_tg_msg(tg_token, tg_chat_id, f"🔄 [交易调度] 现货 {asset} 不足，秒赎回: {redeem_amt}")
                time.sleep(3)  # wait for settlement
                return True
    except Exception as e: 
        send_tg_msg(tg_token, tg_chat_id, f"⚠️ [交易调度] {asset} 赎回失败: {e}")
    return False

def manage_usdt_earn_buffer(client, target_buffer, tg_token, tg_chat_id, log_buffer):
    """Keep USDT spot buffer near target by subscribing/redeeming flexible earn."""
    try:
        asset = 'USDT'
        spot_free = float(client.get_asset_balance(asset=asset)['free'])
        
        earn_list = client.get_simple_earn_flexible_product_list(asset=asset)
        if not earn_list or 'rows' not in earn_list or len(earn_list['rows']) == 0:
            return
        product_id = earn_list['rows'][0]['productId']
        
        # Excess spot -> subscribe to earn (tolerance 5 USDT)
        if spot_free > target_buffer + 5.0:
            excess = round(spot_free - target_buffer, 4)
            if excess >= 0.1:
                client.subscribe_simple_earn_flexible_product(productId=product_id, amount=excess)
                msg = f"📥 [资金管家] 现货结余过多，自动存入理财: ${excess:.2f}"
                log_buffer.append(msg)

        # Shortfall -> redeem from earn (tolerance 5 USDT)
        elif spot_free < target_buffer - 5.0:
            shortfall = round(target_buffer - spot_free, 4)
            earn_positions = client.get_simple_earn_flexible_product_position(asset=asset)
            if earn_positions and 'rows' in earn_positions and len(earn_positions['rows']) > 0:
                earn_free = float(earn_positions['rows'][0]['totalAmount'])
                if earn_free > 0:
                    redeem_amt = min(shortfall, earn_free)
                    redeem_amt = round(redeem_amt, 8)
                    client.redeem_simple_earn_flexible_product(productId=product_id, amount=redeem_amt)
                    msg = f"📤 [资金管家] 现货水位偏低，自动补充现货: ${redeem_amt:.2f}"
                    log_buffer.append(msg)
    except Exception as e:
        log_buffer.append(f"⚠️ USDT理财池维护失败: {e}")

def format_qty(client, symbol, qty):
    """Round quantity to exchange LOT_SIZE to avoid filter errors."""
    try:
        info = client.get_symbol_info(symbol)
        step_size = float([f['stepSize'] for f in info['filters'] if f['filterType'] == 'LOT_SIZE'][0])
        precision = int(round(-math.log(step_size, 10), 0))
        return round(math.floor(qty / step_size) * step_size, precision)
    except:
        return round(math.floor(qty * 10000) / 10000, 4)  # fallback

def get_dynamic_btc_target_ratio(total_equity):
    """BTC target weight increases with equity; no hard minimum."""
    safe_equity = max(float(total_equity), 1.0)
    ratio = 0.14 + 0.16 * math.log1p(safe_equity / 10000.0)
    return min(0.65, max(0.0, ratio))


def get_dynamic_btc_base_order(total_equity):
    """Daily DCA base order scales with total equity."""
    return max(15.0, float(total_equity) * 0.0012)


def get_periodic_report_bucket(now_utc, interval_hours):
    safe_interval = max(1, min(24, int(interval_hours)))
    if now_utc.hour % safe_interval != 0:
        return ""
    return now_utc.strftime("%Y%m%d") + f"{now_utc.hour:02d}"


def build_btc_manual_hint(btc_snapshot):
    ahr = btc_snapshot["ahr999"]
    zscore = btc_snapshot["zscore"]
    sell_trigger = btc_snapshot["sell_trigger"]

    if ahr < 0.45:
        return "AHR999 处于极低估区，可关注额外抄底资金安排。"
    if ahr < 0.8:
        return "AHR999 偏低，若有额外现金可考虑分批加大抄底预算。"
    if zscore >= sell_trigger:
        return "Z-Score 已进入系统止盈区，若主观仓位较重可考虑额外落袋。"
    if zscore >= sell_trigger * 0.9:
        return "Z-Score 接近止盈阈值，注意高位风险。"
    return "BTC 估值处于中性区间，优先按系统节奏执行。"


def maybe_send_periodic_btc_status_report(
    state,
    tg_token,
    tg_chat_id,
    now_utc,
    interval_hours,
    total_equity,
    trend_layer_equity,
    trend_daily_pnl,
    btc_price,
    btc_snapshot,
    btc_target_ratio,
):
    report_bucket = get_periodic_report_bucket(now_utc, interval_hours)
    if not report_bucket or state.get("last_btc_status_report_bucket") == report_bucket:
        return

    gate_text = "开启" if btc_snapshot["regime_on"] else "关闭"
    text = (
        "🛰️ [策略心跳]\n"
        f"时间(UTC): {now_utc.strftime('%Y-%m-%d %H:%M')}\n"
        f"总净值: ${total_equity:.2f}\n"
        f"趋势层权益: ${trend_layer_equity:.2f} ({trend_daily_pnl:.2%})\n"
        f"BTC 现价: ${btc_price:.2f}\n"
        f"AHR999: {btc_snapshot['ahr999']:.3f}\n"
        f"Z-Score: {btc_snapshot['zscore']:.2f} / 阈值 {btc_snapshot['sell_trigger']:.2f}\n"
        f"BTC 目标仓位: {btc_target_ratio:.1%}\n"
        f"BTC 闸门: {gate_text}\n"
        f"提示: {build_btc_manual_hint(btc_snapshot)}"
    )
    send_tg_msg(tg_token, tg_chat_id, text)
    state["last_btc_status_report_bucket"] = report_bucket


def fetch_daily_indicators(client, symbol, lookback_days=420):
    """Daily indicators for one symbol (rotation and risk)."""
    klines = client.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, f"{lookback_days} days ago UTC")
    if not klines:
        return None

    df = pd.DataFrame(klines).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "vol"]
    df[["high", "low", "close", "vol"]] = df[["high", "low", "close", "vol"]].astype(float)
    df["quote_vol"] = df["close"] * df["vol"]

    df["sma20"] = df["close"].rolling(20).mean()
    df["sma60"] = df["close"].rolling(60).mean()
    df["sma200"] = df["close"].rolling(200).mean()
    df["roc20"] = df["close"].pct_change(20)
    df["roc60"] = df["close"].pct_change(60)
    df["roc120"] = df["close"].pct_change(120)
    df["vol20"] = df["close"].pct_change().rolling(20).std()
    df["tr"] = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = df["tr"].rolling(14).mean()
    df["avg_quote_vol_30"] = df["quote_vol"].rolling(30).mean()
    df["avg_quote_vol_90"] = df["quote_vol"].rolling(90).mean()
    df["avg_quote_vol_180"] = df["quote_vol"].rolling(180).mean()
    df["trend_persist_90"] = (df["close"] > df["sma200"]).rolling(90).mean()
    df["age_days"] = np.arange(1, len(df) + 1)

    latest = df.iloc[-1]
    required_fields = [
        "close",
        "sma20",
        "sma60",
        "sma200",
        "roc20",
        "roc60",
        "roc120",
        "vol20",
        "atr14",
        "avg_quote_vol_30",
        "avg_quote_vol_90",
        "avg_quote_vol_180",
        "trend_persist_90",
    ]
    if any(pd.isna(latest[field]) for field in required_fields):
        return None

    return {
        "close": float(latest["close"]),
        "sma20": float(latest["sma20"]),
        "sma60": float(latest["sma60"]),
        "sma200": float(latest["sma200"]),
        "roc20": float(latest["roc20"]),
        "roc60": float(latest["roc60"]),
        "roc120": float(latest["roc120"]),
        "vol20": float(latest["vol20"]),
        "atr14": float(latest["atr14"]),
        "avg_quote_vol_30": float(latest["avg_quote_vol_30"]),
        "avg_quote_vol_90": float(latest["avg_quote_vol_90"]),
        "avg_quote_vol_180": float(latest["avg_quote_vol_180"]),
        "trend_persist_90": float(latest["trend_persist_90"]),
        "age_days": int(latest["age_days"]),
    }


def fetch_btc_market_snapshot(client, btc_price, lookback_days=700, log_buffer=None):
    """Single BTC daily series for DCA and trend gate."""
    try:
        klines = client.get_historical_klines("BTCUSDT", Client.KLINE_INTERVAL_1DAY, f"{lookback_days} days ago UTC")
    except Exception as e:
        if log_buffer is not None:
            log_buffer.append(f"⚠️ BTC daily fetch failed: {e}")
        return None

    if not klines:
        if log_buffer is not None:
            log_buffer.append("⚠️ BTC daily data empty.")
        return None

    df = pd.DataFrame(klines).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "vol"]
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["close"] = df["close"].astype(float)
    df["ma200"] = df["close"].rolling(200).mean()
    df["std200"] = df["close"].rolling(200).std()
    df["zscore"] = (df["close"] - df["ma200"]) / df["std200"]
    df["geom200"] = np.exp(np.log(df["close"]).rolling(200).mean())
    df["sell_trigger"] = df["zscore"].rolling(365).quantile(0.95).clip(lower=2.5)
    df["ma200_slope"] = df["ma200"].pct_change(20)
    df["btc_roc20"] = df["close"].pct_change(20)
    df["btc_roc60"] = df["close"].pct_change(60)
    df["btc_roc120"] = df["close"].pct_change(120)

    # All core fields must be non-NaN
    required_fields = ["ma200", "zscore", "geom200", "sell_trigger", "ma200_slope", "btc_roc20", "btc_roc60", "btc_roc120"]
    valid = df.dropna(subset=required_fields)
    if valid.empty:
        if log_buffer is not None:
            last_time = df["time"].iloc[-1] if not df.empty else None
            log_buffer.append(f"⚠️ BTC data insufficient for MA200/Z-Score. len={len(df)}, last_time={last_time}")
        return None

    latest = valid.iloc[-1]
    regime_on = btc_price > float(latest["ma200"]) and float(latest["ma200_slope"]) > 0
    return {
        "ma200": float(latest["ma200"]),
        "zscore": float(latest["zscore"]),
        "geom200": float(latest["geom200"]),
        "sell_trigger": float(latest["sell_trigger"]),
        "ma200_slope": float(latest["ma200_slope"]),
        "ahr999": float(btc_price / latest["geom200"]),
        "btc_roc20": float(latest["btc_roc20"]),
        "btc_roc60": float(latest["btc_roc60"]),
        "btc_roc120": float(latest["btc_roc120"]),
        "regime_on": regime_on,
    }


def rank_normalize(values):
    if not values:
        return {}
    series = pd.Series(values, dtype=float)
    ranked = series.rank(method="average")
    denom = max(len(series) - 1, 1)
    normalized = (ranked - 1) / denom
    return normalized.to_dict()


def build_stable_quality_pool(indicators_map, btc_snapshot, previous_pool):
    records = []
    for symbol, indicators in indicators_map.items():
        if indicators is None:
            continue

        required_fields = [
            "close",
            "sma20",
            "sma60",
            "sma200",
            "roc20",
            "roc60",
            "roc120",
            "vol20",
            "avg_quote_vol_30",
            "avg_quote_vol_90",
            "avg_quote_vol_180",
            "trend_persist_90",
            "age_days",
        ]
        if any(indicators.get(field) is None for field in required_fields):
            continue
        if indicators["age_days"] < MIN_HISTORY_DAYS:
            continue
        if indicators["avg_quote_vol_180"] < MIN_AVG_QUOTE_VOL_180:
            continue
        if indicators["vol20"] <= 0:
            continue

        rel_20 = indicators["roc20"] - btc_snapshot["btc_roc20"]
        rel_60 = indicators["roc60"] - btc_snapshot["btc_roc60"]
        rel_120 = indicators["roc120"] - btc_snapshot["btc_roc120"]
        price_vs_ma20 = indicators["close"] / indicators["sma20"] - 1.0
        price_vs_ma60 = indicators["close"] / indicators["sma60"] - 1.0
        price_vs_ma200 = indicators["close"] / indicators["sma200"] - 1.0
        abs_momentum = 0.5 * indicators["roc20"] + 0.3 * indicators["roc60"] + 0.2 * indicators["roc120"]
        liquidity_stability = min(
            indicators["avg_quote_vol_30"],
            indicators["avg_quote_vol_90"],
            indicators["avg_quote_vol_180"],
        ) / max(
            indicators["avg_quote_vol_30"],
            indicators["avg_quote_vol_90"],
            indicators["avg_quote_vol_180"],
        )

        records.append(
            {
                "symbol": symbol,
                "liquidity": math.log1p(indicators["avg_quote_vol_180"]),
                "stability": liquidity_stability,
                "relative_strength_core": 0.20 * rel_20 + 0.45 * rel_60 + 0.35 * rel_120,
                "trend_quality": 0.25 * price_vs_ma20 + 0.35 * price_vs_ma60 + 0.40 * price_vs_ma200,
                "persistence": indicators["trend_persist_90"],
                "risk_adjusted_momentum": abs_momentum / indicators["vol20"],
                "bonus": POOL_MEMBERSHIP_BONUS if symbol in previous_pool else 0.0,
            }
        )

    if not records:
        return [], []

    liq_rank = rank_normalize({item["symbol"]: item["liquidity"] for item in records})
    stability_rank = rank_normalize({item["symbol"]: item["stability"] for item in records})
    rel_rank = rank_normalize({item["symbol"]: item["relative_strength_core"] for item in records})
    trend_rank = rank_normalize({item["symbol"]: item["trend_quality"] for item in records})
    persist_rank = rank_normalize({item["symbol"]: item["persistence"] for item in records})
    risk_rank = rank_normalize({item["symbol"]: item["risk_adjusted_momentum"] for item in records})

    ranking = []
    for item in records:
        symbol = item["symbol"]
        score = (
            0.24 * trend_rank[symbol]
            + 0.20 * persist_rank[symbol]
            + 0.18 * liq_rank[symbol]
            + 0.14 * stability_rank[symbol]
            + 0.14 * rel_rank[symbol]
            + 0.10 * risk_rank[symbol]
            + item["bonus"]
        )
        ranking.append(
            {
                "symbol": symbol,
                "score": score,
                "relative_strength_core": item["relative_strength_core"],
                "trend_quality": item["trend_quality"],
            }
        )

    ranking.sort(key=lambda item: (item["score"], item["relative_strength_core"], item["trend_quality"]), reverse=True)
    selected_pool = [item["symbol"] for item in ranking[:TREND_POOL_SIZE]]
    return selected_pool, ranking


def refresh_rotation_pool(state, indicators_map, btc_snapshot):
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    available_symbols = set(TREND_UNIVERSE)
    cached_pool = [symbol for symbol in state.get("rotation_pool_symbols", []) if symbol in available_symbols]
    if state.get("rotation_pool_last_month") == current_month and cached_pool:
        return cached_pool, []

    selected_pool, ranking = build_stable_quality_pool(
        indicators_map,
        btc_snapshot,
        set(cached_pool),
    )
    if selected_pool:
        state["rotation_pool_last_month"] = current_month
        state["rotation_pool_symbols"] = selected_pool
        return selected_pool, ranking

    fallback_pool = cached_pool if cached_pool else list(TREND_UNIVERSE.keys())[:TREND_POOL_SIZE]
    state["rotation_pool_last_month"] = current_month
    state["rotation_pool_symbols"] = fallback_pool
    return fallback_pool, []


def select_rotation_weights(indicators_map, prices, btc_snapshot, candidate_pool, top_n):
    """Pick final trend holdings from monthly pool by relative BTC strength."""
    btc_regime_on = btc_snapshot["regime_on"]
    if not btc_regime_on:
        return {}

    candidates = []
    for symbol in candidate_pool:
        indicators = indicators_map.get(symbol)
        if indicators is None:
            continue

        price = prices.get(symbol, 0.0)
        if (
            price <= indicators["sma20"]
            or price <= indicators["sma60"]
            or price <= indicators["sma200"]
            or indicators["vol20"] <= 0
        ):
            continue

        rel_20 = indicators["roc20"] - btc_snapshot["btc_roc20"]
        rel_60 = indicators["roc60"] - btc_snapshot["btc_roc60"]
        rel_120 = indicators["roc120"] - btc_snapshot["btc_roc120"]
        abs_momentum = 0.5 * indicators["roc20"] + 0.3 * indicators["roc60"] + 0.2 * indicators["roc120"]
        relative_score = (0.5 * rel_20 + 0.3 * rel_60 + 0.2 * rel_120) / indicators["vol20"]

        if relative_score > 0 and abs_momentum > 0:
            candidates.append((symbol, relative_score, indicators["vol20"], abs_momentum))

    candidates.sort(key=lambda item: item[1], reverse=True)
    selected = candidates[:top_n]
    if not selected:
        return {}

    inv_vol_sum = sum(1.0 / item[2] for item in selected)
    return {
        item[0]: {
            "weight": (1.0 / item[2]) / inv_vol_sum,
            "relative_score": item[1],
            "abs_momentum": item[3],
        }
        for item in selected
    }


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
def main():
    # --- Config ---
    global TREND_UNIVERSE
    TREND_UNIVERSE = load_trend_universe_from_live_pool()
    ATR_MULTIPLIER = 2.5
    CIRCUIT_BREAKER_PCT = -0.05
    MIN_BNB_VALUE, BUY_BNB_AMOUNT = 10.0, 15.0
    BTC_STATUS_REPORT_INTERVAL_HOURS = max(1, min(24, get_env_int("BTC_STATUS_REPORT_INTERVAL_HOURS", 24)))

    api_key = os.getenv('BINANCE_API_KEY')
    api_secret = os.getenv('BINANCE_API_SECRET')
    tg_token = os.getenv('TG_TOKEN')
    tg_chat_id = os.getenv('TG_CHAT_ID')

    log_buffer = []
    client = None
    max_retries = 3

    try:
        # --- API and state ---
        for i in range(max_retries):
            try:
                client = Client(api_key, api_secret, {"timeout": 30}) 
                client.ping()
                break
            except Exception as e:
                if i < max_retries - 1: time.sleep(3) 
                else:
                    send_tg_msg(tg_token, tg_chat_id, f"❌ 无法连接 Binance API: {str(e)}")
                    return

        state = get_trade_state()
        if state is None:
            raise Exception(
                "Failed to load Firestore state. Check GCP credentials (GCP_SA_KEY / GOOGLE_APPLICATION_CREDENTIALS), "
                "service account validity, and Firestore API enablement."
            )
        state = normalize_trade_state(state)
        runtime_trend_universe = get_runtime_trend_universe(state)
        
        # --- Balances and allocation ---
        u_total = get_total_balance(client, 'USDT')
        bnb_total = get_total_balance(client, BNB_FUEL_ASSET)
        bnb_price = float(client.get_avg_price(symbol=BNB_FUEL_SYMBOL)['price'])
        
        # USDT spot buffer: 5% of equity, clamp 50–300
        dynamic_usdt_buffer = max(50.0, min(u_total * 0.05, 300.0))

        # BNB auto top-up for fees
        if bnb_total * bnb_price < MIN_BNB_VALUE and u_total >= BUY_BNB_AMOUNT:
            ensure_asset_available(client, 'USDT', BUY_BNB_AMOUNT, tg_token, tg_chat_id)
            try:
                client.order_market_buy(symbol='BNBUSDT', quoteOrderQty=BUY_BNB_AMOUNT)
                u_total -= BUY_BNB_AMOUNT
                bnb_total += (BUY_BNB_AMOUNT * 0.995) / bnb_price
                log_buffer.append(f"🔧 BNB 自动补仓完成")
            except Exception as e: send_tg_msg(tg_token, tg_chat_id, f"⚠️ BNB补仓失败: {e}")

        # Virtual ledger: prices and balances
        prices, balances = {}, {}
        trend_val = 0.0
        for sym, cfg in runtime_trend_universe.items():
            base_asset = cfg['base_asset']
            p = float(client.get_avg_price(symbol=sym)['price'])
            b = get_total_balance(client, base_asset)
            prices[sym], balances[sym] = p, b
            trend_val += (b * p)
            
        btc_p = float(client.get_avg_price(symbol='BTCUSDT')['price'])
        btc_b = get_total_balance(client, 'BTC')
        prices['BTCUSDT'], balances['BTCUSDT'] = btc_p, btc_b
        dca_val = (btc_b * btc_p)
        fuel_val = bnb_total * bnb_price
        btc_snapshot = fetch_btc_market_snapshot(client, btc_p, log_buffer=log_buffer)
        if btc_snapshot is None:
            raise Exception("BTC indicators insufficient for rotation and DCA")

        trend_indicators = {}
        for symbol in TREND_UNIVERSE:
            trend_indicators[symbol] = fetch_daily_indicators(client, symbol)

        active_trend_pool, pool_ranking = refresh_rotation_pool(state, trend_indicators, btc_snapshot)
        selected_candidates = select_rotation_weights(
            trend_indicators,
            prices,
            btc_snapshot,
            active_trend_pool,
            ROTATION_TOP_N,
        )

        total_equity = u_total + fuel_val + trend_val + dca_val
        btc_target_ratio = get_dynamic_btc_target_ratio(total_equity)
        trend_target_ratio = 1.0 - btc_target_ratio
        trend_usdt_pool = max(0, min(u_total, (total_equity * trend_target_ratio) - trend_val))
        dca_usdt_pool = max(0, min(u_total - trend_usdt_pool, (total_equity * btc_target_ratio) - dca_val))
        trend_layer_equity = trend_val + trend_usdt_pool
        
        # --- Daily circuit breaker ---
        now_utc = datetime.now(timezone.utc)
        today_utc = now_utc.strftime("%Y-%m-%d")
        today_id_str = now_utc.strftime("%Y%m%d")

        if state.get('last_reset_date') != today_utc:
            state.update({
                'daily_equity_base': total_equity,
                'daily_trend_equity_base': trend_layer_equity,
                'last_reset_date': today_utc,
                'is_circuit_broken': False,
            })
            set_trade_state(state)

        daily_pnl = (total_equity - state['daily_equity_base']) / state['daily_equity_base'] if state.get('daily_equity_base', 0) > 0 else 0
        trend_daily_pnl = (
            (trend_layer_equity - state['daily_trend_equity_base']) / state['daily_trend_equity_base']
            if state.get('daily_trend_equity_base', 0) > 0
            else 0
        )
        
        log_buffer.append(f"━━━━━━━━━ 📦 全景资产报告 ━━━━━━━━━")
        log_buffer.append(f"💰 总 净 值 : ${total_equity:.2f} (组合日内: {daily_pnl:.2%})")
        log_buffer.append(f"🪙 BTC 核心仓目标占比: {btc_target_ratio:.1%} | 现值 ${dca_val:.2f} | 可用 ${dca_usdt_pool:.2f}")
        log_buffer.append(f"🔥 趋势池目标占比: {trend_target_ratio:.1%} | 现值 ${trend_val:.2f} | 可用 ${trend_usdt_pool:.2f} | 趋势层日内: {trend_daily_pnl:.2%}")
        log_buffer.append(f"⛽ BNB 燃料仓: ${fuel_val:.2f}")
        log_buffer.append(f"🚦 BTC 闸门: {'开启' if btc_snapshot['regime_on'] else '关闭'} | Ahr999={btc_snapshot['ahr999']:.3f} | Z-Score={btc_snapshot['zscore']:.2f}")
        log_buffer.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if state.get('is_circuit_broken'):
            log_buffer.insert(0, f"🔒 熔断锁死中。NAV: ${total_equity:.2f}")
            return 

        if trend_daily_pnl <= CIRCUIT_BREAKER_PCT:
            for sym, cfg in runtime_trend_universe.items():
                tradable_qty = balances[sym]
                if tradable_qty * prices[sym] > 10:
                    try:
                        base_asset = cfg['base_asset']
                        qty = format_qty(client, sym, tradable_qty)
                        ensure_asset_available(client, base_asset, qty, tg_token, tg_chat_id)
                        client.order_market_sell(symbol=sym, quantity=qty)
                        balances[sym] = max(0.0, balances[sym] - qty)
                        set_symbol_trade_state(
                            state,
                            sym,
                            {"is_holding": False, "entry_price": 0.0, "highest_price": 0.0},
                        )
                    except Exception as e: send_tg_msg(tg_token, tg_chat_id, f"❌ 熔断抛售失败 {sym}: {e}")
            state.update({"is_circuit_broken": True})
            set_trade_state(state)
            send_tg_msg(tg_token, tg_chat_id, f"🚫 触发趋势层每日熔断({trend_daily_pnl:.2%})！趋势策略清空，BTC 定投保持。")
            return 

        pool_text = "、".join(active_trend_pool) if active_trend_pool else "暂无可用池"
        ranking_preview = "、".join(
            f"{item['symbol']}(SQ:{item['score']:.2f})"
            for item in pool_ranking[:TREND_POOL_SIZE]
        ) if pool_ranking else "沿用上月趋势池"
        selected_text = "、".join(
            f"{symbol}({meta['weight']:.0%},RS:{meta['relative_score']:.2f})"
            for symbol, meta in selected_candidates.items()
        ) if selected_candidates else "无候选，保持防守"
        log_buffer.append(f"🗓️ 月更趋势池: {pool_text}")
        log_buffer.append(f"🧪 稳健质量排名: {ranking_preview}")
        log_buffer.append(f"🎯 轮动候选: {selected_text}")

        # --- Trend: monthly pool + relative-BTC rotation ---
        for symbol, config in runtime_trend_universe.items():
            curr_price = prices[symbol]
            indicators = trend_indicators.get(symbol)
            st = get_symbol_trade_state(state, symbol)
            
            if st['is_holding']:
                sell_reason = ""
                if not indicators:
                    sell_reason = "Missing indicators"
                else:
                    st['highest_price'] = max(st['highest_price'], curr_price)
                    stop_p = st['highest_price'] - (ATR_MULTIPLIER * indicators['atr14'])
                if symbol not in selected_candidates and not sell_reason:
                    sell_reason = "Rotated out of candidates"
                elif indicators and curr_price < indicators['sma60']: sell_reason = "Below SMA60"
                elif indicators and curr_price < stop_p: sell_reason = f"ATR trailing stop (${stop_p:.2f})"

                if sell_reason:
                    sell_order_id = f"T_SELL_{symbol}_{int(time.time())}"
                    try:
                        tradable_qty = balances[symbol]
                        qty = format_qty(client, symbol, tradable_qty)
                        if qty <= 0:
                            set_symbol_trade_state(
                                state,
                                symbol,
                                {"is_holding": False, "entry_price": 0.0, "highest_price": 0.0},
                            )
                            continue
                        ensure_asset_available(client, config['base_asset'], qty, tg_token, tg_chat_id)
                        client.order_market_sell(symbol=symbol, quantity=qty, newClientOrderId=sell_order_id)
                        balances[symbol] = max(0.0, balances[symbol] - qty)
                        u_total += qty * curr_price
                        set_symbol_trade_state(
                            state,
                            symbol,
                            {"is_holding": False, "entry_price": 0.0, "highest_price": 0.0},
                        )
                        set_trade_state(state) 
                        send_tg_msg(tg_token, tg_chat_id, f"🚨 [趋势卖出] {symbol}\n原因: {sell_reason}\n价格: ${curr_price:.2f}")
                    except Exception as e: pass
            else:
                candidate_meta = selected_candidates.get(symbol)
                if indicators and candidate_meta and curr_price > indicators['sma20'] and curr_price > indicators['sma60'] and curr_price > indicators['sma200']:
                    current_trend_val = sum(balances[sym] * prices[sym] for sym in runtime_trend_universe)
                    current_dca_val = balances['BTCUSDT'] * prices['BTCUSDT']
                    current_total_equity = u_total + fuel_val + current_trend_val + current_dca_val
                    current_btc_target_ratio = get_dynamic_btc_target_ratio(current_total_equity)
                    current_trend_ratio = 1.0 - current_btc_target_ratio
                    trend_usdt_pool = max(0, min(u_total, (current_total_equity * current_trend_ratio) - current_trend_val))
                    buy_u = trend_usdt_pool * candidate_meta['weight']

                    if buy_u > 15:
                        buy_order_id = f"T_BUY_{symbol}_{int(time.time())}"
                        try:
                            qty = format_qty(client, symbol, buy_u*0.985/curr_price)
                            usdt_cost = qty * curr_price
                            ensure_asset_available(client, 'USDT', usdt_cost, tg_token, tg_chat_id)
                            client.order_market_buy(symbol=symbol, quantity=qty, newClientOrderId=buy_order_id)
                            set_symbol_trade_state(
                                state,
                                symbol,
                                {"is_holding": True, "entry_price": curr_price, "highest_price": curr_price},
                            )
                            balances[symbol] += qty
                            u_total -= usdt_cost
                            set_trade_state(state)
                            send_tg_msg(
                                tg_token,
                                tg_chat_id,
                                f"✅ [趋势买入] {symbol}\n现价: ${curr_price:.2f}\n金额: ${buy_u:.2f}\n轮动权重: {candidate_meta['weight']:.0%}\n相对BTC分数: {candidate_meta['relative_score']:.2f}"
                            )
                        except Exception as e: pass
            
            st = get_symbol_trade_state(state, symbol)
            score_text = ""
            if indicators and indicators['vol20'] > 0:
                rel_score = (
                    0.5 * (indicators['roc20'] - btc_snapshot['btc_roc20'])
                    + 0.3 * (indicators['roc60'] - btc_snapshot['btc_roc60'])
                    + 0.2 * (indicators['roc120'] - btc_snapshot['btc_roc120'])
                ) / indicators['vol20']
                abs_momentum = 0.5 * indicators['roc20'] + 0.3 * indicators['roc60'] + 0.2 * indicators['roc120']
                score_text = f" | 相对BTC: {rel_score:.2f} | 动量: {abs_momentum:.2%}"
            log_buffer.append(f" └ {symbol}: {'📈持仓' if st['is_holding'] else '💤空仓'} | 现价: ${curr_price:.4f}{score_text}")

        current_trend_val = sum(balances[sym] * prices[sym] for sym in runtime_trend_universe)
        dca_val = balances['BTCUSDT'] * prices['BTCUSDT']
        total_equity = u_total + fuel_val + current_trend_val + dca_val
        btc_target_ratio = get_dynamic_btc_target_ratio(total_equity)
        trend_target_ratio = 1.0 - btc_target_ratio
        trend_usdt_pool = max(0, min(u_total, (total_equity * trend_target_ratio) - current_trend_val))
        dca_usdt_pool = max(0, min(u_total - trend_usdt_pool, (total_equity * btc_target_ratio) - dca_val))
        trend_layer_equity = current_trend_val + trend_usdt_pool
        trend_daily_pnl = (
            (trend_layer_equity - state['daily_trend_equity_base']) / state['daily_trend_equity_base']
            if state.get('daily_trend_equity_base', 0) > 0
            else 0
        )

        # --- BTC DCA ---
        if dca_usdt_pool > 10 or dca_val > 10:
            ahr = btc_snapshot['ahr999']
            z = btc_snapshot['zscore']
            sell_trigger = btc_snapshot['sell_trigger']
            log_buffer.append(f"🧭 BTC 囤币雷达: Ahr999={ahr:.3f} | Z-Score={z:.2f} (阈值:{sell_trigger:.2f})")

            base = get_dynamic_btc_base_order(total_equity)
            multiplier = 0
            if ahr < 0.45: multiplier = 5
            elif ahr < 0.8: multiplier = 2
            elif ahr < 1.2: multiplier = 1
            
            # At most one DCA buy per day (Firestore)
            if multiplier > 0 and dca_usdt_pool > 15 and state.get('dca_last_buy_date') != today_id_str:
                dca_buy_id = f"D_BUY_BTC_{int(time.time())}"
                try:
                    q = format_qty(client, 'BTCUSDT', min(dca_usdt_pool, base*multiplier)*0.985/btc_p)
                    ensure_asset_available(client, 'USDT', q * btc_p, tg_token, tg_chat_id)
                    client.order_market_buy(symbol='BTCUSDT', quantity=q, newClientOrderId=dca_buy_id)
                    balances['BTCUSDT'] += q
                    u_total -= q * btc_p
                    state['dca_last_buy_date'] = today_id_str
                    send_tg_msg(tg_token, tg_chat_id, f"🛡️ [定投建仓] BTC 买入\nAhr999: {ahr:.2f}\n目标仓位: {btc_target_ratio:.1%}\n数量: {q} BTC")
                except BinanceAPIException as e:
                    pass
            
            # At most one DCA sell per day (Firestore)
            if z > sell_trigger and dca_val > 20 and state.get('dca_last_sell_date') != today_id_str:
                dca_sell_id = f"D_SELL_BTC_{int(time.time())}"
                try:
                    sell_pct = 0.1
                    if z > 4.0: sell_pct = 0.3
                    if z > 5.0: sell_pct = 0.5
                    
                    q = format_qty(client, 'BTCUSDT', balances['BTCUSDT']*sell_pct)
                    ensure_asset_available(client, 'BTC', q, tg_token, tg_chat_id)
                    client.order_market_sell(symbol='BTCUSDT', quantity=q, newClientOrderId=dca_sell_id)
                    balances['BTCUSDT'] = max(0.0, balances['BTCUSDT'] - q)
                    u_total += q * btc_p
                    state['dca_last_sell_date'] = today_id_str
                    send_tg_msg(tg_token, tg_chat_id, f"💰 [定投止盈] BTC 逃顶\n比例: {sell_pct*100}%\n数量: {q} BTC")
                except BinanceAPIException as e:
                    pass

        # --- USDT earn buffer ---
        manage_usdt_earn_buffer(client, dynamic_usdt_buffer, tg_token, tg_chat_id, log_buffer)

        # --- Periodic BTC status report ---
        maybe_send_periodic_btc_status_report(
            state,
            tg_token,
            tg_chat_id,
            now_utc,
            BTC_STATUS_REPORT_INTERVAL_HOURS,
            total_equity,
            trend_layer_equity,
            trend_daily_pnl,
            btc_p,
            btc_snapshot,
            btc_target_ratio,
        )

        set_trade_state(state)

    except Exception as e:
        traceback.print_exc()
        try: send_tg_msg(tg_token, tg_chat_id, f"❌ 系统崩溃:\n{str(e)[:200]}")
        except: pass
        sys.exit(1)
    
    finally:
        print("\n".join(log_buffer))

if __name__ == "__main__":
    main()
