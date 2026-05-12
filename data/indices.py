"""
data/indices.py — Index snapshot fetch (Nifty 50, BankNifty, Sensex).
"""

import numpy as np
import pandas as pd
import streamlit as st

from data.fetch import _download

MODE_CFG = {
    "Intraday":   dict(period="5d",  interval="5m",  ema_fast=9,  ema_slow=21,  rsi_len=14),
    "Swing":      dict(period="1y",  interval="1d",  ema_fast=50, ema_slow=200, rsi_len=21),
    "Positional": dict(period="2y",  interval="1d",  ema_fast=50, ema_slow=200, rsi_len=21),
}

ACTION_THRESHOLDS = dict(strong_buy=75, buy=58, watch=42)

_INDEX_MAP = [
    ("Nifty 50",  "^NSEI"),
    ("BankNifty", "^NSEBANK"),
    ("Sensex",    "^BSESN"),
]


def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()


def _rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / p, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


def _action_label(norm_score: float) -> str:
    if norm_score >= ACTION_THRESHOLDS["strong_buy"]: return "STRONG BUY"
    if norm_score >= ACTION_THRESHOLDS["buy"]:        return "BUY"
    if norm_score >= ACTION_THRESHOLDS["watch"]:      return "WATCH"
    return "SKIP"


@st.cache_data(ttl=300)
def fetch_indices(mode: str = "Swing") -> dict:
    cfg      = MODE_CFG[mode]
    min_bars = 60 if mode == "Intraday" else 50
    out      = {}

    for name, ticker in _INDEX_MAP:
        try:
            df = _download(ticker, cfg["period"], cfg["interval"])
            df = df.dropna()
            if len(df) < min_bars:
                out[name] = None
                continue

            close    = df["Close"]
            c, prev  = float(close.iloc[-1]), float(close.iloc[-2])
            chg, pct = c - prev, (c - prev) / prev * 100
            ef       = float(_ema(close, cfg["ema_fast"]).iloc[-1])
            es       = float(_ema(close, cfg["ema_slow"]).iloc[-1])
            e200     = float(_ema(close, 200).iloc[-1]) if len(close) >= 200 else es
            r        = float(_rsi(close, cfg["rsi_len"]).iloc[-1])
            hh       = float(close.iloc[-11:-1].max())
            trend_up = c > e200 and c > ef and ef > es

            bull  = 0
            bull += 25 if trend_up else 0
            bull += 15 if ef > es else (7 if ef > es * 0.995 else 0)
            bull += (15 if r >= 65 else 10) if r >= 60 else (5 if r > 50 else 0)
            bull += 15 if c > hh else (9 if c > hh * 0.98 else 0)
            if len(close) >= 3 and c > float(close.iloc[-3]):
                bull += 8

            norm_score = min(100.0, max(0.0, bull * 100.0 / 78))
            interval_label = {"5m": "5min", "1d": "Daily", "1wk": "Weekly"}.get(
                cfg["interval"], cfg["interval"])

            out[name] = {
                "value":    round(c, 1),
                "chg":      round(chg, 2),
                "pct":      round(pct, 2),
                "score":    round(norm_score, 1),
                "action":   _action_label(norm_score),
                "rsi":      round(r, 1),
                "trend":    "↑ Above EMAs" if trend_up else "↓ Below EMAs",
                "interval": interval_label,
                "ema_fast": cfg["ema_fast"],
                "ema_slow": cfg["ema_slow"],
            }
        except Exception:
            out[name] = None

    return out
