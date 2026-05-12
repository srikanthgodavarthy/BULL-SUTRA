"""
core/liquidity.py — Liquidity checks and FIX-4 intraday vol normalisation.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# NSE cash-market session  09:15 – 15:30 IST
NSE_OPEN_HOUR,  NSE_OPEN_MIN  = 9,  15
NSE_CLOSE_HOUR, NSE_CLOSE_MIN = 15, 30
NSE_SESSION_MINUTES = (
    (NSE_CLOSE_HOUR * 60 + NSE_CLOSE_MIN)
    - (NSE_OPEN_HOUR  * 60 + NSE_OPEN_MIN)
)   # 375

LIQUIDITY_MIN_CR = 5.0


# ── FIX-4: session-elapsed fraction ───────────────────────────────────────────

def _session_elapsed_fraction() -> float:
    """
    Fraction of the NSE session (09:15-15:30 IST) completed right now.
    Clamped to [0.05, 1.0] to avoid near-zero early in the day.
    """
    now_ist     = datetime.utcnow() + timedelta(hours=5, minutes=30)
    mins_since  = (now_ist.hour * 60 + now_ist.minute) - (NSE_OPEN_HOUR * 60 + NSE_OPEN_MIN)
    return float(np.clip(mins_since / NSE_SESSION_MINUTES, 0.05, 1.0))


# ── FIX-4: time-normalised volume average ─────────────────────────────────────

def _intraday_vol_avg(volume: pd.Series, bars_per_day: int) -> float:
    """
    For intraday data, scale today's partial-session volume to a full-session
    equivalent, then average with recent prior-day totals.
    """
    elapsed_frac = _session_elapsed_fraction()

    today_bars = int(min(bars_per_day * elapsed_frac + 1, len(volume)))
    today_vol  = float(volume.iloc[-today_bars:].sum())
    today_proj = today_vol / elapsed_frac

    if len(volume) > bars_per_day + today_bars:
        prior      = volume.iloc[:-(today_bars)].rolling(bars_per_day).sum().dropna()
        prior_daily = prior.iloc[-5:].values.tolist()
    else:
        prior_daily = []

    all_days = prior_daily + [today_proj]
    return float(np.mean(all_days)) if all_days else float(volume.mean() * bars_per_day)


# ── Liquidity gate ─────────────────────────────────────────────────────────────

def liquidity_ok(df: pd.DataFrame, min_cr: float = LIQUIDITY_MIN_CR,
                 mode: str = "Swing") -> tuple[bool, float]:
    try:
        traded = df["Close"] * df["Volume"]
        n_rows = len(df)

        if n_rows >= 2:
            try:
                delta_min = (df.index[1] - df.index[0]).total_seconds() / 60
            except Exception:
                delta_min = 1440
        else:
            delta_min = 1440

        if delta_min <= 5:       bars_per_day = 75
        elif delta_min <= 15:    bars_per_day = 25
        elif delta_min <= 30:    bars_per_day = 13
        elif delta_min < 240:    bars_per_day = 7
        else:                    bars_per_day = 1

        # FIX-4: time-normalised path for intraday
        if mode == "Intraday" and bars_per_day > 1:
            avg_daily_vol = _intraday_vol_avg(df["Volume"], bars_per_day)
            avg_cr        = float(avg_daily_vol * float(df["Close"].iloc[-1])) / 1e7
        else:
            daily_traded = traded.rolling(bars_per_day).sum()
            avg_cr       = float(daily_traded.rolling(20).mean().iloc[-1]) / 1e7

        return avg_cr >= min_cr, round(avg_cr, 1)
    except Exception:
        return True, 0.0
