"""
ui/table.py — Main scanner results DataFrame with conditional styling.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from core.phases import get_phase_arrow
from core.scoring import fmt


def build_display_rows(results: list[dict]) -> list[dict]:
    rows = []
    for i, r in enumerate(results):
        chg      = r["%Change"]
        phase    = r.get("Phase", "IDLE")
        ph_arrow = get_phase_arrow(r["Symbol"])
        setup_icon = {"fib": "Fib", "breakout": "BRK",
                      "norm": "std", "vdu": "VDU"}.get(r.get("Setup", "norm"), "std")
        rows.append({
            "#":       i + 1,
            "Symbol":  r["Symbol"],
            "Score":   r["Score"],
            "Conf%":   r.get("Confidence", 0),
            "Phase":   f'{phase}{" " + ph_arrow if ph_arrow else ""}',
            "Setup":   setup_icon,
            "Action":  r["Action"],
            "B-Gate":  "⚠" if r.get("BreadthGated") else "",
            "%Chg":    f"+{chg}%" if chg >= 0 else f"{chg}%",
            "RSI":     r.get("RSI", "—"),
            "RS_Rank": r.get("RS_Rank", 50),
            "LTP":     fmt(r["LTP"]),
            "Entry":   fmt(r["Entry"]) + (" ⚡" if r["Entry"] != r["LTP"] else ""),
            "SL":      fmt(r["SL"]),
            "T1":      fmt(r["T1"]),
            "T2":      fmt(r["T2"]),
            "T3":      fmt(r["T3"]),
            "Liq₹Cr":  r.get("AvgTradedCr", "—"),
            "HTF":     "↑" if r.get("HTFUp", True) else "↓",
            "ExtN":    r.get("ExtN", 0),
            "Ext":     " ".join(r.get("ExtLabels", [])) or "—",
        })
    return rows


def _color_extn(val) -> str:
    if val == 0: return "background-color: transparent; color: #6b7090"
    if val == 1: return "background-color: #78350f44; color: #f59e0b"
    if val == 2: return "background-color: #9a3412aa; color: #fb923c"
    return "background-color: #7f1d1d; color: #fca5a5; font-weight: 600"


def _color_action(val) -> str:
    if val == "STRONG BUY": return "color: #f59e0b; font-weight: 600"
    if val == "BUY":        return "color: #22c55e"
    if val == "WATCH":      return "color: #d97706"
    return "color: #6b7090"


def _color_pct(val) -> str:
    if isinstance(val, str) and val.startswith("+"):
        return "color: #22c55e; font-family: JetBrains Mono, monospace"
    if isinstance(val, str) and val.startswith("-"):
        return "color: #ef4444; font-family: JetBrains Mono, monospace"
    return ""


def render_results_table(results: list[dict]) -> None:
    rows = build_display_rows(results)
    df   = pd.DataFrame(rows)

    styled = (
        df.style
        .map(_color_extn,   subset=["ExtN"])
        .map(_color_action, subset=["Action"])
        .map(_color_pct,    subset=["%Chg"])
        .set_properties(**{
            "font-family": "JetBrains Mono, monospace",
            "font-size":   "11px",
        })
    )
    st.dataframe(styled, use_container_width=True, hide_index=True, height=480)
    st.markdown(
        '<div style="font-size:10px;color:#3a3a60;font-family:JetBrains Mono,monospace;'
        'margin-top:4px;">Score 0-100 · Conf% = confidence · RS_Rank = 52w percentile '
        '(80+=top) · HTF ↑/↓ = weekly · Liq₹Cr = avg daily value · '
        'ExtN 0=clean 3+=skip · B-Gate = breadth gated</div>',
        unsafe_allow_html=True,
    )


def render_export_button(results: list[dict], mode: str) -> None:
    buy_rows = [r for r in results if r["Action"] in ("BUY", "STRONG BUY")]
    if not buy_rows:
        return
    csv = (
        pd.DataFrame(buy_rows)
        .drop(columns=["ExtFlags"], errors="ignore")
        .to_csv(index=False)
    )
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.download_button(
        "Export BUY results", csv,
        f"NSE_Scan_{mode}_{ts}.csv", "text/csv",
    )
