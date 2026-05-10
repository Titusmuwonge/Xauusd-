"""
AWB SetupTrader — Python Backtester (numpy-optimized)
Implements both SMC setups using EURUSD 5M, 15M, 1H data.
Sessions in EAT (UTC+3, Africa/Nairobi).
"""

import pandas as pd
import numpy as np
import pytz
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR   = Path(__file__).parent.parent
EAT        = pytz.timezone("Africa/Nairobi")

SL_PIPS    = 15
TP_PIPS    = 34
PIP_SIZE   = 0.0001          # EURUSD 5-digit broker
RISK_PCT   = 0.01            # 1% of equity per trade
INIT_BAL   = 10_000.0

SWING_LB         = 10
FVG_LB           = 30
MAN_LB           = 5
MIN_FVG_PIPS     = 5
MIN_ENGULF_PIPS  = 3
BOS_MAX_AGE      = 8
COOLDOWN_BARS    = 24

LONDON_START = 10            # EAT hour
LONDON_END   = 12
NY_START     = 15
NY_END       = 17

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_5m() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "EURUSD5.csv", sep="\t", header=None,
                     names=["time", "open", "high", "low", "close", "volume"],
                     parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def load_htf(filename: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / filename, parse_dates=["Timestamp"])
    df = df.rename(columns={"Timestamp": "time"})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Session helper  (vectorised against EAT timezone)
# ---------------------------------------------------------------------------

def in_session_mask(times: pd.DatetimeIndex) -> np.ndarray:
    eat_hours = times.tz_convert(EAT).hour
    return ((eat_hours >= LONDON_START) & (eat_hours < LONDON_END)) | \
           ((eat_hours >= NY_START)     & (eat_hours < NY_END))


# ---------------------------------------------------------------------------
# Numpy-array detectors  (operate on slices ending at idx-inclusive)
# Index convention: idx is the LAST CLOSED bar (newest)
# ---------------------------------------------------------------------------

def find_swing_high(high: np.ndarray, idx: int, lookback: int) -> float:
    if idx < lookback + 2:
        return np.nan
    best = np.nan
    # window ends at idx-1 (most recent CLOSED swing candidate)
    for i in range(idx - lookback, idx):
        if 0 < i < len(high) - 1:
            if high[i] > high[i-1] and high[i] > high[i+1]:
                if np.isnan(best) or high[i] > best:
                    best = high[i]
    return best


def find_swing_low(low: np.ndarray, idx: int, lookback: int) -> float:
    if idx < lookback + 2:
        return np.nan
    best = np.nan
    for i in range(idx - lookback, idx):
        if 0 < i < len(low) - 1:
            if low[i] < low[i-1] and low[i] < low[i+1]:
                if np.isnan(best) or low[i] < best:
                    best = low[i]
    return best


def detect_manipulation_up(high: np.ndarray, close: np.ndarray, idx: int,
                           swing_high: float, lookback: int) -> bool:
    if np.isnan(swing_high):
        return False
    s = max(0, idx - lookback)
    for i in range(s, idx + 1):
        if high[i] > swing_high and close[i] < swing_high:
            return True
    return False


def detect_manipulation_down(low: np.ndarray, close: np.ndarray, idx: int,
                             swing_low: float, lookback: int) -> bool:
    if np.isnan(swing_low):
        return False
    s = max(0, idx - lookback)
    for i in range(s, idx + 1):
        if low[i] < swing_low and close[i] > swing_low:
            return True
    return False


def detect_bos_bearish(close: np.ndarray, idx: int, swing_low: float,
                       max_age: int) -> bool:
    if np.isnan(swing_low):
        return False
    s = max(0, idx - max_age)
    return bool(np.any(close[s:idx+1] < swing_low))


def detect_bos_bullish(close: np.ndarray, idx: int, swing_high: float,
                       max_age: int) -> bool:
    if np.isnan(swing_high):
        return False
    s = max(0, idx - max_age)
    return bool(np.any(close[s:idx+1] > swing_high))


def detect_fvg_bearish(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                       idx: int, lookback: int, min_pips: float):
    """
    Bearish FVG: bar A (i-1).low > bar C (i+1).high
    Plus bar B (i) must be a strong bearish candle (body >= 3 pips).
    Returns (fvg_high, fvg_low) of the most recent qualifying gap.
    """
    min_size = min_pips * PIP_SIZE
    body_min = 3 * PIP_SIZE
    s = max(1, idx - lookback)
    for i in range(idx - 1, s, -1):
        if i + 1 > idx:
            continue
        gap = l[i-1] - h[i+1]
        if gap >= min_size:
            body = o[i] - c[i]
            if c[i] < o[i] and body >= body_min:
                return l[i-1], h[i+1]
    return np.nan, np.nan


def detect_fvg_bullish(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                       idx: int, lookback: int, min_pips: float):
    """Bullish FVG: bar C (i+1).low > bar A (i-1).high"""
    min_size = min_pips * PIP_SIZE
    body_min = 3 * PIP_SIZE
    s = max(1, idx - lookback)
    for i in range(idx - 1, s, -1):
        if i + 1 > idx:
            continue
        gap = l[i+1] - h[i-1]
        if gap >= min_size:
            body = c[i] - o[i]
            if c[i] > o[i] and body >= body_min:
                return l[i+1], h[i-1]
    return np.nan, np.nan


def detect_engulfing_bearish(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                             idx: int, min_pips: float,
                             zone_high: float = np.nan, zone_low: float = np.nan) -> bool:
    """Strong bearish engulfing with optional FVG rejection check.
       If zone given: candle high must reach into FVG and close below FVG low (rejection)."""
    if idx < 1:
        return False
    cur_o, cur_c = o[idx], c[idx]
    prev_o, prev_c = o[idx-1], c[idx-1]
    if prev_c <= prev_o:
        return False
    body = cur_o - cur_c
    if body < min_pips * PIP_SIZE:
        return False
    if not (cur_o >= prev_c and cur_c < prev_o):
        return False
    if not np.isnan(zone_high):
        # Wick must have entered the FVG zone, close must reject below
        if h[idx] < zone_low:        # never reached the zone
            return False
        if cur_c > zone_low:         # didn't close below the zone (no rejection)
            return False
    return True


def detect_engulfing_bullish(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                             idx: int, min_pips: float,
                             zone_high: float = np.nan, zone_low: float = np.nan) -> bool:
    if idx < 1:
        return False
    cur_o, cur_c = o[idx], c[idx]
    prev_o, prev_c = o[idx-1], c[idx-1]
    if prev_c >= prev_o:
        return False
    body = cur_c - cur_o
    if body < min_pips * PIP_SIZE:
        return False
    if not (cur_o <= prev_c and cur_c > prev_o):
        return False
    if not np.isnan(zone_high):
        if l[idx] > zone_high:       # never reached the zone
            return False
        if cur_c < zone_high:        # didn't close above the zone
            return False
    return True


def in_zone(price: float, zone_high: float, zone_low: float) -> bool:
    if np.isnan(zone_high):
        return False
    return zone_low <= price <= zone_high


# ---------------------------------------------------------------------------
# Setup detection — receive arrays + current indices
# ---------------------------------------------------------------------------

def check_setup1(o5, h5, l5, c5, i5,
                 o15, h15, l15, c15, i15,
                 o1h, h1h, l1h, c1h, i1h):
    # ---- SELL ----
    sh1h = find_swing_high(h1h, i1h, SWING_LB)
    if not np.isnan(sh1h):
        if detect_manipulation_up(h1h, c1h, i1h, sh1h, MAN_LB):
            sl15 = find_swing_low(l15, i15, SWING_LB)
            if detect_bos_bearish(c15, i15, sl15, BOS_MAX_AGE):
                fvg_h, fvg_l = detect_fvg_bearish(o15, h15, l15, c15, i15, FVG_LB, MIN_FVG_PIPS)
                if not np.isnan(fvg_h):
                    if in_zone(c5[i5], fvg_h, fvg_l) or in_zone(h5[i5], fvg_h, fvg_l):
                        if detect_engulfing_bearish(o5, h5, l5, c5, i5, MIN_ENGULF_PIPS, fvg_h, fvg_l):
                            return "sell"
    # ---- BUY ----
    sl1h = find_swing_low(l1h, i1h, SWING_LB)
    if not np.isnan(sl1h):
        if detect_manipulation_down(l1h, c1h, i1h, sl1h, MAN_LB):
            sh15 = find_swing_high(h15, i15, SWING_LB)
            if detect_bos_bullish(c15, i15, sh15, BOS_MAX_AGE):
                fvg_h, fvg_l = detect_fvg_bullish(o15, h15, l15, c15, i15, FVG_LB, MIN_FVG_PIPS)
                if not np.isnan(fvg_h):
                    if in_zone(c5[i5], fvg_h, fvg_l) or in_zone(l5[i5], fvg_h, fvg_l):
                        if detect_engulfing_bullish(o5, h5, l5, c5, i5, MIN_ENGULF_PIPS, fvg_h, fvg_l):
                            return "buy"
    return None


def check_setup2(o5, h5, l5, c5, i5,
                 o15, h15, l15, c15, i15,
                 o1h, h1h, l1h, c1h, i1h):
    # ---- SELL ----
    f1h_h, f1h_l = detect_fvg_bearish(o1h, h1h, l1h, c1h, i1h, FVG_LB, MIN_FVG_PIPS)
    if not np.isnan(f1h_h):
        f15_h, f15_l = detect_fvg_bearish(o15, h15, l15, c15, i15, FVG_LB * 4, MIN_FVG_PIPS)
        if not np.isnan(f15_h) and f15_h <= f1h_h + 5 * PIP_SIZE:
            if in_zone(c5[i5], f1h_h, f1h_l) or in_zone(h5[i5], f1h_h, f1h_l):
                if detect_engulfing_bearish(o5, h5, l5, c5, i5, MIN_ENGULF_PIPS, f1h_h, f1h_l):
                    return "sell"
    # ---- BUY ----
    f1h_h, f1h_l = detect_fvg_bullish(o1h, h1h, l1h, c1h, i1h, FVG_LB, MIN_FVG_PIPS)
    if not np.isnan(f1h_h):
        f15_h, f15_l = detect_fvg_bullish(o15, h15, l15, c15, i15, FVG_LB * 4, MIN_FVG_PIPS)
        if not np.isnan(f15_h) and f15_l >= f1h_l - 5 * PIP_SIZE:
            if in_zone(c5[i5], f1h_h, f1h_l) or in_zone(l5[i5], f1h_h, f1h_l):
                if detect_engulfing_bullish(o5, h5, l5, c5, i5, MIN_ENGULF_PIPS, f1h_h, f1h_l):
                    return "buy"
    return None


# ---------------------------------------------------------------------------
# Lot size + trade simulation
# ---------------------------------------------------------------------------

def calc_lot_size(equity: float, sl_pips: int) -> float:
    risk_amount = equity * RISK_PCT
    pip_value   = 10.0   # USD/pip per lot for EURUSD
    lots = risk_amount / (sl_pips * pip_value)
    return round(max(0.01, lots), 2)


def simulate_trade(h5, l5, c5, t5, entry_idx: int, direction: str):
    entry_price = c5[entry_idx]
    sl_d = SL_PIPS * PIP_SIZE
    tp_d = TP_PIPS * PIP_SIZE
    if direction == "sell":
        sl_p, tp_p = entry_price + sl_d, entry_price - tp_d
    else:
        sl_p, tp_p = entry_price - sl_d, entry_price + tp_d

    end = min(entry_idx + 500, len(c5))
    for j in range(entry_idx + 1, end):
        if direction == "sell":
            if h5[j] >= sl_p:
                return {"result": "loss", "exit_time": t5[j], "entry": entry_price,
                        "exit": sl_p, "pips": -SL_PIPS, "exit_idx": j}
            if l5[j] <= tp_p:
                return {"result": "win", "exit_time": t5[j], "entry": entry_price,
                        "exit": tp_p, "pips": TP_PIPS, "exit_idx": j}
        else:
            if l5[j] <= sl_p:
                return {"result": "loss", "exit_time": t5[j], "entry": entry_price,
                        "exit": sl_p, "pips": -SL_PIPS, "exit_idx": j}
            if h5[j] >= tp_p:
                return {"result": "win", "exit_time": t5[j], "entry": entry_price,
                        "exit": tp_p, "pips": TP_PIPS, "exit_idx": j}
    return {"result": "timeout", "exit_time": t5[end-1], "entry": entry_price,
            "exit": entry_price, "pips": 0, "exit_idx": end-1}


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

def run_backtest():
    print("Loading data...")
    df5  = load_5m()
    df15 = load_htf("ohlc_15m.csv")
    df1h = load_htf("ohlc_1h.csv")

    start = max(df5["time"].iloc[0], df15["time"].iloc[0], df1h["time"].iloc[0])
    end   = min(df5["time"].iloc[-1], df15["time"].iloc[-1], df1h["time"].iloc[-1])
    df5  = df5[(df5["time"]  >= start) & (df5["time"]  <= end)].reset_index(drop=True)
    df15 = df15[(df15["time"] >= start) & (df15["time"] <= end)].reset_index(drop=True)
    df1h = df1h[(df1h["time"] >= start) & (df1h["time"] <= end)].reset_index(drop=True)

    print(f"Backtest range: {start.date()} → {end.date()}")
    print(f"5M bars: {len(df5)} | 15M bars: {len(df15)} | 1H bars: {len(df1h)}")

    # Numpy arrays
    o5, h5, l5, c5 = df5["open"].values, df5["high"].values, df5["low"].values, df5["close"].values
    o15, h15, l15, c15 = df15["open"].values, df15["high"].values, df15["low"].values, df15["close"].values
    o1h, h1h, l1h, c1h = df1h["open"].values, df1h["high"].values, df1h["low"].values, df1h["close"].values
    t5 = df5["time"].values
    t15 = df15["time"].values
    t1h = df1h["time"].values

    # Session mask on 5M
    sess_mask = in_session_mask(pd.DatetimeIndex(df5["time"]))

    print("Running backtest…")
    trades = []
    equity = INIT_BAL
    eq_curve = [equity]
    last_trade_end_idx = 0
    last_signal_idx    = -COOLDOWN_BARS

    min_5m, min_15m, min_1h = 60, 60, 30
    i15 = 0
    i1h = 0
    n5 = len(df5)

    for i in range(min_5m, n5):
        if i <= last_trade_end_idx:
            continue
        if i - last_signal_idx < COOLDOWN_BARS:
            continue
        if not sess_mask[i]:
            continue

        bar_t = t5[i]
        # Advance HTF cursors
        while i15 + 1 < len(t15) and t15[i15 + 1] <= bar_t:
            i15 += 1
        while i1h + 1 < len(t1h) and t1h[i1h + 1] <= bar_t:
            i1h += 1
        if i15 < min_15m or i1h < min_1h:
            continue

        # i in 5M is the CURRENT bar — use i-1 as last closed bar
        i5_closed = i - 1

        direction = check_setup1(o5, h5, l5, c5, i5_closed,
                                 o15, h15, l15, c15, i15,
                                 o1h, h1h, l1h, c1h, i1h)
        setup_id = "setup1"
        if direction is None:
            direction = check_setup2(o5, h5, l5, c5, i5_closed,
                                     o15, h15, l15, c15, i15,
                                     o1h, h1h, l1h, c1h, i1h)
            setup_id = "setup2"
        if direction is None:
            continue

        lots = calc_lot_size(equity, SL_PIPS)
        result = simulate_trade(h5, l5, c5, t5, i, direction)
        result["setup"]      = setup_id
        result["direction"]  = direction
        result["entry_time"] = bar_t
        result["lots"]       = lots
        pnl = result["pips"] * lots * 10.0
        equity += pnl
        result["pnl"]    = round(pnl, 2)
        result["equity"] = round(equity, 2)
        trades.append(result)
        eq_curve.append(equity)
        last_signal_idx    = i
        last_trade_end_idx = result["exit_idx"]

        if len(trades) % 20 == 0:
            print(f"  {len(trades)} trades | equity: ${equity:,.2f}")

    print(f"\nBacktest complete: {len(trades)} trades | final equity: ${equity:,.2f}")
    return pd.DataFrame(trades), eq_curve


if __name__ == "__main__":
    from report import generate_report
    trades_df, eq = run_backtest()
    generate_report(trades_df, eq)
