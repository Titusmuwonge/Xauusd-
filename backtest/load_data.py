"""
Flexible OHLCV data loader.

Handles:
  • CSVs exported by export_from_mt5.py (our standard format)
  • MT4/MT5 terminal history exports (Date,Time,Open,High,Low,Close,Volume)
  • Dukascopy JForex exports
  • TradingView exports
  • Generic wide-format CSVs — column names are detected automatically

Usage:
    from backtest.load_data import load_ohlcv
    df = load_ohlcv("backtest/data/XAUUSD_M15.csv")
"""

from __future__ import annotations
import os
import re
import pandas as pd
import numpy as np


# ── Column-name normalisers ──────────────────────────────────────────────────
_COL_MAP = {
    # open
    "open": "open", "o": "open",
    # high
    "high": "high", "h": "high",
    # low
    "low": "low", "l": "low",
    # close
    "close": "close", "c": "close", "last": "close", "price": "close",
    # volume
    "volume": "volume", "vol": "volume", "v": "volume",
    "tick_volume": "volume", "tickvol": "volume",
}

_DATE_COLS = {"date", "datetime", "timestamp", "time", "Date", "Datetime"}


def _detect_columns(df: pd.DataFrame) -> dict[str, str]:
    """Return {original_col: standard_col} for recognised columns."""
    mapping = {}
    for col in df.columns:
        key = col.strip().lower().replace(" ", "_")
        if key in _COL_MAP:
            mapping[col] = _COL_MAP[key]
    return mapping


def _parse_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    """Try to build a DatetimeIndex from whatever the file provides."""
    idx = df.index

    # Already a DatetimeIndex
    if isinstance(idx, pd.DatetimeIndex):
        return idx.tz_localize(None) if idx.tz is not None else idx

    # Numeric index — look for date/time columns in the DataFrame itself
    for col in list(df.columns):
        if col.lower().strip() in {c.lower() for c in _DATE_COLS}:
            try:
                ts = pd.to_datetime(df[col])
                return ts.dt.tz_localize(None) if ts.dt.tz is not None else ts
            except Exception:
                pass

    # Try to parse the index itself
    try:
        ts = pd.to_datetime(idx)
        return ts.tz_localize(None) if ts.tz is not None else ts
    except Exception:
        pass

    raise ValueError("Cannot find a datetime column in the CSV. "
                     "Expected a column named Date, Datetime, Time, or Timestamp.")


def load_ohlcv(path: str, timeframe: str = "M15") -> pd.DataFrame:
    """
    Load an OHLCV CSV from *path* and return a clean DataFrame with columns
    [open, high, low, close, volume] and a DatetimeIndex (UTC, no tzinfo).

    Raises ValueError if mandatory OHLC columns cannot be found.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Data file not found: {path}")

    # Auto-detect separator (semicolon vs comma)
    with open(path, "r") as fh:
        first_line = fh.readline()
    sep = ";" if first_line.count(";") >= 3 else ","

    # Try common MT4/MT5 export: separate Date + Time columns  OR  combined Date col
    raw = pd.read_csv(path, sep=sep, nrows=3, header=0)
    cols_lower = [c.strip().lower() for c in raw.columns]

    if "date" in cols_lower and "time" in cols_lower:
        # MT4/MT5 style: Date,Time,Open,High,Low,Close,Volume
        df = pd.read_csv(path, sep=sep, header=0)
        date_col = df.columns[cols_lower.index("date")]
        time_col = df.columns[cols_lower.index("time")]
        df.index = pd.to_datetime(df[date_col].astype(str) + " " + df[time_col].astype(str))
        df = df.drop(columns=[date_col, time_col])
    else:
        # Combined datetime in first column — handle "YYYY.MM.DD HH:MM" (dots) or ISO
        df = pd.read_csv(path, sep=sep, header=0)
        # Find the datetime column (first non-numeric or explicitly named)
        dt_col = None
        for col in df.columns:
            if col.strip().lower() in {c.lower() for c in _DATE_COLS}:
                dt_col = col
                break
        if dt_col is None:
            # Fall back: first column
            dt_col = df.columns[0]
        # Replace dots-as-date-separator: "2004.06.11" → "2004-06-11"
        df[dt_col] = df[dt_col].astype(str).str.replace(
            r"^(\d{4})\.(\d{2})\.(\d{2})", r"\1-\2-\3", regex=True
        )
        df.index = pd.to_datetime(df[dt_col])
        df = df.drop(columns=[dt_col])

    df.index.name = None

    # Normalise column names
    col_map = _detect_columns(df)
    df = df.rename(columns=col_map)

    # Strip any date/time columns that snuck into data
    for col in list(df.columns):
        if col.lower().strip() in {c.lower() for c in _DATE_COLS}:
            df = df.drop(columns=[col])

    # Ensure mandatory columns exist
    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing mandatory columns {missing} in {path}.\n"
            f"Columns found after normalisation: {list(df.columns)}\n"
            f"Rename your columns to: open, high, low, close, volume"
        )

    if "volume" not in df.columns:
        df["volume"] = 0.0

    df = df[["open", "high", "low", "close", "volume"]].copy()

    # Fix the datetime index
    df.index = _parse_index(df)
    df = df.sort_index()

    # Convert to float and drop rows with any NaN in OHLC
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])

    # Sanity checks
    bad_hl = (df["high"] < df["low"]).sum()
    if bad_hl > 0:
        print(f"  WARNING: {bad_hl} bars where high < low — they will be fixed")
        df["high"] = np.maximum(df["high"], df["low"])

    print(f"  Loaded {len(df):,} {timeframe} bars  "
          f"{df.index[0].date()} → {df.index[-1].date()}  "
          f"${df['close'].min():.2f}–${df['close'].max():.2f}")
    return df


def preferred_data_path(timeframe: str = "M15") -> str:
    """Return the path of the best available data file for *timeframe*."""
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    data_dir  = os.path.join(os.path.dirname(__file__), "data")
    candidates = [
        # User-uploaded real data in repo root
        os.path.join(repo_root, "XAU_15m_data.csv"),
        os.path.join(repo_root, f"XAU_{timeframe}_data.csv"),
        # Real MT5 export in data dir
        os.path.join(data_dir, f"XAUUSD_{timeframe}.csv"),
        os.path.join(data_dir, f"xauusd_{timeframe.lower()}.csv"),
        os.path.join(data_dir, f"GOLD_{timeframe}.csv"),
        os.path.join(data_dir, f"XAUUSD_{timeframe}_real.csv"),
    ]
    for p in candidates:
        if os.path.exists(p) and os.path.getsize(p) > 100_000:
            return os.path.normpath(p)
    # Fall back to synthetic
    return os.path.join(data_dir, f"XAUUSD_{timeframe}.csv")
