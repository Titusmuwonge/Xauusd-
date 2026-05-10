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
    # sigma_annual / sqrt(96 bars/day * 252 days) = sigma_per_bar
    # Gold: ranging≈12%, trending≈18%, volatile≈35% annual vol
    BARS_PA = 96 * 252
    regime_params = {
        # Ranging uses Ornstein-Uhlenbeck mean reversion (kappa = pull strength per bar).
        # kappa=0.20 → half-life ≈ 3.5 bars (~53 min); price oscillates around anchor
        # tightly enough that CUSUM z-score accumulation stays below threshold.
        0: dict(mode="ou",  kappa=0.20, sigma=0.00077, jump_prob=0.001, jump_size=0.004),
        1: dict(mode="gbm", mu= 0.15/BARS_PA, sigma=0.00116, jump_prob=0.002, jump_size=0.008),
        2: dict(mode="gbm", mu= 0.00/BARS_PA, sigma=0.00225, jump_prob=0.010, jump_size=0.015),
    }
    # Transition matrix (row → col): ranging≈55%, trending≈38%, volatile≈7% steady-state
    trans = np.array([
        [0.970, 0.025, 0.005],  # from ranging
        [0.040, 0.950, 0.010],  # from trending
        [0.100, 0.150, 0.750],  # from volatile
    ])

    prices   = np.empty(n_bars + 1)
    prices[0] = start_price
    regime    = 0
    regimes   = np.empty(n_bars, dtype=int)
    # OU anchor: slowly-drifting "fair value" that price reverts to in ranging
    log_anchor = np.log(start_price)

    for i in range(n_bars):
        p  = regime_params[regime]
        jp = p["jump_prob"]
        js = p["jump_size"]

        z     = rng.standard_normal()
        jump  = rng.choice([0, 1], p=[1 - jp, jp])
        j_dir = rng.choice([-1, 1])
        j_mag = rng.exponential(js) * j_dir * jump

        if p["mode"] == "ou":
            # OU: r = kappa*(log_anchor - log_price) + sigma*z
            # This pulls log-price back to anchor each bar.
            log_pull = p["kappa"] * (log_anchor - np.log(prices[i]))
            r = log_pull + p["sigma"] * z + j_mag
            # Anchor itself drifts very slowly (real gold has a slight long-run bias)
            log_anchor += rng.standard_normal() * 0.00010
        else:
            r = p["mu"] + p["sigma"] * z + j_mag
            # In trending/volatile, anchor follows price so it doesn't snap back hard
            log_anchor = 0.95 * log_anchor + 0.05 * np.log(prices[i])

        prices[i + 1] = prices[i] * np.exp(r)
        regimes[i]    = regime
        regime         = rng.choice(3, p=trans[regime])

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
