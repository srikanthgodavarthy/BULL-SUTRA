"""
core/targets.py — Target price computation and VIX-based multipliers.
"""

VIX_CAUTION = 20
VIX_STRESS  = 25


def vix_target_mult(vix_val):
    """Returns (t1_mult, t2_mult, t3_mult, sl_expansion)."""
    if vix_val is None or vix_val < VIX_CAUTION:
        return 1.0, 2.0, 3.0, 1.0
    if vix_val < VIX_STRESS:
        return 0.75, 1.4, 2.0, 1.2
    return 0.6, 1.1, 1.6, 1.35


def _compute_targets(entry, sl, atr_val, fib, setup_type,
                     sw_hi, sw_lo, regime_bearish=False, vix_val=None):
    rk = max(entry - sl, atr_val * 0.5)
    t1m, t2m, t3m, sl_exp = vix_target_mult(vix_val)

    if regime_bearish:
        t1m *= 0.8; t2m *= 0.7; t3m *= 0.6

    if setup_type == "fib" and fib:
        t1    = round(fib["ext127"], 2)
        t2    = round(fib["ext161"], 2)
        ext_r = fib["ext161"] - fib["ext127"]
        t3    = round(fib["ext161"] + min(ext_r, atr_val * 3), 2)
    elif setup_type == "breakout" and fib:
        t1 = round((entry + rk * t1m + fib["ext127"]) / 2, 2)
        t2 = round((entry + rk * t2m + fib["ext161"]) / 2, 2)
        t3 = round((entry + rk * t3m + fib["ext261"]) / 2, 2)
    else:
        t1 = round(entry + rk * t1m, 2)
        t2 = round(entry + rk * t2m, 2)
        t3 = round(entry + rk * t3m, 2)

    min_move = atr_val * 0.8
    if t1 - entry < min_move:
        t1 = round(entry + min_move, 2)
        t2 = round(entry + min_move * 2, 2)
        t3 = round(entry + min_move * 3, 2)

    return t1, t2, t3, sl_exp
