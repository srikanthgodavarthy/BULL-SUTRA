"""
data/fetch.py — OHLCV fetching with retry, merged intraday+daily context.
"""

import time
import logging
import warnings

import pandas as pd
import yfinance as yf
import streamlit as st

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

MODE_CFG = {
    "Intraday":   dict(period="5d",  interval="5m",
                       htf_period="3mo", htf_interval="15m"),
    "Swing":      dict(period="1y",  interval="1d",
                       htf_period="2y",  htf_interval="1wk"),
    "Positional": dict(period="2y",  interval="1d",
                       htf_period="5y",  htf_interval="1wk"),
}


def to_nse(sym: str) -> str:
    sym = sym.strip().upper()
    return sym if sym.endswith(".NS") else sym + ".NS"


def _download(ticker: str, period: str, interval: str,
              retries: int = 3) -> pd.DataFrame:
    for attempt in range(retries):
        try:
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                auto_adjust=True,
                progress=False,
                threads=False
            )

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.dropna(how="all")

            if df.empty:
                print(f"[FETCH FAIL] {ticker} -> EMPTY ({period}, {interval})")

            return df

        except Exception as e:
            print(f"[FETCH ERROR] {ticker} attempt {attempt+1}: {e}")
            if attempt < retries - 1:
                time.sleep(min(0.5 * (attempt + 1), 1.0))

    return pd.DataFrame()


def _clean(df: pd.DataFrame, min_bars: int) -> pd.DataFrame | None:
    if df.empty:
        return None

    # ✅ Validate required columns
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(df.columns):
        print(f"[CLEAN FAIL] Missing columns: {df.columns}")
        return None

    # ✅ Handle partial last candle safely
    if pd.isna(df["Close"].iloc[-1]):
        df = df.iloc[:-1]

    df["Close"] = df["Close"].ffill()

    if "Volume" in df.columns:
        df["Volume"] = df["Volume"].fillna(0)

    df = df.dropna(subset=["Close"])

    # ✅ RELAX bar requirement (CRITICAL FIX)
    if len(df) < int(min_bars * 0.6):
        print(f"[CLEAN FAIL] Too short: {len(df)} bars (min {min_bars})")
        return None

    return df


def _fetch_one(args: tuple) -> tuple:
    """Primary fetch (primary timeframe). Returns (sym, df | None)."""
    sym, mode, min_bars = args
    cfg = MODE_CFG[mode]
    ticker = to_nse(sym)

    print(f"\n--- FETCH {ticker} [{mode}] ---")

    raw = _download(ticker, cfg["period"], cfg["interval"])

    df = _clean(raw, min_bars)   # ✅ use one variable consistently

    if df is None:
        print(f"[REJECT] {ticker} -> CLEAN FAILED")
    else:
        print(f"[SUCCESS] {ticker} -> {len(df)} bars")

    return sym, df   # ✅ single return only


def _fetch_one_with_daily(args: tuple) -> tuple:
    """
    PERF-2: merged primary + daily context in one call per symbol.
    Returns (sym, primary_df | None, daily_df | None).
    """
    sym, mode, min_bars = args
    primary_sym, primary_df = _fetch_one(args)
    daily_df = None
    if mode == "Intraday" and primary_df is not None:
        _, daily_df = _fetch_one((sym, "Swing", 30))

    if daily_df is None:
            print(f"[WARN] {sym} -> DAILY FAILED (continuing)")
    return primary_sym, primary_df, daily_df


@st.cache_data(ttl=300)
def fetch_nifty(mode: str = "Swing") -> pd.Series:
    cfg = MODE_CFG[mode]
    df  = _download("^NSEI", cfg["period"], cfg["interval"])
    return df["Close"].dropna()


@st.cache_data(ttl=300)
def fetch_vix() -> tuple:
    try:
        df = _download("^INDIAVIX", "5d", "1d")
        df = df.dropna()
        if df.empty:
            return None, "UNKNOWN"
        v     = float(df["Close"].iloc[-1])
        label = "CALM" if v < 15 else ("CAUTION" if v < 25 else "STRESS")
        return round(v, 2), label
    except Exception:
        return None, "UNKNOWN"
