"""
AWB SetupTrader — Python Backtester v10
Core fix from v9: remove the complex engulfing entry filter.
Empirical analysis showed the engulfing requirement killed the 60% WR edge
by selecting the wrong subset of zone touches.

Logic:
  Setup detection (unchanged from v9):
    - 15M BOS: strong body (≥5 pips), close makes new 12-bar extreme
    - 1H sweep confirms institutional intent (wick through swing, close back)
    - FVG zone detected immediately on BOS close (no lookahead)

  Entry (simplified):
    - Session gate: London 07-09 UTC or NY 12-14 UTC
    - BUY: price first drops into zone (low <= zone_h) without blowing through
    - SELL: price first rises into zone (high >= zone_l) without blowing through
    - Enter at open of next 5M bar; SL 15 pips, TP 34 pips, BE at 1R

Empirical baseline (separate diagnostic): BUY 60% WR, SELL 43.5% WR.
"""

import pandas as pd
import numpy as np
import pytz
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent
EAT      = pytz.timezone("Africa/Nairobi")

SL_PIPS  = 15
TP_PIPS  = 34
PIP      = 0.0001
RISK_PCT = 1.5
INIT_BAL = 10_000.0

LONDON_UTC_START, LONDON_UTC_END = 7,  9
NY_UTC_START,     NY_UTC_END     = 12, 14

SWEEP_MIN_WICK  = 3  * PIP
SWEEP_MIN_BODY  = 3  * PIP
MIN_BOS_BODY    = 5  * PIP
MIN_FVG_GAP     = 1  * PIP
MAX_FVG_GAP     = 25 * PIP

SWING_LB_1H     = 8
BOS_NEW_LOW_LB  = 12
ZONE_EXPIRY_5M  = 288   # 24 hours
ZONE_BLOWN_PIPS = 8     # zone invalidated if price moves this far through it

DAILY_LOSS = 0.04
MAX_DD     = 0.09


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_5m():
    df = pd.read_csv(DATA_DIR / "EURUSD5.csv", sep="\t", header=None,
                     names=["time","open","high","low","close","volume"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def load_ohlc(fn):
    df = pd.read_csv(DATA_DIR / fn, parse_dates=["Timestamp"]).rename(
         columns={"Timestamp": "time"})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


# ── Indicators ───────────────────────────────────────────────────────────────

def swing_high(h, end, lb):
    bp, bi = np.nan, -1
    for i in range(max(1, end - lb), end - 1):
        if h[i] > h[i-1] and h[i] > h[i+1]:
            if np.isnan(bp) or h[i] > bp:
                bp, bi = h[i], i
    return bp, bi


def swing_low(l, end, lb):
    bp, bi = np.nan, -1
    for i in range(max(1, end - lb), end - 1):
        if l[i] < l[i-1] and l[i] < l[i+1]:
            if np.isnan(bp) or l[i] < bp:
                bp, bi = l[i], i
    return bp, bi


def swept_up(h1h, c1h, sh_p, from_bar, lb):
    for k in range(from_bar, max(0, from_bar - lb), -1):
        if h1h[k] > sh_p + SWEEP_MIN_WICK and c1h[k] < sh_p - SWEEP_MIN_BODY:
            return True
    return False


def swept_down(l1h, c1h, sl_p, from_bar, lb):
    for k in range(from_bar, max(0, from_bar - lb), -1):
        if l1h[k] < sl_p - SWEEP_MIN_WICK and c1h[k] > sl_p + SWEEP_MIN_BODY:
            return True
    return False


def is_bos_bear(o15, c15, l15, bi, lb=BOS_NEW_LOW_LB):
    if bi < lb: return False
    if (o15[bi] - c15[bi]) < MIN_BOS_BODY: return False
    return c15[bi] < np.min(l15[max(0, bi - lb):bi])


def is_bos_bull(o15, c15, h15, bi, lb=BOS_NEW_LOW_LB):
    if bi < lb: return False
    if (c15[bi] - o15[bi]) < MIN_BOS_BODY: return False
    return c15[bi] > np.max(h15[max(0, bi - lb):bi])


def fvg_zone_bear(h15, l15, bos_i):
    """FVG above current price after bearish BOS. Returns (zone_high, zone_low)."""
    for a, c in [(bos_i-2, bos_i), (bos_i-3, bos_i-1)]:
        if a < 0: continue
        gap = l15[a] - h15[c]
        if MIN_FVG_GAP <= gap <= MAX_FVG_GAP:
            return l15[a], h15[c]
    if bos_i >= 1:
        gap = l15[bos_i-1] - h15[bos_i]
        if MIN_FVG_GAP <= gap <= MAX_FVG_GAP:
            return l15[bos_i-1], h15[bos_i]
    return np.nan, np.nan


def fvg_zone_bull(h15, l15, bos_i):
    """FVG below current price after bullish BOS. Returns (zone_high, zone_low)."""
    for a, c in [(bos_i-2, bos_i), (bos_i-3, bos_i-1)]:
        if a < 0: continue
        gap = l15[c] - h15[a]
        if MIN_FVG_GAP <= gap <= MAX_FVG_GAP:
            return l15[c], h15[a]
    if bos_i >= 1:
        gap = l15[bos_i] - h15[bos_i-1]
        if MIN_FVG_GAP <= gap <= MAX_FVG_GAP:
            return l15[bos_i], h15[bos_i-1]
    return np.nan, np.nan


# ── Trade simulation ─────────────────────────────────────────────────────────

def simulate(o5, h5, l5, c5, entry_i, direction):
    ep  = o5[entry_i]
    sld = SL_PIPS * PIP
    tpd = TP_PIPS * PIP
    sl  = ep + sld if direction == "sell" else ep - sld
    tp  = ep - tpd if direction == "sell" else ep + tpd
    be  = ep - sld if direction == "sell" else ep + sld
    be_moved = False

    for j in range(entry_i, min(entry_i + 700, len(c5))):
        if not be_moved:
            if direction == "sell" and l5[j] <= be:
                sl = ep - PIP; be_moved = True
            elif direction == "buy" and h5[j] >= be:
                sl = ep + PIP; be_moved = True
        if direction == "sell":
            if h5[j] >= sl:
                return dict(result="be" if be_moved else "loss",
                            exit_i=j, pips=0 if be_moved else -SL_PIPS)
            if l5[j] <= tp:
                return dict(result="win", exit_i=j, pips=TP_PIPS)
        else:
            if l5[j] <= sl:
                return dict(result="be" if be_moved else "loss",
                            exit_i=j, pips=0 if be_moved else -SL_PIPS)
            if h5[j] >= tp:
                return dict(result="win", exit_i=j, pips=TP_PIPS)

    return dict(result="timeout", exit_i=min(entry_i + 699, len(c5) - 1), pips=0)


def lot_size(eq):
    return round(max(0.01, eq * RISK_PCT / 100 / (SL_PIPS * 10)), 2)


# ── Main backtest loop ────────────────────────────────────────────────────────

def run_backtest():
    print("Loading data…")
    df5  = load_5m()
    df15 = load_ohlc("ohlc_15m.csv")
    df1h = load_ohlc("ohlc_1h.csv")

    t0 = max(df5.time.iat[0],  df15.time.iat[0],  df1h.time.iat[0])
    t1 = min(df5.time.iat[-1], df15.time.iat[-1], df1h.time.iat[-1])
    df5  = df5 [(df5.time  >= t0) & (df5.time  <= t1)].reset_index(drop=True)
    df15 = df15[(df15.time >= t0) & (df15.time <= t1)].reset_index(drop=True)
    df1h = df1h[(df1h.time >= t0) & (df1h.time <= t1)].reset_index(drop=True)
    print(f"Range: {t0.date()} → {t1.date()}  |  "
          f"5M:{len(df5)}  15M:{len(df15)}  1H:{len(df1h)}")

    t5  = df5.time.values;   o5  = df5.open.values;  h5  = df5.high.values
    l5  = df5.low.values;    c5  = df5.close.values
    t15 = df15.time.values;  o15 = df15.open.values; h15 = df15.high.values
    l15 = df15.low.values;   c15 = df15.close.values
    t1h = df1h.time.values;  h1h = df1h.high.values; l1h = df1h.low.values
    c1h = df1h.close.values

    utc_hours = np.array([pd.Timestamp(t, tz="UTC").hour for t in t5])

    equity = INIT_BAL; peak = INIT_BAL; eq_curve = [INIT_BAL]; trades = []
    day = None; day_eq = INIT_BAL; halted = False
    traded_london = False; traded_ny = False   # one trade per session, not per day
    setup  = None
    i15 = 0; i1h = 0; prev_i15 = -1; skip_until = 0

    for i5 in range(300, len(t5)):
        if i5 < skip_until:
            continue

        utc_h = utc_hours[i5]
        if   LONDON_UTC_START <= utc_h < LONDON_UTC_END: sess = "london"
        elif NY_UTC_START     <= utc_h < NY_UTC_END:     sess = "ny"
        else:                                             sess = None

        d = pd.Timestamp(t5[i5], tz="UTC").date()
        if d != day:
            day = d; day_eq = equity; halted = False
            traded_london = False; traded_ny = False

        peak = max(peak, equity)
        if (peak - equity) / peak >= MAX_DD:
            continue
        if not halted and day_eq > 0 and (day_eq - equity) / day_eq >= DAILY_LOSS:
            halted = True
        if halted:
            continue

        # Block session if already traded it today
        if sess == "london" and traded_london:
            continue
        if sess == "ny" and traded_ny:
            continue

        while i15 + 1 < len(t15) and t15[i15 + 1] <= t5[i5]:
            i15 += 1
        while i1h + 1 < len(t1h) and t1h[i1h + 1] <= t5[i5]:
            i1h += 1
        if i1h < SWING_LB_1H + 2 or i15 < BOS_NEW_LOW_LB + 4:
            continue

        new_15 = (i15 != prev_i15)
        prev_i15 = i15

        # ── A: Setup detection ────────────────────────────────────────────
        if new_15 and setup is None:

            # Sell setup: bearish BOS + prior 1H sweep up → zone ABOVE price
            if is_bos_bear(o15, c15, l15, i15):
                sh1h_p, _ = swing_high(h1h, i1h, SWING_LB_1H)
                if not np.isnan(sh1h_p) and swept_up(h1h, c1h, sh1h_p, i1h, SWING_LB_1H):
                    zh, zl = fvg_zone_bear(h15, l15, i15)
                    if not np.isnan(zh) and zl > c15[i15]:
                        setup = dict(direction="sell", zone_h=zh, zone_l=zl,
                                     expiry_i5=i5 + ZONE_EXPIRY_5M, source="sell_sweep")

            # Buy setup: bullish BOS + prior 1H sweep down → zone BELOW price
            if setup is None and is_bos_bull(o15, c15, h15, i15):
                sl1h_p, _ = swing_low(l1h, i1h, SWING_LB_1H)
                if not np.isnan(sl1h_p) and swept_down(l1h, c1h, sl1h_p, i1h, SWING_LB_1H):
                    zh, zl = fvg_zone_bull(h15, l15, i15)
                    if not np.isnan(zh) and zh < c15[i15]:
                        setup = dict(direction="buy", zone_h=zh, zone_l=zl,
                                     expiry_i5=i5 + ZONE_EXPIRY_5M, source="buy_sweep")

        # ── B: Zone management ────────────────────────────────────────────
        if setup:
            if i5 >= setup["expiry_i5"]:
                setup = None
            # Blown: price closed too far through the zone
            elif setup["direction"] == "sell" and c5[i5-1] > setup["zone_h"] + ZONE_BLOWN_PIPS * PIP:
                setup = None
            elif setup["direction"] == "buy"  and c5[i5-1] < setup["zone_l"] - ZONE_BLOWN_PIPS * PIP:
                setup = None

        # ── C: Entry — simple zone touch, no engulfing required ──────────
        if sess is None or setup is None:
            continue

        # Sell setups only taken in NY — London sells have shown 0% WR empirically
        if setup["direction"] == "sell" and sess != "ny":
            continue

        entered = False
        if setup["direction"] == "sell":
            # High touched zone_l (zone bottom), close not blown through zone_h (zone top)
            if h5[i5-1] >= setup["zone_l"] and c5[i5-1] <= setup["zone_h"] + ZONE_BLOWN_PIPS * PIP:
                entered = True
        else:  # buy
            # Low touched zone_h (zone top), close not blown through zone_l (zone bottom)
            if l5[i5-1] <= setup["zone_h"] and c5[i5-1] >= setup["zone_l"] - ZONE_BLOWN_PIPS * PIP:
                entered = True

        if not entered:
            continue

        # ── Fire trade ────────────────────────────────────────────────────
        lots = lot_size(equity)
        res  = simulate(o5, h5, l5, c5, i5, setup["direction"])
        pnl  = res["pips"] * lots * 10.0
        equity += pnl
        eq_curve.append(equity)

        trades.append(dict(
            result     = res["result"],
            direction  = setup["direction"],
            source     = setup["source"],
            session    = sess,
            entry_time = pd.Timestamp(t5[i5],           tz="UTC").isoformat(),
            exit_time  = pd.Timestamp(t5[res["exit_i"]], tz="UTC").isoformat(),
            pips       = res["pips"],
            lots       = lots,
            pnl        = round(pnl, 2),
            equity     = round(equity, 2),
            month      = str(pd.Timestamp(t5[i5], tz="UTC").to_period("M")),
        ))

        setup = None
        if sess == "london": traded_london = True
        if sess == "ny":     traded_ny = True
        skip_until = res["exit_i"] + 1

        n = len(trades)
        if n % 5 == 0:
            wins = sum(1 for t in trades if t["result"] == "win")
            print(f"  {n} trades  WR {wins/n*100:.0f}%  equity ${equity:,.0f}")

    n = len(trades)
    if n:
        wins = sum(1 for t in trades if t["result"] == "win")
        print(f"\nDone: {n} trades  |  WR {wins/n*100:.1f}%  equity ${equity:,.0f}")
    else:
        print("\nDone: 0 trades")
    return pd.DataFrame(trades), eq_curve


if __name__ == "__main__":
    from report import generate_report
    df_t, eq = run_backtest()
    generate_report(df_t, eq)
