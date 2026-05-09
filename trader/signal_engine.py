"""
Signal Engine — runs CUSUM/ATR/EMA indicator pipeline and builds full market context.
Reuses bgc_backtest.calc_indicators() so Python and MQL5 logic stay in sync.
"""

import os
import sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))
from bgc_backtest import calc_indicators, BacktestParams, Regime


def get_regime(df: pd.DataFrame, params: BacktestParams = None) -> dict:
    """Run indicator pipeline on OHLCV dataframe and return last-bar signal state."""
    if params is None:
        params = BacktestParams()
    df = calc_indicators(df.copy(), params)
    last = df.iloc[-1]
    return {
        "regime":    int(last["regime"]),
        "cusum_pos": float(last["cusum_pos"]),
        "cusum_neg": float(last["cusum_neg"]),
        "atr":       float(last["atr"]),
        "ema":       float(last["ema"]),
        "std":       float(last["std"]),
        "ext_dir":   int(last["ext_dir"]),
        "z_score":   float(last["z"]),
    }


def get_context(bridge, magic: int = 20260505, params: BacktestParams = None) -> dict:
    """Pull live data from MT5 via bridge, compute signals, and return full context dict."""
    import MetaTrader5 as mt5

    df = bridge.get_ohlcv(bridge.symbol, mt5.TIMEFRAME_M15, 200)
    if df is None or len(df) < 50:
        raise RuntimeError(f"Insufficient bars from MT5 (got {len(df) if df is not None else 0})")

    signals  = get_regime(df, params)
    account  = bridge.get_account()
    positions = bridge.get_positions(magic=magic)
    pending  = bridge.get_pending_orders(magic=magic)
    closed   = bridge.get_closed_deals(magic=magic, count=10)
    sym_info = bridge.get_symbol_info()

    # Recent 5-bar OHLC summary for Claude's briefing
    recent_bars = []
    for _, row in df.tail(5).iterrows():
        recent_bars.append({
            "open":  round(float(row["open"]),  2),
            "high":  round(float(row["high"]),  2),
            "low":   round(float(row["low"]),   2),
            "close": round(float(row["close"]), 2),
        })

    # Drawdown from recent equity peak (approximate from account data)
    peak_equity = max(account.get("balance", 0), account.get("equity", 0))
    equity      = account.get("equity", 0)
    dd_pct      = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0.0

    return {
        "timestamp":    pd.Timestamp.now().isoformat(),
        "symbol":       bridge.symbol,
        "signals":      signals,
        "account":      account,
        "dd_pct":       round(dd_pct, 2),
        "positions":    positions,
        "pending_orders": pending,
        "recent_closed":  closed,
        "recent_bars":  recent_bars,
        "bid":          sym_info.get("bid", 0),
        "ask":          sym_info.get("ask", 0),
        "symbol_info":  sym_info,
    }
