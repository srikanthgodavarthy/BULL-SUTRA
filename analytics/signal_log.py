"""
analytics/signal_log.py — Signal logging, staleness checks, and log tab rendering.
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

MODE_VALIDITY = {
    "Intraday":   4,
    "Swing":      72,
    "Positional": 240,
}


# ── Staleness helpers ──────────────────────────────────────────────────────────

def signal_is_stale(logged_at_iso: str, mode: str) -> bool:
    try:
        validity_h = MODE_VALIDITY.get(mode, 72)
        logged_at  = datetime.fromisoformat(logged_at_iso)
        return (datetime.now() - logged_at) > timedelta(hours=validity_h)
    except Exception:
        return False


def signal_age_label(logged_at_iso: str, mode: str) -> tuple[str, bool]:
    try:
        validity_h = MODE_VALIDITY.get(mode, 72)
        logged_at  = datetime.fromisoformat(logged_at_iso)
        delta      = datetime.now() - logged_at
        hours      = delta.total_seconds() / 3600
        stale      = hours > validity_h
        if hours < 1:
            age_str = f"{int(delta.total_seconds() / 60)}m ago"
        elif hours < 24:
            age_str = f"{hours:.1f}h ago"
        else:
            age_str = f"{hours / 24:.1f}d ago"
        return age_str, stale
    except Exception:
        return "unknown", False


# ── Log a scan's BUY/STRONG BUY signals ───────────────────────────────────────

def log_scan_signals(results: list[dict], mode: str) -> None:
    ts = datetime.now().isoformat()
    validity_h = MODE_VALIDITY.get(mode, 72)
    for r in results:
        if r.get("Action") in ("BUY", "STRONG BUY"):
            st.session_state.signal_log.append({
                "timestamp":     ts,
                "symbol":        r["Symbol"],
                "action":        r["Action"],
                "phase":         r.get("Phase"),
                "score":         r["Score"],
                "confidence":    r.get("Confidence", 0),
                "rs_rank":       r.get("RS_Rank", 50),
                "entry":         r.get("Entry"),
                "sl":            r.get("SL"),
                "t1":            r.get("T1"),
                "ltp_at_signal": r.get("LTP"),
                "mode":          mode,
                "validity_hours": validity_h,
                "outcome":       "Pending",
                "breadth_gated": r.get("BreadthGated", False),
            })
