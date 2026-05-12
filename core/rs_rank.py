"""
core/rs_rank.py — Relative-strength rank computation (vectorised, PERF-4).
"""

import numpy as np
import pandas as pd


def _52w_return(close_series: pd.Series) -> float:
    if len(close_series) < 10:
        return 0.0
    lookback = min(252, len(close_series) - 1)
    c_now    = float(close_series.iloc[-1])
    c_base   = float(close_series.iloc[-lookback])
    if c_base == 0:
        return 0.0
    return round((c_now - c_base) / c_base * 100, 2)


def compute_rs_ranks(sym_returns: dict) -> dict:
    """
    Vectorised percentile rank (0-100) for a dict of {symbol: 52w_return}.
    Uses numpy argsort — O(n log n) vs O(n²) naïve approach.
    """
    if not sym_returns:
        return {}
    syms  = list(sym_returns.keys())
    vals  = np.array([sym_returns[s] for s in syms], dtype=np.float64)
    order = np.argsort(vals)
    ranks = np.empty_like(order)
    ranks[order] = np.arange(len(vals))
    normalized   = np.round(ranks / max(len(vals) - 1, 1) * 100).astype(int)
    return dict(zip(syms, normalized.tolist()))
