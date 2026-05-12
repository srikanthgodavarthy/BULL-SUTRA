"""
data/htf.py — Higher-timeframe trend fetch and analysis.
FIX-1: closed-candle only — last (forming) bar is dropped before EMA calc.
"""

import time
import concurrent.futures

import pandas as pd
import streamlit as st

from data.fetch import _download, to_nse

MODE_CFG = {
    "Intraday":   dict(htf_period="3mo", htf_interval="15m"),
    "Swing":      dict(htf_period="2y",  htf_interval="1wk"),
    "Positional": dict(htf_period="5y",  htf_interval="1wk"),
}


@st.cache_data(ttl=900)
def _fetch_htf_cached(ticker: str, period: str, interval: str) -> pd.DataFrame:
    return _download(ticker, period, interval)


def _htf_trend_from_df(df: pd.DataFrame, mode: str) -> tuple[bool, str]:
    """
    FIX-1: drop the live/incomplete HTF candle before computing EMAs.
    Prevents repainting from a partially-formed bar.
    """
    if df is None or df.empty:
        return True, "HTF-UNKNOWN"

    if mode == "Intraday" and len(df) > 2:
        df = df.iloc[:-1].copy()

    min_bars = 55 if mode == "Intraday" else 26
    if len(df) < min_bars:
        return True, "HTF-UNKNOWN"

    cl = df["Close"]
    ef = float(cl.ewm(span=(21 if mode == "Intraday" else 13), adjust=False).mean().iloc[-1])
    es = float(cl.ewm(span=(55 if mode == "Intraday" else 26), adjust=False).mean().iloc[-1])
    c  = float(cl.iloc[-1])
    up = c > ef > es
    return up, ("HTF↑" if up else "HTF↓")


def prefetch_htf_parallel(symbols: list, mode: str,
                           status_text, progress_bar) -> dict:
    """
    PERF-1/3: parallel HTF pre-fetch (up to 32 workers, no sleep throttle).
    """
    cfg     = MODE_CFG[mode]
    results = {}
    total   = len(symbols)

    def _fetch_one_htf(sym):
        ticker = to_nse(sym)
        df     = _fetch_htf_cached(ticker, cfg["htf_period"], cfg["htf_interval"])
        return sym, _htf_trend_from_df(df, mode)

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, total)) as pool:
        futures = {pool.submit(_fetch_one_htf, sym): sym for sym in symbols}
        for fut in concurrent.futures.as_completed(futures):
            sym, result = fut.result()
            results[sym] = result
            completed   += 1
            progress_bar.progress(0.15 + completed / total * 0.25)
            if completed % 20 == 0:
                status_text.text(f"HTF pre-fetch {completed}/{total}…")

    return results
