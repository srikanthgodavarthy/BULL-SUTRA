"""
core/confidence.py — Confidence model and label helpers.
"""

from core.phases import (
    PHASE_BRK, PHASE_CONT, PHASE_ENTRY,
    PHASE_SETUP, PHASE_IDLE, PHASE_EXIT,
)

VIX_CAUTION = 20


def compute_confidence(*, norm_bull, phase, trend_up, trend_strong,
                       vol_confirmed, ema_stack, htf_aligned,
                       regime_bullish, ext_n, vix_val,
                       phase_bonus=0, rs_rank=50) -> float:
    c  = 0.0
    c += {
        PHASE_BRK:   20, PHASE_CONT:  17, PHASE_ENTRY: 13,
        PHASE_SETUP: 7,  PHASE_IDLE:  2,  PHASE_EXIT:  0,
    }.get(phase, 0)
    c += min(20, norm_bull * 0.20)
    c += 15 if vol_confirmed else 5
    c += 15 if ema_stack else (7 if trend_strong else 0)
    c += 15 if htf_aligned else 0
    c += 10 if regime_bullish else 2
    c -= min(5, ext_n * 2)
    if vix_val is not None and vix_val > VIX_CAUTION:
        c -= 5
    if rs_rank >= 90:   c += 5
    elif rs_rank >= 80: c += 3
    elif rs_rank <= 20: c -= 3
    c += phase_bonus
    return round(min(100, max(0, c)), 1)


def confidence_label(conf: float) -> tuple[str, str]:
    if conf >= 80: return "HIGH", "#2ecc71"
    if conf >= 60: return "MED",  "#f39c12"
    if conf >= 40: return "LOW",  "#e67e22"
    return "WEAK", "#e74c3c"
