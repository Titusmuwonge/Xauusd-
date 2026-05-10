"""
MT5 Historical Data Exporter — run this on the Windows PC where MT5 is installed.

Usage:
    python backtest/export_from_mt5.py

Exports XAUUSD OHLCV bars for M1, M5, M15, H1 going back to 2004 and saves
them as CSVs into backtest/data/. These files are then used by run_backtest.py.

Requirements (Windows only):
    pip install MetaTrader5 pandas
"""

import os
import sys
from datetime import datetime

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    print("  Run: pip install MetaTrader5")
    print("  This script must run on the Windows machine where MT5 is installed.")
    sys.exit(1)

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

SYMBOL = "XAUUSD"
START  = datetime(2004, 1, 1)

TIMEFRAMES = {
    "M1":  (mt5.TIMEFRAME_M1,  "XAUUSD_M1.csv"),
    "M5":  (mt5.TIMEFRAME_M5,  "XAUUSD_M5.csv"),
    "M15": (mt5.TIMEFRAME_M15, "XAUUSD_M15.csv"),
    "H1":  (mt5.TIMEFRAME_H1,  "XAUUSD_H1.csv"),
}


def export_tf(tf_name: str, tf_const: int, filename: str) -> bool:
    print(f"\n[{tf_name}] Requesting bars from {START.date()} …", flush=True)

    rates = mt5.copy_rates_from(SYMBOL, tf_const, START, 10_000_000)
    if rates is None or len(rates) == 0:
        err = mt5.last_error()
        print(f"  ERROR: no data returned — {err}")
        return False

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={
        "time":       None,
        "open":       "open",
        "high":       "high",
        "low":        "low",
        "close":      "close",
        "tick_volume": "volume",
    })
    df = df.set_index("time")[["open", "high", "low", "close", "volume"]]
    df.index.name = None

    out = os.path.join(DATA_DIR, filename)
    df.to_csv(out)
    size_mb = os.path.getsize(out) / 1024 / 1024
    print(f"  Saved {len(df):,} bars → {out}  ({size_mb:.1f} MB)")
    print(f"  Date range: {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  Price range: ${df['close'].min():.2f} – ${df['close'].max():.2f}")
    return True


def main():
    print("BGC — MT5 Historical Data Exporter")
    print("=" * 45)

    if not mt5.initialize():
        print(f"FATAL: mt5.initialize() failed — {mt5.last_error()}")
        print("  Make sure MT5 is running and AutoTrading is enabled.")
        sys.exit(1)

    info = mt5.terminal_info()
    acct = mt5.account_info()
    print(f"Connected: {info.name}  |  Account: {acct.login}  |  Broker: {acct.company}")

    sym = mt5.symbol_info(SYMBOL)
    if sym is None:
        print(f"ERROR: {SYMBOL} not found in MT5. Add it to Market Watch first.")
        mt5.shutdown()
        sys.exit(1)
    if not sym.visible:
        mt5.symbol_select(SYMBOL, True)

    results = {}
    for tf_name, (tf_const, filename) in TIMEFRAMES.items():
        results[tf_name] = export_tf(tf_name, tf_const, filename)

    mt5.shutdown()

    print("\n" + "=" * 45)
    print("Export summary:")
    for tf, ok in results.items():
        print(f"  {tf:4s}: {'OK' if ok else 'FAILED'}")

    if results.get("M15"):
        print("\nReady for backtest:")
        print("  python backtest/run_backtest.py")


if __name__ == "__main__":
    main()
