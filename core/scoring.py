"""
core/scoring.py — Bull Sutra Pro v11
Stock scoring engine with all FIX patches applied.
"""

import numpy as np
import pandas as pd

from core.phases import detect_phase_and_entry, ext_phase_override, record_phase_transition, phase_transition_conf_bonus, get_phase_arrow, PHASE_IDLE, PHASE_SETUP, PHASE_ENTRY, PHASE_CONT, PHASE_BRK, PHASE_EXIT, PHASE_ORDER
from core.exhaustion import detect_exhaustion, ext_action_cap, EXT_PENALTIES
from core.confidence import compute_confidence
from core.targets import _compute_targets
from core.liquidity import liquidity_ok, _intraday_vol_avg
from core.rs_rank import _52w_return

from data.fetch import fetch_nifty

# ── Config ─────────────────────────────────────────────────────────────────────

MODE_CFG = {
    "Intraday":   dict(period="5d",  interval="5m",  ema_fast=9,  ema_slow=21,
                       atr_mult=1.5, atr_wide=3.0, atr_max=1.0,
                       mom1_th=2,  mom3_th=5,  mom6_th=8,  score_th=65, rsi_len=14,
                       htf_period="3mo", htf_interval="15m", validity_hours=4),
    "Swing":      dict(period="1y",  interval="1d",  ema_fast=50, ema_slow=200,
                       atr_mult=2.5, atr_wide=4.0, atr_max=1.5,
                       mom1_th=3,  mom3_th=7,  mom6_th=10, score_th=70, rsi_len=21,
                       htf_period="2y", htf_interval="1wk", validity_hours=72),
    "Positional": dict(period="2y",  interval="1d",  ema_fast=50, ema_slow=200,
                       atr_mult=3.5, atr_wide=5.0, atr_max=1.5,
                       mom1_th=5,  mom3_th=10, mom6_th=15, score_th=70, rsi_len=21,
                       htf_period="5y", htf_interval="1wk", validity_hours=240),
}

BULL_MAX     = 120
BULL_MAX_V11 = 113   # FIX-6: reduced by 7 (removed EMA double-count, max bonus now 8 vs 15)

ACTION_THRESHOLDS = dict(strong_buy=75, buy=58, watch=42)

VIX_CALM    = 15
VIX_CAUTION = 20
VIX_STRESS  = 25

SECTOR_MAP = {
    "RELIANCE":"Energy","ONGC":"Energy","BPCL":"Energy","COALINDIA":"Energy",
    "NTPC":"Utilities","POWERGRID":"Utilities","ADANIENT":"Utilities",
    "ADANIPORTS":"Industrials","LT":"Industrials","BHEL":"Industrials",
    "HDFCBANK":"Financials","ICICIBANK":"Financials","SBIN":"Financials",
    "KOTAKBANK":"Financials","AXISBANK":"Financials","BAJFINANCE":"Financials",
    "BAJAJFINSV":"Financials","SBILIFE":"Financials","HDFCLIFE":"Financials",
    "ICICIPRULI":"Financials","INDUSINDBK":"Financials",
    "TCS":"IT","INFY":"IT","WIPRO":"IT","HCLTECH":"IT","TECHM":"IT",
    "SUNPHARMA":"Healthcare","DRREDDY":"Healthcare","CIPLA":"Healthcare",
    "DIVISLAB":"Healthcare","APOLLOHOSP":"Healthcare",
    "HINDUNILVR":"FMCG","ITC":"FMCG","NESTLEIND":"FMCG","BRITANNIA":"FMCG","TATACONSUM":"FMCG",
    "ASIANPAINT":"Chemicals","ULTRACEMCO":"Materials","GRASIM":"Materials",
    "TATASTEEL":"Metals","JSWSTEEL":"Metals","HINDALCO":"Metals","VEDL":"Metals","NMDC":"Metals",
    "MARUTI":"Auto","TATAMOTORS":"Auto","M&M":"Auto","EICHERMOT":"Auto",
    "HEROMOTOCO":"Auto","BAJAJ-AUTO":"Auto",
    "TITAN":"Consumer","BHARTIARTL":"Telecom",
}


# ── Math helpers ───────────────────────────────────────────────────────────────

def ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()


def rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / p, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


def atr_series(df: pd.DataFrame, p: int = 14) -> pd.Series:
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        (hi - lo),
        (hi - cl.shift()).abs(),
        (lo - cl.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / p, adjust=False).mean()


def fib_levels(df: pd.DataFrame, lookback: int = 30):
    sw_hi = float(df["High"].iloc[-lookback:].max())
    sw_lo = float(df["Low"].iloc[-lookback:].min())
    rng   = sw_hi - sw_lo
    if rng == 0:
        return sw_hi, sw_lo, {}, rng
    return sw_hi, sw_lo, {
        "236": sw_hi - rng * 0.236, "382": sw_hi - rng * 0.382,
        "500": sw_hi - rng * 0.500, "618": sw_hi - rng * 0.618,
        "786": sw_hi - rng * 0.786,
        "ext127": sw_hi + rng * 0.272, "ext161": sw_hi + rng * 0.618,
        "ext261": sw_hi + rng * 1.618,
    }, rng


def action_label(norm_score: float) -> str:
    if norm_score >= ACTION_THRESHOLDS["strong_buy"]: return "STRONG BUY"
    if norm_score >= ACTION_THRESHOLDS["buy"]:        return "BUY"
    if norm_score >= ACTION_THRESHOLDS["watch"]:      return "WATCH"
    return "SKIP"


def _market_regime(nifty_close: pd.Series):
    if len(nifty_close) < 50:
        return True, "UNKNOWN"
    ema20 = float(ema(nifty_close, 20).iloc[-1])
    ema50 = float(ema(nifty_close, 50).iloc[-1])
    bull  = (float(nifty_close.iloc[-1]) > ema50) and (ema20 > ema50)
    return bull, ("BULLISH" if bull else "BEARISH")


def fmt(val) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    return f"₹{val:,.2f}"


# ── Core scorer ────────────────────────────────────────────────────────────────

def score_stock(df: pd.DataFrame, nifty_close: pd.Series,
                mode: str = "Swing", daily_close: pd.Series = None,
                market_bullish: bool = True, vix_val: float = None,
                min_liquidity_cr: float = 5.0,
                sym: str = None, htf_up: bool = True, rs_rank: int = 50,
                phase_history_snapshot: dict = None) -> dict | None:
    """
    Full bull-score pipeline.

    FIX-4: vol_avg for Intraday uses time-normalised _intraday_vol_avg().
    FIX-6: EMA cross block replaced with fresh-cross bonus (non-redundant).
    """
    try:
        cfg    = MODE_CFG[mode]
        close  = df["Close"]
        volume = df["Volume"]
        n      = len(close)
        if n < 50:
            return None

        liq_ok, avg_cr = liquidity_ok(df, min_liquidity_cr, mode=mode)

        c        = float(close.iloc[-1])
        prev     = float(close.iloc[-2])
        e_fast_s = ema(close, cfg["ema_fast"])
        e_slow_s = ema(close, cfg["ema_slow"])
        e_fast   = float(e_fast_s.iloc[-1])
        e_slow   = float(e_slow_s.iloc[-1])
        e200_s   = ema(close, 200)
        e200     = float(e200_s.iloc[-1]) if n >= 200 else None
        atr_s    = atr_series(df)
        atr_val  = float(atr_s.iloc[-1])
        atr_mean = float(atr_s.rolling(20).mean().iloc[-1])
        chg      = round(((c - prev) / prev) * 100, 2)
        hh       = float(close.iloc[-11:-1].max())

        # ── FIX-4: time-normalised vol_avg ────────────────────────────────────
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

        if mode == "Intraday" and bars_per_day > 1:
            vol_avg = _intraday_vol_avg(volume, bars_per_day)
        else:
            vol_avg = float(volume.rolling(20).mean().iloc[-1])

        v = float(volume.iloc[-1])

        above_ema50 = c > float(ema(close, 50).iloc[-1])

        rs_raw = 0.0
        if n >= 6 and len(nifty_close) >= 6:
            rs_raw = ((c - float(close.iloc[-6])) / float(close.iloc[-6]) -
                      (float(nifty_close.iloc[-1]) - float(nifty_close.iloc[-6])) /
                      float(nifty_close.iloc[-6])) * 100

        trend_up     = (e200 is None or c > e200) and c > e_fast and e_fast > e_slow
        trend_down   = (e200 is None or c < e200) and c < e_fast and e_fast < e_slow
        trend_strong = c > e_fast and e_fast > e_slow
        ema_stack    = (e200 is not None) and (c > e200) and (e_fast > e_slow) and (e_fast > e200)

        # ── FIX-6: fresh EMA cross (non-redundant) ────────────────────────────
        fresh_cross = False
        if n >= 6 and e_fast > e_slow:
            lookback_cross = min(5, n - 1)
            for k in range(1, lookback_cross + 1):
                ef_prev = float(e_fast_s.iloc[-(k + 1)])
                es_prev = float(e_slow_s.iloc[-(k + 1)])
                if ef_prev <= es_prev:
                    fresh_cross = True
                    break
        ema_cross_bonus = 8 if fresh_cross else (4 if e_fast > e_slow else 0)

        # ── Momentum (use daily source for intraday) ──────────────────────────
        mom_src = (daily_close if (mode == "Intraday" and daily_close is not None
                                   and len(daily_close) >= 21) else close)
        mom_n = len(mom_src)
        mom1 = (c - float(mom_src.iloc[-21]))  / float(mom_src.iloc[-21])  * 100 if mom_n >= 21  else 0
        mom3 = (c - float(mom_src.iloc[-63]))  / float(mom_src.iloc[-63])  * 100 if mom_n >= 63  else 0
        mom6 = (c - float(mom_src.iloc[-126])) / float(mom_src.iloc[-126]) * 100 if mom_n >= 126 else 0
        strong_htf = mom1 > cfg["mom1_th"] and mom3 > cfg["mom3_th"] and mom6 > cfg["mom6_th"]

        # ── Fibonacci ─────────────────────────────────────────────────────────
        sw_hi, sw_lo, fib, fib_rng = fib_levels(df, lookback=30)
        prox      = atr_val * 0.3
        in_golden = bool(fib and c >= fib["618"] - prox and c <= fib["500"] + prox)
        near_e127 = bool(fib and abs(c - fib["ext127"]) < prox)
        near_e161 = bool(fib and abs(c - fib["ext161"]) < prox)

        # ── VDU (Volume Dry-Up) ───────────────────────────────────────────────
        VDU_VOL_RATIO  = 0.70
        VDU_RANGE_MULT = 0.80
        vdu_vol_dry = False
        vdu_coil    = False
        if n >= 20 and vol_avg > 0:
            recent_vols = [float(volume.iloc[k]) for k in [-3, -2, -1]]
            vdu_vol_dry = all(vv < vol_avg * VDU_VOL_RATIO for vv in recent_vols)
        if n >= 5:
            recent_hi = float(df["High"].iloc[-5:].max())
            recent_lo = float(df["Low"].iloc[-5:].min())
            vdu_coil  = (recent_hi - recent_lo) < atr_val * VDU_RANGE_MULT
        vdu_setup  = bool(trend_up and vdu_vol_dry and vdu_coil)
        qualified  = strong_htf and trend_strong

        # ── Exhaustion ────────────────────────────────────────────────────────
        rsi_series = rsi(close, cfg["rsi_len"])
        ext_flags, ext_penalty, ext_labels, ext_n = detect_exhaustion(
            close=close, high=df["High"], low=df["Low"], volume=volume,
            rsi_series=rsi_series, e_fast_s=e_fast_s, atr_s=atr_s, atr_mean=atr_mean,
            c=c, v=v, vol_avg=vol_avg, mode=mode, vix_val=vix_val,
        )
        r = float(rsi_series.iloc[-1])

        # ── Bull score (FIX-6) ────────────────────────────────────────────────
        bull  = 0
        bull += 25 if trend_up else 0
        bull += ema_cross_bonus          # FIX-6: replaces old "+15 if e_fast > e_slow"
        bull += (15 if r >= 65 else 10) if r >= 60 else (5 if r > 50 else 0)
        bull += 10 if v > vol_avg * 1.2 else (5 if v > vol_avg else 0)
        bull += 15 if c > hh else (9 if c > hh * 0.98 else 0)
        if n >= 3 and c > float(close.iloc[-3]):
            bull += 8
        bull += 7 if rs_rank >= 80 else (3 if rs_rank >= 60 else (0 if rs_rank >= 40 else -3))
        if mode == "Positional":
            bull += 15 if qualified else -15
        else:
            bull += 15 if strong_htf else -10
        bull += 10 if in_golden else 0
        if near_e127:   bull -= 20
        elif near_e161: bull -= 30
        bull += ext_penalty

        BEARISH_HAIRCUT = 0.85
        regime_bearish  = not market_bullish
        if regime_bearish:
            bull = int(bull * BEARISH_HAIRCUT)

        raw_score = max(0, bull)
        norm_bull  = min(100.0, max(0.0, bull * 100.0 / BULL_MAX_V11))
        score_th   = float(cfg["score_th"])

        act           = action_label(norm_bull)
        vol_confirmed = v > vol_avg * 1.2

        # ── Phase detection ───────────────────────────────────────────────────
        phase, entry_price, setup_type = detect_phase_and_entry(
            df, mode, c=c, e_fast_s=e_fast_s, e_slow_s=e_slow_s,
            atr_s=atr_s, atr_val=atr_val, atr_mean=atr_mean,
            v=v, vol_avg=vol_avg, fib=fib, sw_hi=sw_hi, sw_lo=sw_lo,
            in_golden=in_golden, near_e127=near_e127, near_e161=near_e161,
            norm_bull=norm_bull, trend_up=trend_up, trend_down=trend_down,
            trend_strong=trend_strong, score_th=score_th, vdu_setup=vdu_setup,
            htf_up=htf_up, regime_bearish=regime_bearish, vix_val=vix_val,
        )
        phase, _ = ext_phase_override(phase, ext_flags, ext_n, mode)
        act       = ext_action_cap(act, ext_n, vix_val)

        # ── Phase bonus from snapshot (PERF-7: no session_state in thread) ───
        phase_bonus = 0
        if sym and phase_history_snapshot:
            history = phase_history_snapshot.get(sym, [])
            if len(history) >= 3:
                last3 = [h[1] for h in history[-3:]]
                progressions = [
                    [PHASE_SETUP, PHASE_ENTRY, PHASE_CONT],
                    [PHASE_ENTRY, PHASE_CONT, PHASE_BRK],
                    [PHASE_SETUP, PHASE_ENTRY, PHASE_BRK],
                ]
                phase_bonus = 5 if last3 in progressions else 0

        confidence = compute_confidence(
            norm_bull=norm_bull, phase=phase, trend_up=trend_up,
            trend_strong=trend_strong, vol_confirmed=vol_confirmed,
            ema_stack=ema_stack, htf_aligned=htf_up,
            regime_bullish=market_bullish, ext_n=ext_n, vix_val=vix_val,
            phase_bonus=phase_bonus, rs_rank=rs_rank,
        )

        ltp   = round(c, 2)
        entry = entry_price if entry_price else ltp

        # ── Stop-loss ─────────────────────────────────────────────────────────
        mult = cfg["atr_mult"]; wide = cfg["atr_wide"]; closest = cfg["atr_max"]
        if setup_type == "fib" and fib:
            fib_sl = max(float(sw_lo), fib["618"] - atr_val * 0.5)
            fib_sl = max(fib_sl, entry - atr_val * 0.8)
            sl     = round(fib_sl, 2)
        elif setup_type == "breakout":
            sl = round(entry - atr_val * (1.5 if mode == "Intraday" else 2.0), 2)
        else:
            raw_sl      = entry - atr_val * mult
            furthest_sl = entry - atr_val * wide
            closest_sl  = entry - atr_val * closest
            sl = round(max(furthest_sl, min(raw_sl, closest_sl)), 2)

        min_risk = atr_val * 0.5
        if entry - sl < min_risk:
            sl = round(entry - min_risk, 2)

        t1, t2, t3, sl_exp = _compute_targets(
            entry, sl, atr_val, fib, setup_type, sw_hi, sw_lo,
            regime_bearish=regime_bearish, vix_val=vix_val,
        )
        if sl_exp > 1.0:
            sl = round(entry - (entry - sl) * sl_exp, 2)

        return {
            "Score":          round(norm_bull, 1),
            "RawBull":        raw_score,
            "Action":         act,
            "Phase":          phase,
            "Setup":          setup_type,
            "Confidence":     confidence,
            "%Change":        chg,
            "LTP":            ltp,
            "Entry":          entry,
            "SL":             sl,
            "T1":             t1,
            "T2":             t2,
            "T3":             t3,
            "InGolden":       in_golden,
            "VDU":            vdu_setup,
            "AboveEMA50":     above_ema50,
            "AvgTradedCr":    avg_cr,
            "LiquidityOK":    liq_ok,
            "RSI":            round(r, 1),
            "RS":             round(rs_raw, 2),
            "RS_Rank":        rs_rank,
            "ExtN":           ext_n,
            "ExtLabels":      ext_labels,
            "ExtFlags":       ext_flags,
            "HTFUp":          htf_up,
            "EMAStack":       ema_stack,
            "VolConf":        vol_confirmed,
            "FreshCross":     fresh_cross,
            "ATR":            round(atr_val, 2),
            "ATR_Mean":       round(atr_mean, 2),
            "PhaseBonus":     phase_bonus,
            "BreadthGated":   False,         # set by run_scan (FIX-2)
            "_detected_phase": phase,
        }
    except Exception:
        return None
