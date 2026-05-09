"""
Synthetic XAUUSD M15 data generator.
Uses a regime-switching GBM with jump diffusion and volatility clustering —
calibrated to realistic gold parameters (2022-2025 era).
On your own machine replace this with real data from yfinance/dukascopy.
"""

import numpy as np
import pandas as pd
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def generate_xauusd_m15(
    n_bars:        int   = 26_000,   # ~270 trading days of M15
    start_price:   float = 1900.0,
    seed:          int   = 42,
) -> pd.DataFrame:
    """
    Regime-switching GBM + jump diffusion calibrated to XAUUSD.
    Regimes:
      0 = ranging  (low vol, mean-reverting drift)
      1 = trending  (moderate drift, moderate vol)
      2 = volatile  (high vol, jump risk — news/FOMC)
    """
    rng = np.random.default_rng(seed)

    # ── Regime parameters (per M15 bar)
    # drift = annualised / (96 bars/day * 252 days)
    BARS_PA = 96 * 252
    regime_params = {
        0: dict(mu= 0.00/BARS_PA, sigma=0.0045, jump_prob=0.001, jump_size=0.004),
        1: dict(mu= 0.12/BARS_PA, sigma=0.0080, jump_prob=0.002, jump_size=0.008),
        2: dict(mu= 0.00/BARS_PA, sigma=0.0180, jump_prob=0.010, jump_size=0.020),
    }
    # Regime transition matrix (from row → to col)
    trans = np.array([
        [0.980, 0.015, 0.005],  # from ranging
        [0.020, 0.970, 0.010],  # from trending
        [0.050, 0.100, 0.850],  # from volatile
    ])

    prices = np.empty(n_bars + 1)
    prices[0] = start_price
    regime    = 0
    regimes   = np.empty(n_bars, dtype=int)

    for i in range(n_bars):
        p   = regime_params[regime]
        mu  = p["mu"]
        sig = p["sigma"]
        jp  = p["jump_prob"]
        js  = p["jump_size"]

        z      = rng.standard_normal()
        jump   = rng.choice([0, 1], p=[1 - jp, jp])
        j_dir  = rng.choice([-1, 1])
        j_mag  = rng.exponential(js) * j_dir * jump

        r = mu + sig * z + j_mag
        prices[i + 1] = prices[i] * np.exp(r)

        regimes[i] = regime
        regime = rng.choice(3, p=trans[regime])

    # Build M15 OHLCV bars from the close series
    c = prices[1:]      # close
    o = prices[:-1]     # open = prev close

    # Realistic high/low around open-close using ATR-derived noise
    hl_noise = np.abs(rng.standard_normal(n_bars)) * 0.003
    high = np.maximum(o, c) * (1 + hl_noise)
    low  = np.minimum(o, c) * (1 - hl_noise)

    # Volume (correlated with volatility regime)
    base_vol = np.where(regimes == 0, 800, np.where(regimes == 1, 1500, 3000))
    volume   = base_vol * (0.5 + rng.exponential(0.5, n_bars))

    # M15 timestamps (Mon–Fri, skip weekends)
    start_ts = pd.Timestamp("2022-01-03 00:00:00")
    timestamps = []
    ts = start_ts
    while len(timestamps) < n_bars:
        if ts.weekday() < 5:  # Mon–Fri
            timestamps.append(ts)
        ts += pd.Timedelta(minutes=15)

    df = pd.DataFrame({
        "open":   np.round(o,   2),
        "high":   np.round(high, 2),
        "low":    np.round(low,  2),
        "close":  np.round(c,   2),
        "volume": np.round(volume, 0),
    }, index=pd.DatetimeIndex(timestamps[:n_bars]))

    out = os.path.join(DATA_DIR, "XAUUSD_M15.csv")
    df.to_csv(out)
    print(f"Generated {len(df)} synthetic M15 bars → {out}")
    print(f"  Price range: ${df['close'].min():.2f} – ${df['close'].max():.2f}")
    print(f"  Date range:  {df.index[0].date()} → {df.index[-1].date()}")
    return df


if __name__ == "__main__":
    df = generate_xauusd_m15()
    print(df.tail())
