"""
ui/detail.py — Detail tab: per-stock drill-down with position sizing,
confidence model breakdown, and exhaustion signal explanations.
"""

import streamlit as st

from core.phases import (
    PHASE_COLORS, PHASE_ORDER,
    PHASE_IDLE, PHASE_SETUP, PHASE_ENTRY, PHASE_CONT, PHASE_BRK, PHASE_EXIT,
)
from core.confidence import confidence_label
from core.exhaustion import EXT_PENALTIES
from core.scoring import fmt


# ── Position sizing (FIX-5) ───────────────────────────────────────────────────

def position_size(account_size: float, entry: float, sl: float,
                  atr_val: float, atr_mean: float, vix_val: float,
                  risk_pct: float = 0.02, max_capital_pct: float = 0.20) -> dict:
    """
    FIX-5: final_qty is clamped so capital_used ≤ account_size × max_capital_pct.
    """
    import numpy as np
    risk_per_share = max(entry - sl, 0.01)
    base_qty       = int((account_size * risk_pct) / risk_per_share)

    vix_adj = float(np.clip(20.0 / vix_val, 0.5, 1.5)) if (vix_val and vix_val > 0) else 1.0
    atr_adj = float(np.clip(atr_mean / atr_val, 0.6, 1.4)) if atr_mean > 0 else 1.0

    vol_adj_qty = max(1, int(base_qty * vix_adj * atr_adj))

    # FIX-5: capital cap
    max_qty_by_capital = max(1, int((account_size * max_capital_pct) / entry))
    final_qty          = min(vol_adj_qty, max_qty_by_capital)

    return {
        "base_qty":        base_qty,
        "vix_adj":         round(vix_adj, 2),
        "atr_adj":         round(atr_adj, 2),
        "vol_adj_qty":     vol_adj_qty,
        "final_qty":       final_qty,
        "capital_used":    round(final_qty * entry, 2),
        "max_loss":        round(final_qty * risk_per_share, 2),
        "risk_pct":        risk_pct,
        "max_capital_pct": max_capital_pct,
        "capital_capped":  final_qty < vol_adj_qty,
    }


# ── Detail tab renderer ────────────────────────────────────────────────────────

def render_detail_tab(all_results: list[dict], vix_val: float | None) -> None:
    if not all_results:
        st.info("Run a scan first.")
        return

    sel = st.selectbox("Select stock", [r["Symbol"] for r in all_results])
    r   = next((x for x in all_results if x["Symbol"] == sel), None)
    if not r:
        return

    phase = r.get("Phase", PHASE_IDLE)
    chg   = r["%Change"]
    conf  = r.get("Confidence", 0)
    _, conf_col = confidence_label(conf)

    _render_phase_bar(phase, sel)

    if r.get("BreadthGated"):
        st.warning("🔵 **Breadth Gated** — action capped to WATCH due to weak market breadth.")

    _render_phase_history(sel)
    _render_metrics_row(r, chg, conf)
    st.markdown("---")
    _render_position_sizing(r, vix_val)
    _render_confidence_model(r, phase)
    _render_exhaustion_detail(r)
    _render_info_row(r)

    if r["Entry"] != r["LTP"]:
        st.info(
            f"⚡ Entry ₹{r['Entry']:,} is the trigger price. "
            f"LTP = ₹{r['LTP']:,}. Place order near Entry when phase = ENTRY/BREAKOUT."
        )


def _render_phase_bar(phase: str, sym: str) -> None:
    phases_order = [PHASE_IDLE, PHASE_SETUP, PHASE_ENTRY, PHASE_CONT, PHASE_BRK, PHASE_EXIT]
    ph_txt_map   = {
        "#00dd88": "#064e3b", "#22aa55": "#064e3b",
        "#2255cc": "#dbeafe", "#b87333": "#431407",
        "#555577": "#c4c6d0", "#cc4444": "#fee2e2",
    }

    html = '<div style="display:flex;gap:5px;margin-bottom:12px;flex-wrap:wrap;">'
    for ph in phases_order:
        active  = ph == phase
        bg      = PHASE_COLORS[ph] if active else "#1e1e40"
        brd     = f"1px solid {PHASE_COLORS[ph]}" if active else "1px solid #1e1e40"
        ph_txt  = ph_txt_map.get(PHASE_COLORS[ph], "#e8e8f4") if active else "#6b7090"
        weight  = "600" if active else "400"
        marker  = "  ◀" if active else ""
        html += (
            f'<div style="background:{bg};border:{brd};color:{ph_txt};'
            f'padding:4px 12px;border-radius:5px;font-size:11px;'
            f'font-weight:{weight};font-family:DM Sans,sans-serif;">'
            f'{ph}{marker}</div>'
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def _render_phase_history(sym: str) -> None:
    history = st.session_state.get("phase_history", {}).get(sym, [])
    if len(history) < 2:
        return
    transitions = []
    for j in range(1, len(history)):
        prev_ts, prev_ph = history[j - 1]
        curr_ts, curr_ph = history[j]
        arrow = "↗" if PHASE_ORDER.get(curr_ph, 0) > PHASE_ORDER.get(prev_ph, 0) else "↘"
        transitions.append(
            f'{prev_ph} {arrow} {curr_ph}'
            f'  <span style="color:#3a3a60;font-size:10px;">({curr_ts[:16]})</span>'
        )
    st.markdown(
        '<details><summary style="color:#6b7090;font-size:11px;cursor:pointer;">'
        f'Phase History ({len(history)} states)</summary>'
        '<div style="font-size:11px;color:#6b7090;padding:6px 0;'
        'font-family:JetBrains Mono,monospace;">'
        + "<br>".join(transitions) + "</div></details>",
        unsafe_allow_html=True,
    )


def _render_metrics_row(r: dict, chg: float, conf: float) -> None:
    conf_lbl, _ = confidence_label(conf)
    d1, d2, d3, d4, d5 = st.columns(5)
    d1.metric("LTP",        fmt(r["LTP"]),   f"{'+' if chg >= 0 else ''}{chg}%")
    d2.metric("Entry ⚡",   fmt(r["Entry"]))
    d3.metric("Stop Loss",  fmt(r["SL"]))
    d4.metric("Score",      r["Score"])
    d5.metric("Confidence", f"{conf}% ({conf_lbl})")

    t1c, t2c, t3c, r1c = st.columns(4)
    t1c.metric("T1", fmt(r["T1"]))
    t2c.metric("T2", fmt(r["T2"]))
    t3c.metric("T3", fmt(r["T3"]))
    risk = round(r["Entry"] - r["SL"], 2) if r.get("Entry") and r.get("SL") else 0
    r1c.metric("Risk/Share", fmt(risk))


def _render_position_sizing(r: dict, vix_val) -> None:
    with st.expander("Position Sizing (Volatility-Normalized + Capital Cap)", expanded=True):
        acct     = st.session_state.get("account_size",    500_000)
        risk_pct = st.session_state.get("risk_pct",        0.02)
        max_cap  = st.session_state.get("max_capital_pct", 0.20)
        risk     = round(r["Entry"] - r["SL"], 2) if r.get("Entry") and r.get("SL") else 0

        ps = position_size(
            account_size    = acct,
            entry           = r["Entry"],
            sl              = r["SL"],
            atr_val         = r.get("ATR", risk),
            atr_mean        = r.get("ATR_Mean", risk),
            vix_val         = vix_val,
            risk_pct        = risk_pct,
            max_capital_pct = max_cap,
        )
        ps1, ps2, ps3, ps4 = st.columns(4)
        ps1.metric("Suggested Qty", ps["final_qty"])
        ps2.metric("Capital Used",  fmt(ps["capital_used"]))
        ps3.metric("Max Loss",      fmt(ps["max_loss"]))
        ps4.metric("Risk/Share",    fmt(risk))

        cap_note = (
            f'  ⚠ <span style="color:#f59e0b;">Capital capped</span>'
            f' (pre-cap qty: {ps["vol_adj_qty"]})'
            if ps.get("capital_capped") else ""
        )
        st.markdown(
            f'<div style="background:#111120;border:1px solid #1e1e40;border-radius:6px;'
            f'padding:8px 12px;margin-top:8px;font-size:11px;'
            f'font-family:JetBrains Mono,monospace;color:#6b7090;">'
            f'Base: <span style="color:#e8e8f4;">{ps["base_qty"]}</span>  ×  '
            f'VIX adj <span style="color:#f59e0b;">{ps["vix_adj"]}×</span>  ×  '
            f'ATR adj <span style="color:#f59e0b;">{ps["atr_adj"]}×</span>  =  '
            f'<span style="color:#22c55e;font-weight:600;">{ps["final_qty"]} shares</span>'
            f'{cap_note}</div>',
            unsafe_allow_html=True,
        )


def _render_confidence_model(r: dict, phase: str) -> None:
    conf = r.get("Confidence", 0)
    conf_lbl, _ = confidence_label(conf)
    with st.expander(f"Confidence Model — {conf}% ({conf_lbl})", expanded=False):
        factors = {
            "Phase alignment": {
                PHASE_BRK:   20, PHASE_CONT:  17, PHASE_ENTRY: 13,
                PHASE_SETUP: 7,  PHASE_IDLE:  2,  PHASE_EXIT:  0,
            }.get(phase, 0),
            "Score quality":   round(min(20, r["Score"] * 0.20), 1),
            "Volume confirmed": 15 if r.get("VolConf") else 5,
            "EMA stack":       8 if r.get("EMAStack") else 3,
            "HTF alignment":   7 if r.get("HTFUp", True) else 0,
            "Market regime":   10 if r.get("Regime") == "BULLISH" else 2,
            "Exhaustion drag": -min(5, r.get("ExtN", 0) * 2),
            "RS rank bonus": (
                10 if r.get("RS_Rank", 50) >= 90 else
                7  if r.get("RS_Rank", 50) >= 80 else
                3  if r.get("RS_Rank", 50) >= 70 else 0
            ),
            "Phase progression": r.get("PhaseBonus", 0),
        }
        for fname, fval in factors.items():
            col_f = (
                "#22c55e" if fval >= 10 else
                "#f59e0b" if fval >= 5  else
                "#ef4444" if fval < 0   else "#6b7090"
            )
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:4px 0;border-bottom:1px solid #1e1e40;">'
                f'<span style="color:#6b7090;font-size:12px;'
                f'font-family:DM Sans,sans-serif;">{fname}</span>'
                f'<span style="color:{col_f};font-size:12px;font-weight:600;'
                f'font-family:JetBrains Mono,monospace;">{fval:+.0f}</span></div>',
                unsafe_allow_html=True,
            )


def _render_exhaustion_detail(r: dict) -> None:
    ext_n      = r.get("ExtN", 0)
    ext_flags  = r.get("ExtFlags", {})

    if ext_n == 0:
        st.success("✅ No extension/exhaustion signals — structure is clean.")
        return

    flag_desc = {
        "rsi_overheat":     "Stock has run up too fast — buyers are exhausted. Wait for a cooldown.",
        "atr_extension":    "Today's range is unusually large — possible blow-off.",
        "parabolic":        "Price jumped far more than normal in 3 bars. Hard to sustain.",
        "ema_distance":     "Price is stretched way above its average. Pullback likely.",
        "climactic_volume": "Huge volume spike with long upper wick — potential distribution.",
        "mom_exhaustion":   "Price rising but buying pressure quietly weakening.",
        "bearish_div":      "New high, but momentum didn't confirm it.",
    }

    with st.expander(
        f"⚠ {ext_n} Caution Signal{'s' if ext_n > 1 else ''} — "
        f"{'DO NOT enter' if ext_n >= 3 else 'Reduce size'}",
        expanded=True,
    ):
        for fk, fa in ext_flags.items():
            if fa:
                ec = "#ef4444" if ext_n >= 3 else "#f59e0b"
                st.markdown(
                    f'<div style="color:{ec};font-size:12px;padding:3px 0;">'
                    f'▸ <strong>{fk.replace("_"," ").title()}</strong> — '
                    f'{flag_desc.get(fk, "")}</div>',
                    unsafe_allow_html=True,
                )

        penalty = sum(EXT_PENALTIES[k] for k, v in ext_flags.items() if v)
        st.markdown(
            f'<div style="margin-top:8px;padding:6px 10px;background:#7f1d1d22;'
            f'border:1px solid #7f1d1d;border-radius:5px;font-size:12px;color:#fca5a5;">'
            f'Score reduced by {abs(penalty)} pts — '
            + ("Skip. Wait for pullback + RSI < 60." if ext_n >= 3
               else "Half size. Wait for support/EMA dip.")
            + "</div>",
            unsafe_allow_html=True,
        )


def _render_info_row(r: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("RSI",         r.get("RSI", "—"))
    c2.metric("RS Rank",     f'{r.get("RS_Rank", 50)}/100')
    c3.metric("Liq (₹Cr/d)", r.get("AvgTradedCr", "—"))
    c4.metric("Raw RS Diff", f"{r.get('RS', 0):+.1f}%")
