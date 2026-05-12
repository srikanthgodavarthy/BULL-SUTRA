"""
core/phases.py — Phase detection, transitions, and memory.
"""

import threading
import streamlit as st

# ── Phase constants ────────────────────────────────────────────────────────────
PHASE_IDLE  = "IDLE"
PHASE_SETUP = "SETUP"
PHASE_ENTRY = "ENTRY"
PHASE_CONT  = "CONT"
PHASE_BRK   = "BREAKOUT"
PHASE_EXIT  = "EXIT"

PHASE_COLORS = {
    PHASE_IDLE:  "#555577", PHASE_SETUP: "#b87333",
    PHASE_ENTRY: "#2255cc", PHASE_CONT:  "#22aa55",
    PHASE_BRK:   "#00dd88", PHASE_EXIT:  "#cc4444",
}

PHASE_ORDER = {
    PHASE_IDLE: 0, PHASE_SETUP: 1, PHASE_ENTRY: 2,
    PHASE_CONT: 3, PHASE_BRK:   4, PHASE_EXIT: -1,
}

VIX_CAUTION = 20
VIX_STRESS  = 25

_phase_lock = threading.Lock()

MODE_CFG_REF = None  # set from scoring to avoid circular import


# ── Phase transition memory ────────────────────────────────────────────────────

def record_phase_transition(sym: str, new_phase: str):
    if "phase_history" not in st.session_state:
        st.session_state["phase_history"] = {}
    history = st.session_state["phase_history"]
    if sym not in history:
        history[sym] = []

    from datetime import datetime
    prev_phase = history[sym][-1][1] if history[sym] else None
    changed    = prev_phase != new_phase
    arrow      = ""
    is_prog    = False
    is_regr    = False

    if changed:
        ts = datetime.now().isoformat()
        history[sym].append((ts, new_phase))
        history[sym] = history[sym][-10:]
        if prev_phase is not None:
            prev_ord = PHASE_ORDER.get(prev_phase, 0)
            new_ord  = PHASE_ORDER.get(new_phase, 0)
            if new_phase == PHASE_EXIT:
                arrow = "→EXIT"; is_regr = True
            elif new_ord > prev_ord:
                arrow = f"↗{new_phase}"; is_prog = True
            elif new_ord < prev_ord and new_phase != PHASE_EXIT:
                arrow = f"↘{new_phase}"; is_regr = True

    return changed, arrow, is_prog, is_regr


def phase_transition_conf_bonus(sym: str) -> int:
    history = st.session_state.get("phase_history", {})
    if sym not in history or len(history[sym]) < 3:
        return 0
    last3 = [h[1] for h in history[sym][-3:]]
    progressions = [
        [PHASE_SETUP, PHASE_ENTRY, PHASE_CONT],
        [PHASE_ENTRY, PHASE_CONT, PHASE_BRK],
        [PHASE_SETUP, PHASE_ENTRY, PHASE_BRK],
    ]
    return 5 if last3 in progressions else 0


def get_phase_arrow(sym: str) -> str:
    history = st.session_state.get("phase_history", {})
    if sym not in history or len(history[sym]) < 2:
        return ""
    prev = history[sym][-2][1]
    curr = history[sym][-1][1]
    if curr == PHASE_EXIT:
        return "→EXIT"
    if PHASE_ORDER.get(curr, 0) > PHASE_ORDER.get(prev, 0):
        return "↗"
    if PHASE_ORDER.get(curr, 0) < PHASE_ORDER.get(prev, 0):
        return "↘"
    return ""


# ── Phase + Entry detection  (FIX-3) ─────────────────────────────────────────

def detect_phase_and_entry(df, mode, *, c, e_fast_s, e_slow_s, atr_s,
                            atr_val, atr_mean, v, vol_avg, fib, sw_hi, sw_lo,
                            in_golden, near_e127, near_e161, norm_bull,
                            trend_up, trend_down, trend_strong, score_th,
                            vdu_setup=False, htf_up=True,
                            regime_bearish=False, vix_val=None):
    import numpy as np
    close = df["Close"]
    high  = df["High"]
    n     = len(close)

    if n < 60:
        return PHASE_IDLE, None, "norm"

    e_fast_val = float(e_fast_s.iloc[-1])
    e_slow_val = float(e_slow_s.iloc[-1])

    brk_lb         = 5
    rolling_hi_brk = float(high.iloc[-brk_lb - 1:-1].max()) if n > brk_lb + 1 else float(high.iloc[-1])

    # FIX-3: tighter buffer (0.15 × ATR)
    buf = atr_val * 0.15

    is_compressed = atr_val < atr_mean * 0.8
    is_expanding  = atr_val > float(atr_s.iloc[-2])

    # FIX-3: reject when prior 3-bar range is already expanded
    prior_3bar_atr_expanded = atr_val > atr_mean * 1.4

    body = (abs(float(close.iloc[-1]) - float(df["Open"].iloc[-1]))
            if "Open" in df.columns else atr_val * 0.3)
    upper_wick = (float(high.iloc[-1]) - max(float(close.iloc[-1]), float(df["Open"].iloc[-1]))
                  if "Open" in df.columns else 0)
    is_exhaustion = upper_wick > body * 1.5

    # FIX-3: hard volume gate — breakout candle MUST have vol > 1.5× avg
    brk_vol_ok = (v > vol_avg * 1.5) if vol_avg > 0 else False
    vol_spike  = v > vol_avg * 1.3

    is_fib_buy = trend_up and in_golden

    cont_vol_mult = 1.5 if (regime_bearish or (vix_val and vix_val > VIX_CAUTION)) else 1.2
    BRK_CONF_MIN  = 0.70 if regime_bearish else 0.65

    brk_weights = {
        "price_above_high": (0.30, c > rolling_hi_brk + buf),
        "trend_up":         (0.20, trend_up),
        "score_ok":         (0.15, norm_bull >= score_th),
        "compressed":       (0.15, is_compressed),
        "expanding":        (0.10, is_expanding),
        "vol_spike":        (0.10, vol_spike),
    }
    brk_confidence = sum(w for w, cond in brk_weights.values() if cond)

    is_breakout = (
        brk_confidence >= BRK_CONF_MIN
        and not is_exhaustion
        and brk_vol_ok                   # FIX-3 hard vol gate
        and not prior_3bar_atr_expanded  # FIX-3 anti-blowoff
        and htf_up
    )

    is_cont = (
        n >= 4
        and c > float(close.iloc[-4:-1].max())
        and c > e_fast_val
        and v > vol_avg * cont_vol_mult
        and trend_strong
        and htf_up
    )

    ema_down    = e_fast_val < e_slow_val and float(e_fast_s.iloc[-4]) < float(e_slow_s.iloc[-4])
    trail_level = float(close.iloc[-10:].max()) - atr_val * 1.5
    trail_break = c < trail_level

    # ── Phase assignment ──────────────────────────────────────────────────────
    if trend_down and ema_down:
        phase, setup_type = PHASE_EXIT, "norm"
    elif is_breakout:
        phase, setup_type = PHASE_BRK, "breakout"
    elif (is_fib_buy or norm_bull >= score_th) and is_cont and trend_up:
        phase, setup_type = PHASE_CONT, ("fib" if is_fib_buy else "norm")
    elif (is_fib_buy or norm_bull >= score_th) and trend_up:
        phase, setup_type = PHASE_ENTRY, ("fib" if is_fib_buy else "norm")
    elif (is_fib_buy or norm_bull >= score_th * 0.85 or vdu_setup) and trend_up:
        phase, setup_type = PHASE_SETUP, ("fib" if is_fib_buy else ("vdu" if vdu_setup else "norm"))
    elif trail_break and trend_up:
        phase, setup_type = PHASE_EXIT, "norm"
    else:
        phase, setup_type = PHASE_IDLE, "norm"

    if not htf_up and phase in (PHASE_ENTRY, PHASE_CONT, PHASE_BRK):
        phase, setup_type = PHASE_SETUP, setup_type

    # ── Entry price ───────────────────────────────────────────────────────────
    entry_price = None
    if phase in (PHASE_ENTRY, PHASE_CONT, PHASE_BRK, PHASE_SETUP):
        prox = atr_val * 0.3
        if is_breakout:
            entry_price = round(rolling_hi_brk + buf, 2)
        elif is_fib_buy and fib:
            entry_price = round(fib["618"] + prox * 0.3, 2)
        else:
            cross       = close > e_fast_s
            signal_bars = cross & ~cross.shift(1).fillna(False)
            if signal_bars.any():
                entry_price = round(float(close[signal_bars[::-1].idxmax()]), 2)
            else:
                entry_price = round(c, 2)

    return phase, entry_price, setup_type
