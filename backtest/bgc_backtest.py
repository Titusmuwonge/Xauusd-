"""
BGC Grid Backtest Engine — Python port of XAUUSD_BGC_Grid EA logic
Mirrors RegimeDetector, GridManager, RiskEngine exactly.
"""

import os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from enum import IntEnum


# ──────────────────────────────────────────────────────────────
# Enums & Data Structures
# ──────────────────────────────────────────────────────────────
class Regime(IntEnum):
    RANGING      =  0
    TRENDING_UP  =  1
    TRENDING_DOWN = -1


class OrderType(IntEnum):
    BUY_LIMIT  = 0
    SELL_LIMIT = 1
    BUY_STOP   = 2
    SELL_STOP  = 3


@dataclass
class Order:
    id:         int
    type:       OrderType
    open_price: float
    sl:         float
    tp:         float
    lot:        float
    placed_at:  pd.Timestamp


@dataclass
class Position:
    id:         int
    direction:  int          # +1 long, -1 short
    entry:      float
    sl:         float
    tp:         float
    lot:        float
    opened_at:  pd.Timestamp
    closed_at:  Optional[pd.Timestamp] = None
    exit_price: float = 0.0
    pnl:        float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestParams:
    # Regime detection
    ema_period:       int   = 20
    atr_period:       int   = 14
    cusum_threshold:  float = 2.5
    cusum_allowance:  float = 0.5
    dev_sigma:        float = 2.0
    # Grid
    lot_size:         float = 0.01
    max_levels:       int   = 4
    grid_atr_mult:    float = 0.7
    tp_atr_mult:      float = 1.0
    spread_pts:       float = 30.0   # typical XAUUSD spread in pts (0.30 USD)
    # Risk
    basket_tp_pct:    float = 1.50
    basket_sl_pct:    float = 2.00
    age_limit_hours:  int   = 4
    # Account
    initial_balance:  float = 3000.0
    contract_size:    float = 100.0  # 1 lot = 100 oz


# ──────────────────────────────────────────────────────────────
# Indicator Engine
# ──────────────────────────────────────────────────────────────
def calc_indicators(df: pd.DataFrame, p: BacktestParams) -> pd.DataFrame:
    """Add EMA, ATR, rolling std, CUSUM to the dataframe."""
    d = df.copy()

    # EMA
    d["ema"] = d["close"].ewm(span=p.ema_period, adjust=False).mean()

    # ATR (Wilder)
    hl = d["high"] - d["low"]
    hc = (d["high"] - d["close"].shift(1)).abs()
    lc = (d["low"]  - d["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1/p.atr_period, adjust=False).mean()

    # Rolling std (same window as EMA)
    d["std"] = d["close"].rolling(p.ema_period).std(ddof=0).clip(lower=1e-10)

    # Standardised innovation z = (price - EMA) / std
    d["z"] = (d["close"] - d["ema"]) / d["std"]

    # Page's CUSUM (vectorised is tricky — must loop because it's path-dependent)
    cusum_pos = np.zeros(len(d))
    cusum_neg = np.zeros(len(d))
    k = p.cusum_allowance
    z_vals = d["z"].values
    for i in range(1, len(d)):
        cusum_pos[i] = max(0.0, cusum_pos[i-1] + z_vals[i] - k)
        cusum_neg[i] = max(0.0, cusum_neg[i-1] - z_vals[i] - k)

    d["cusum_pos"] = cusum_pos
    d["cusum_neg"] = cusum_neg

    # Regime classification with hysteresis:
    # Only return to RANGING when both CUSUM values drop below 1.0.
    h = p.cusum_threshold
    regime_arr = np.where(cusum_pos >= h, int(Regime.TRENDING_UP),
                 np.where(cusum_neg >= h, int(Regime.TRENDING_DOWN), int(Regime.RANGING)))
    for i in range(1, len(regime_arr)):
        if regime_arr[i] == int(Regime.RANGING) and regime_arr[i-1] != int(Regime.RANGING):
            if cusum_pos[i] >= 1.0 or cusum_neg[i] >= 1.0:
                regime_arr[i] = regime_arr[i-1]  # hold previous trend during transition
    d["regime"] = regime_arr

    # Price extension flag
    d["ext_dir"] = np.where(d["z"] >=  p.dev_sigma,  1,
                   np.where(d["z"] <= -p.dev_sigma, -1, 0))

    return d


# ──────────────────────────────────────────────────────────────
# Backtest Simulator
# ──────────────────────────────────────────────────────────────
class BGCBacktest:
    def __init__(self, p: BacktestParams):
        self.p           = p
        self.balance     = p.initial_balance
        self.equity      = p.initial_balance
        self.orders:     List[Order]    = []
        self.positions:  List[Position] = []
        self.closed:     List[Position] = []
        self._id         = 0
        self.cycle_start: Optional[pd.Timestamp] = None
        self.prev_regime = Regime.RANGING
        self.spread      = p.spread_pts * 0.01  # convert pts to USD (XAUUSD 1pt=0.01)
        self.equity_curve: List[Tuple[pd.Timestamp, float]] = []

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    # ── P&L ────────────────────────────────────────────────────
    def _pip_value(self, lots: float) -> float:
        """USD value of a 1-point ($0.01) move for given lots."""
        return lots * self.p.contract_size * 0.01

    def _position_pnl(self, pos: Position, price: float) -> float:
        move = (price - pos.entry) * pos.direction
        return move * pos.lot * self.p.contract_size

    def _total_float_pnl(self, mid: float) -> float:
        return sum(self._position_pnl(p, mid) for p in self.positions)

    # ── Order placement ─────────────────────────────────────────
    def _order_exists_at(self, price: float, spacing: float) -> bool:
        tol = spacing * 0.3
        for o in self.orders:
            if abs(o.open_price - price) < tol:
                return True
        for p in self.positions:
            if abs(p.entry - price) < tol:
                return True
        return False

    def _place(self, otype: OrderType, price: float, sl: float, tp: float,
               spacing: float, ts: pd.Timestamp):
        if self._order_exists_at(price, spacing):
            return
        o = Order(self._next_id(), otype, price, sl, tp, self.p.lot_size, ts)
        self.orders.append(o)

    # ── Grid builders ───────────────────────────────────────────
    def _mgt_grid(self, mid: float, atr: float, ext_dir: int, ts: pd.Timestamp):
        spacing = atr * self.p.grid_atr_mult
        tp_dist = atr * self.p.tp_atr_mult if self.p.tp_atr_mult > 0 else 0

        n = min(self.p.max_levels, 4)
        for i in range(1, n + 1):
            if ext_dir > 0:  # price above EMA — sell limits for mean reversion
                sp = mid + i * spacing
                sl = sp + atr * 1.0  # 1:1 RR (was 2.0)
                tp = sp - tp_dist if tp_dist else 0.0
                self._place(OrderType.SELL_LIMIT, sp, sl, tp, spacing, ts)
            else:  # price below EMA — buy limits for mean reversion
                bp = mid - i * spacing
                sl = bp - atr * 1.0  # 1:1 RR (was 2.0)
                tp = bp + tp_dist if tp_dist else 0.0
                self._place(OrderType.BUY_LIMIT, bp, sl, tp, spacing, ts)

    def _tgt_grid(self, mid: float, atr: float, direction: int, ts: pd.Timestamp):
        spacing = atr * self.p.grid_atr_mult
        tp_dist = atr * 1.5  # fixed 1:1 RR — 1.5×ATR TP matches 1.5×ATR SL

        n = min(self.p.max_levels, 3)
        for i in range(1, n + 1):
            if direction > 0:
                price = mid + i * spacing
                sl    = price - atr * 1.5
                tp    = price + tp_dist
                self._place(OrderType.BUY_STOP, price, sl, tp, spacing, ts)
            else:
                price = mid - i * spacing
                sl    = price + atr * 1.5
                tp    = price - tp_dist
                self._place(OrderType.SELL_STOP, price, sl, tp, spacing, ts)

    # ── Order → Position fill check ─────────────────────────────
    def _check_fills(self, row: pd.Series, ts: pd.Timestamp):
        filled = []
        for o in self.orders:
            hi, lo = row["high"], row["low"]
            ask = row["close"] + self.spread
            bid = row["close"]

            triggered = False
            fill_price = o.open_price

            if o.type == OrderType.BUY_LIMIT  and lo <= o.open_price:
                triggered = True
                fill_price = min(ask, o.open_price)
            elif o.type == OrderType.SELL_LIMIT and hi >= o.open_price:
                triggered = True
                fill_price = max(bid, o.open_price)
            elif o.type == OrderType.BUY_STOP  and hi >= o.open_price:
                triggered = True
                fill_price = max(ask, o.open_price)
            elif o.type == OrderType.SELL_STOP  and lo <= o.open_price:
                triggered = True
                fill_price = min(bid, o.open_price)

            if triggered:
                direction = 1 if o.type in (OrderType.BUY_LIMIT, OrderType.BUY_STOP) else -1
                pos = Position(
                    id=o.id, direction=direction,
                    entry=fill_price, sl=o.sl, tp=o.tp,
                    lot=o.lot, opened_at=ts
                )
                self.positions.append(pos)
                filled.append(o)
                if self.cycle_start is None:
                    self.cycle_start = ts

        for o in filled:
            self.orders.remove(o)

    # ── SL / TP checks ──────────────────────────────────────────
    def _check_exits(self, row: pd.Series, ts: pd.Timestamp):
        hi, lo = row["high"], row["low"]
        mid = row["close"]
        exited = []

        for pos in self.positions:
            if pos.direction > 0:  # long
                if lo <= pos.sl:
                    pos.exit_price = pos.sl
                    pos.exit_reason = "SL"
                    pos.pnl = self._position_pnl(pos, pos.sl)
                    exited.append(pos)
                elif pos.tp > 0 and hi >= pos.tp:
                    pos.exit_price = pos.tp
                    pos.exit_reason = "TP"
                    pos.pnl = self._position_pnl(pos, pos.tp)
                    exited.append(pos)
            else:  # short
                if hi >= pos.sl:
                    pos.exit_price = pos.sl
                    pos.exit_reason = "SL"
                    pos.pnl = self._position_pnl(pos, pos.sl)
                    exited.append(pos)
                elif pos.tp > 0 and lo <= pos.tp:
                    pos.exit_price = pos.tp
                    pos.exit_reason = "TP"
                    pos.pnl = self._position_pnl(pos, pos.tp)
                    exited.append(pos)

        for pos in exited:
            pos.closed_at = ts
            self.balance += pos.pnl
            self.positions.remove(pos)
            self.closed.append(pos)

    # ── Liquidate all (basket TP/SL/age) ────────────────────────
    def _liquidate(self, row: pd.Series, ts: pd.Timestamp, reason: str):
        bid = row["close"]
        ask = row["close"] + self.spread
        self.orders.clear()
        for pos in list(self.positions):
            exit_price = bid if pos.direction > 0 else ask  # long exits at bid, short at ask
            pos.exit_price  = exit_price
            pos.exit_reason = reason
            pos.closed_at   = ts
            pos.pnl = self._position_pnl(pos, exit_price)
            self.balance += pos.pnl
            self.closed.append(pos)
        self.positions.clear()
        self.cycle_start = None

    # ── Risk guards (every bar) ──────────────────────────────────
    def _check_risk(self, row: pd.Series, ts: pd.Timestamp) -> bool:
        if not self.positions and not self.orders:
            return False

        mid       = row["close"]
        float_pnl = self._total_float_pnl(mid)
        equity    = self.balance + float_pnl  # use equity not balance for thresholds
        basket_tp = equity * (self.p.basket_tp_pct / 100.0)
        basket_sl = -equity * (self.p.basket_sl_pct / 100.0)

        if self.positions and float_pnl >= basket_tp:
            self._liquidate(row, ts, "BasketTP")
            return True

        if self.positions and float_pnl <= basket_sl:
            self._liquidate(row, ts, "HardSL")
            return True

        # Age limit — fires on positions OR pending orders
        if self.cycle_start is not None:
            elapsed = (ts - self.cycle_start).total_seconds()
            if elapsed >= self.p.age_limit_hours * 3600:
                if self.positions or self.orders:
                    self._liquidate(row, ts, "AgeLimit")
                    self.orders.clear()
                    self.cycle_start = None
                    return True

        return False

    # ── Main loop ───────────────────────────────────────────────
    def run(self, df: pd.DataFrame) -> "BacktestResult":
        df = calc_indicators(df, self.p)

        for ts, row in df.iterrows():
            if pd.isna(row["atr"]) or pd.isna(row["ema"]):
                continue

            mid  = row["close"]
            atr  = row["atr"]
            ema  = row["ema"]
            reg  = Regime(row["regime"])

            self._check_fills(row, ts)
            self._check_exits(row, ts)

            # Risk checks every bar (pass row so liquidation uses correct bid/ask)
            if self._check_risk(row, ts):
                self.prev_regime = reg
                self.equity_curve.append((ts, self.balance))
                continue

            # Regime transition — cancel all stale orders
            if reg != self.prev_regime:
                self.orders.clear()
                if reg == Regime.RANGING and not self.positions:
                    self.cycle_start = None
                self.prev_regime = reg

            # Grid management — cycle_start is set only on first FILL (in _check_fills)
            if reg == Regime.RANGING:
                ext = row["ext_dir"]
                if ext != 0:
                    self._mgt_grid(mid, atr, ext, ts)  # directional bias via ext_dir
            else:
                direction = 1 if reg == Regime.TRENDING_UP else -1
                self.orders.clear()
                self._tgt_grid(mid, atr, direction, ts)

            # Natural cycle close
            if not self.positions and not self.orders:
                self.cycle_start = None

            self.equity = self.balance + self._total_float_pnl(mid)
            self.equity_curve.append((ts, self.equity))

        return BacktestResult(self.closed, self.equity_curve, self.p)


# ──────────────────────────────────────────────────────────────
# Results & Metrics
# ──────────────────────────────────────────────────────────────
class BacktestResult:
    def __init__(self, closed: List[Position], equity_curve, p: BacktestParams):
        self.closed       = closed
        self.equity_curve = equity_curve
        self.p            = p
        self._df_trades   = self._build_trades_df()
        self._eq          = self._build_equity_series()

    def _build_trades_df(self) -> pd.DataFrame:
        if not self.closed:
            return pd.DataFrame()
        rows = [{
            "opened_at":   t.opened_at,
            "closed_at":   t.closed_at,
            "direction":   "BUY" if t.direction > 0 else "SELL",
            "entry":       t.entry,
            "exit":        t.exit_price,
            "sl":          t.sl,
            "tp":          t.tp,
            "lot":         t.lot,
            "pnl":         t.pnl,
            "exit_reason": t.exit_reason,
        } for t in self.closed]
        return pd.DataFrame(rows)

    def _build_equity_series(self) -> pd.Series:
        if not self.equity_curve:
            return pd.Series(dtype=float)
        ts, vals = zip(*self.equity_curve)
        return pd.Series(vals, index=ts)

    def metrics(self) -> dict:
        df  = self._df_trades
        eq  = self._eq
        ini = self.p.initial_balance

        if df.empty:
            return {"error": "No trades"}

        total_trades  = len(df)
        wins          = df[df["pnl"] > 0]
        losses        = df[df["pnl"] <= 0]
        win_rate      = len(wins) / total_trades * 100
        net_pnl       = df["pnl"].sum()
        gross_profit  = wins["pnl"].sum()
        gross_loss    = abs(losses["pnl"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf
        avg_win       = wins["pnl"].mean()   if len(wins)   > 0 else 0
        avg_loss      = losses["pnl"].mean() if len(losses) > 0 else 0
        expectancy    = (win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss

        # Drawdown
        roll_max      = eq.cummax()
        dd            = eq - roll_max
        max_dd        = dd.min()
        max_dd_pct    = (max_dd / roll_max[dd.idxmin()]) * 100 if not eq.empty else 0

        # Recovery factor
        recovery      = net_pnl / abs(max_dd) if max_dd != 0 else np.inf

        # Sharpe (annualised, assuming M15 = 96 bars/day, 252 trading days)
        returns       = eq.pct_change().dropna()
        bars_per_year = 96 * 252
        sharpe        = (returns.mean() / returns.std() * np.sqrt(bars_per_year)
                         if returns.std() > 0 else 0)

        # Sortino
        neg_ret       = returns[returns < 0]
        sortino       = (returns.mean() / neg_ret.std() * np.sqrt(bars_per_year)
                         if len(neg_ret) > 0 and neg_ret.std() > 0 else 0)

        # Calmar
        calmar        = (net_pnl / abs(max_dd) if max_dd != 0 else np.inf)

        # Consecutive losses
        consec        = 0
        max_consec    = 0
        for pnl in df["pnl"]:
            if pnl < 0:
                consec += 1
                max_consec = max(max_consec, consec)
            else:
                consec = 0

        # Exit breakdown
        exit_counts = df["exit_reason"].value_counts().to_dict()

        return {
            "Total Trades":       total_trades,
            "Win Rate %":         round(win_rate, 1),
            "Net P&L":            round(net_pnl, 2),
            "Net P&L %":          round(net_pnl / ini * 100, 2),
            "Profit Factor":      round(profit_factor, 3),
            "Avg Win":            round(avg_win, 2),
            "Avg Loss":           round(avg_loss, 2),
            "Expectancy":         round(expectancy, 2),
            "Max Drawdown $":     round(max_dd, 2),
            "Max Drawdown %":     round(max_dd_pct, 2),
            "Recovery Factor":    round(recovery, 2),
            "Sharpe Ratio":       round(sharpe, 3),
            "Sortino Ratio":      round(sortino, 3),
            "Calmar Ratio":       round(calmar, 3),
            "Max Consec Losses":  max_consec,
            "Exit Breakdown":     exit_counts,
        }

    def print_metrics(self):
        m = self.metrics()
        print("\n" + "═" * 50)
        print("  BGC GRID BACKTEST RESULTS")
        print("═" * 50)
        for k, v in m.items():
            if k == "Exit Breakdown":
                print(f"  {k}:")
                for reason, cnt in v.items():
                    print(f"      {reason}: {cnt}")
            else:
                print(f"  {k:<25} {v}")
        print("═" * 50)

    def trades_df(self) -> pd.DataFrame:
        return self._df_trades

    def equity_series(self) -> pd.Series:
        return self._eq
