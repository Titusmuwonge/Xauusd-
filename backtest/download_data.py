"""
XAUUSD Historical Data Downloader
Sources: yfinance (quick), Dukascopy (deep history)
Outputs a single clean CSV: data/XAUUSD_M15.csv
"""

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import struct
import io

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
OUTPUT_FILE = os.path.join(DATA_DIR, "XAUUSD_M15.csv")


# ──────────────────────────────────────────────────────────────
# SOURCE 1 — yfinance (fast, ~60 days of M15, good for a quick run)
# ──────────────────────────────────────────────────────────────
def fetch_yfinance(years_back: int = 2) -> pd.DataFrame:
    """
    GC=F = Gold Futures (CME) — closest liquid proxy for XAUUSD spot.
    yfinance caps 15m data at ~60 days; use interval='1d' for long history.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("pip install yfinance")

    print("Fetching from yfinance (GC=F 15m — last 60 days)...")
    raw = yf.download("GC=F", period="60d", interval="15m", auto_adjust=True, progress=False)

    if raw.empty:
        raise ValueError("yfinance returned no data")

    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df.columns = ["open", "high", "low", "close", "volume"]
    df.dropna(inplace=True)
    print(f"  yfinance: {len(df)} bars  {df.index[0]} → {df.index[-1]}")
    return df


# ──────────────────────────────────────────────────────────────
# SOURCE 2 — Dukascopy (free tick data, reconstructed to M15)
# Up to 10+ years of history. No account needed.
# ──────────────────────────────────────────────────────────────
DUKA_URL = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

def _fetch_duka_hour(year: int, month: int, day: int, hour: int) -> pd.DataFrame:
    """Download one hour of Dukascopy bi5 tick data and decode it."""
    url = DUKA_URL.format(year=year, month=month - 1, day=day, hour=hour)
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200 or len(r.content) < 10:
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

    import lzma
    try:
        raw = lzma.decompress(r.content)
    except Exception:
        return pd.DataFrame()

    # Each tick: 5 int32 (ms_offset, ask*10, bid*10, ask_vol*1e6, bid_vol*1e6)
    record_size = 20
    n = len(raw) // record_size
    ticks = []
    base_ts = datetime(year, month, day, hour)
    for i in range(n):
        chunk = raw[i * record_size:(i + 1) * record_size]
        ms, ask_raw, bid_raw, avol, bvol = struct.unpack(">iiiii", chunk)
        ts = base_ts + timedelta(milliseconds=ms)
        bid = bid_raw / 100000.0
        ask = ask_raw / 100000.0
        mid = (bid + ask) / 2.0
        ticks.append((ts, mid, (avol + bvol) / 2e6))

    if not ticks:
        return pd.DataFrame()

    df = pd.DataFrame(ticks, columns=["time", "price", "volume"])
    df.set_index("time", inplace=True)
    return df


def fetch_dukascopy(years_back: int = 3) -> pd.DataFrame:
    """
    Download XAUUSD tick data from Dukascopy and resample to M15.
    Warning: slow on first run (many HTTP requests). Results are cached.
    """
    cache_file = os.path.join(DATA_DIR, f"XAUUSD_ticks_{years_back}y.parquet")
    if os.path.exists(cache_file):
        print(f"Loading cached tick data from {cache_file}...")
        ticks = pd.read_parquet(cache_file)
        print(f"  Loaded {len(ticks)} ticks")
    else:
        print(f"Downloading {years_back} years of XAUUSD ticks from Dukascopy...")
        print("  This takes 10–30 minutes on first run. Subsequent runs use cache.")
        end   = datetime.utcnow()
        start = end - timedelta(days=365 * years_back)
        all_ticks = []
        current = start

        while current <= end:
            for hour in range(24):
                chunk = _fetch_duka_hour(current.year, current.month, current.day, hour)
                if not chunk.empty:
                    all_ticks.append(chunk)
            # Progress dot every week
            if current.weekday() == 0:
                print(f"  {current.date()} ...", end="\r")
            current += timedelta(days=1)
            time.sleep(0.05)  # be polite to Dukascopy

        if not all_ticks:
            raise ValueError("Dukascopy returned no data — check internet connection")

        ticks = pd.concat(all_ticks).sort_index()
        ticks.to_parquet(cache_file)
        print(f"\n  Downloaded {len(ticks)} ticks → cached to {cache_file}")

    # Resample ticks → M15 OHLCV
    df = ticks["price"].resample("15min").ohlc()
    df["volume"] = ticks["volume"].resample("15min").sum()
    df.dropna(inplace=True)
    print(f"  Resampled to {len(df)} M15 bars  {df.index[0]} → {df.index[-1]}")
    return df


# ──────────────────────────────────────────────────────────────
# SOURCE 3 — Alpha Vantage (free API key, 5 calls/min, 2y daily)
# ──────────────────────────────────────────────────────────────
def fetch_alpha_vantage(api_key: str, interval: str = "15min") -> pd.DataFrame:
    """
    Get XAUUSD from Alpha Vantage. Free tier: 500 calls/day, 5/min.
    Get a free API key at alphavantage.co
    """
    url = (
        f"https://www.alphavantage.co/query"
        f"?function=FX_INTRADAY"
        f"&from_symbol=XAU"
        f"&to_symbol=USD"
        f"&interval={interval}"
        f"&outputsize=full"
        f"&apikey={api_key}"
    )
    print(f"Fetching from Alpha Vantage (XAU/USD {interval})...")
    r = requests.get(url, timeout=30)
    data = r.json()

    key = f"Time Series FX ({interval})"
    if key not in data:
        raise ValueError(f"Alpha Vantage error: {data.get('Note', data)}")

    rows = []
    for ts, vals in data[key].items():
        rows.append({
            "time":   pd.to_datetime(ts),
            "open":   float(vals["1. open"]),
            "high":   float(vals["2. high"]),
            "low":    float(vals["3. low"]),
            "close":  float(vals["4. close"]),
            "volume": 0.0
        })

    df = pd.DataFrame(rows).set_index("time").sort_index()
    df.dropna(inplace=True)
    print(f"  Alpha Vantage: {len(df)} bars  {df.index[0]} → {df.index[-1]}")
    return df


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────
def download(source: str = "yfinance", av_api_key: str = "", years_back: int = 3) -> pd.DataFrame:
    """
    source: 'yfinance' | 'dukascopy' | 'alphavantage'
    """
    if source == "yfinance":
        df = fetch_yfinance()
    elif source == "dukascopy":
        df = fetch_dukascopy(years_back)
    elif source == "alphavantage":
        if not av_api_key:
            raise ValueError("Pass your Alpha Vantage API key via av_api_key=")
        df = fetch_alpha_vantage(av_api_key)
    else:
        raise ValueError(f"Unknown source '{source}'")

    df.to_csv(OUTPUT_FILE)
    print(f"\nSaved {len(df)} bars to {OUTPUT_FILE}")
    return df


if __name__ == "__main__":
    df = download(source="yfinance")
    print(df.tail())
