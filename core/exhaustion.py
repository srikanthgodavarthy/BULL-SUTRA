"""
core/exhaustion.py — Exhaustion / overextension detection.
"""

import numpy as np
import pandas as pd

from core.phases import PHASE_BRK, PHASE_CONT, PHASE_ENTRY, PHASE_SETUP, PHASE_EXIT

VIX_CALM   = 15
VIX_STRESS = 25

EXT_CFG = {
    "Intraday":   dict(rsi_ceil=80, ema_dist=3.5, atr_exp=2.5, parab=3.0, clim_vol=3.0, div_bars=10),
    "Swing":      dict(rsi_ceil=78, ema_dist=3.0, atr_exp=2.5, parab=3.0, clim_vol=3.0, div_bars=14),
    "Positional": dict(rsi_ceil=75, ema_dist=2.5, atr_exp=2.0, parab=2.5, clim_vol=2.5, div_bars=20),
}

EXT_PENALTIES = {
    "rsi_overheat":     -8,
    "atr_extension":    -8,
    "parabolic":        -6,
    "ema_distance":     -5,
    "climactic_volume": -6,
    "mom_exhaustion":   -4,
    "bearish_div":      -6,
}


def detect_exhaustion(close, high, low, volume, rsi_series,
                      e_fast_s, atr_s, atr_mean, c, v, vol_avg,
                      mode, vix_val=None):
    cfg   = EXT_CFG[mode]
    n     = len(close)
    flags = {k: False for k in EXT_PENALTIES}
    labels = []

    rsi_ceil = cfg["rsi_ceil"]
    if vix_val is not None:
        if vix_val < VIX_CALM:     rsi_ceil += 2
        elif vix_val > VIX_STRESS: rsi_ceil -= 3

    rsi_now = float(rsi_series.iloc[-1])
    if rsi_now > rsi_ceil:
        flags["rsi_overheat"] = True; labels.append("Too hot")

    atr_val = float(atr_s.iloc[-1])
    if atr_mean > 0 and atr_val > atr_mean * cfg["atr_exp"]:
        flags["atr_extension"] = True; labels.append("Range blowout")

    if n >= 23:
        daily_pct  = close.pct_change().dropna()
        hist_sigma = float(daily_pct.iloc[-20:].std())
        exp_3b     = hist_sigma * (3 ** 0.5)
        act_3b     = abs(float(close.iloc[-1]) - float(close.iloc[-4])) / float(close.iloc[-4])
        if exp_3b > 0 and act_3b > cfg["parab"] * exp_3b:
            flags["parabolic"] = True; labels.append("Parabolic")

    e_fast_now = float(e_fast_s.iloc[-1])
    if atr_val > 0:
        ema_dist_atrs = (c - e_fast_now) / atr_val
        if ema_dist_atrs > cfg["ema_dist"]:
            flags["ema_distance"] = True; labels.append("EMA overext")

    wick_thresh = 0.35 if (c > 0 and atr_val / c > 0.03) else 0.30

    if n >= 12 and vol_avg > 0:
        prior_run = c > float(close.iloc[-11])
        up_bar    = c > float(close.iloc[-2])
        if prior_run and up_bar and v > vol_avg * cfg["clim_vol"]:
            bar_range  = float(high.iloc[-1]) - float(low.iloc[-1])
            upper_wick = float(high.iloc[-1]) - c
            if bar_range > 0 and (upper_wick / bar_range) > wick_thresh:
                flags["climactic_volume"] = True; labels.append("Vol climax")

    if n >= 10:
        lookback      = min(cfg["div_bars"], n - 1)
        rsi_win       = rsi_series.iloc[-lookback:]
        rsi_peak      = float(rsi_win.max())
        rsi_peak_idx  = rsi_win.idxmax()
        price_at_peak = float(close[rsi_peak_idx])
        gap_req = 5 if mode == "Intraday" else 3
        if (rsi_now < rsi_peak - gap_req
                and c > price_at_peak
                and rsi_win.idxmax() != rsi_win.index[-1]):
            flags["mom_exhaustion"] = True; labels.append("Mom fade")

    if n >= 20:
        lookback  = min(cfg["div_bars"] * 2, n - 2)
        h_slice   = high.iloc[-lookback:]
        r_slice   = rsi_series.iloc[-lookback:]
        pivot_idx = []
        for i in range(1, len(h_slice) - 1):
            if (float(h_slice.iloc[i]) > float(h_slice.iloc[i - 1])
                    and float(h_slice.iloc[i]) > float(h_slice.iloc[i + 1])):
                pivot_idx.append(i)
        if len(pivot_idx) >= 2:
            p1, p2   = pivot_idx[-2], pivot_idx[-1]
            ph1, ph2 = float(h_slice.iloc[p1]), float(h_slice.iloc[p2])
            rh1, rh2 = float(r_slice.iloc[p1]), float(r_slice.iloc[p2])
            if ph2 > ph1 and rh2 < rh1 - 2 and (len(h_slice) - 1 - p2) <= 5:
                flags["bearish_div"] = True; labels.append("Bear div")

    penalty = sum(EXT_PENALTIES[k] for k, v2 in flags.items() if v2)
    n_flags = sum(flags.values())
    return flags, float(penalty), labels, n_flags


def ext_phase_override(phase, ext_flags, n_flags, mode):
    rsi_ext     = ext_flags.get("rsi_overheat", False)
    atr_ext     = ext_flags.get("atr_extension", False)
    is_critical = n_flags >= 3 or (rsi_ext and atr_ext)
    is_moderate = n_flags == 2
    if is_critical:
        if phase == PHASE_BRK:   return PHASE_EXIT,  "ext-critical→EXIT"
        if phase == PHASE_CONT:  return PHASE_SETUP, "ext-critical→SETUP"
        if phase == PHASE_ENTRY: return PHASE_SETUP, "ext-critical→SETUP"
    elif is_moderate:
        if phase == PHASE_BRK:  return PHASE_SETUP, "ext-moderate→SETUP"
    return phase, None


def ext_action_cap(action, n_flags, vix_val=None):
    if n_flags == 0 and (vix_val is None or vix_val < VIX_STRESS):
        return action
    if vix_val is not None and vix_val >= VIX_STRESS:
        return "WATCH" if action in ("STRONG BUY", "BUY") else action
    if n_flags >= 3:
        return "WATCH" if action in ("STRONG BUY", "BUY") else action
    return "BUY" if action == "STRONG BUY" else action
