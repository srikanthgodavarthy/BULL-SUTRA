"""
ui/breadth.py — Breadth Engine tab: heatmap, distribution, RS rank buckets.
"""

import streamlit as st

from core.phases import PHASE_BRK
from core.scoring import SECTOR_MAP

VIX_CALM    = 15
VIX_CAUTION = 20
VIX_STRESS  = 25


# ── Breadth computation ────────────────────────────────────────────────────────

def compute_breadth(results: list[dict]) -> dict:
    if not results:
        return {}

    total          = len(results)
    above_ema50    = sum(1 for r in results if r.get("AboveEMA50", False))
    breakout_count = sum(1 for r in results if r.get("Phase") == PHASE_BRK)
    advancing      = sum(1 for r in results if r.get("%Change", 0) > 0)
    declining      = sum(1 for r in results if r.get("%Change", 0) < 0)
    unchanged      = total - advancing - declining

    pct_above_ema50 = round(above_ema50 / total * 100, 1)
    pct_breakout    = round(breakout_count / total * 100, 1)
    ad_ratio        = round(advancing / max(declining, 1), 2)
    pct_advancing   = round(advancing / total * 100, 1)

    sector_scores = {}
    sector_counts = {}
    for r in results:
        sym = r.get("Symbol", "")
        sec = SECTOR_MAP.get(sym, "Other")
        sector_scores[sec] = sector_scores.get(sec, 0) + r.get("Score", 0)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
    sector_avg = {
        sec: round(sector_scores[sec] / sector_counts[sec], 1)
        for sec in sector_scores
    }

    liquid_count = sum(1 for r in results if r.get("LiquidityOK", True))

    return {
        "total":           total,
        "above_ema50":     above_ema50,
        "pct_above_ema50": pct_above_ema50,
        "breakout_count":  breakout_count,
        "pct_breakout":    pct_breakout,
        "advancing":       advancing,
        "declining":       declining,
        "unchanged":       unchanged,
        "ad_ratio":        ad_ratio,
        "pct_advancing":   pct_advancing,
        "sector_avg":      sector_avg,
        "liquid_count":    liquid_count,
        "breadth_signal":  _breadth_signal(pct_above_ema50, ad_ratio, pct_breakout),
    }


def _breadth_signal(pct_ema50, ad_ratio, pct_brk) -> tuple[str, str]:
    score = 0
    if pct_ema50 >= 70:   score += 2
    elif pct_ema50 >= 50: score += 1
    if ad_ratio >= 2.0:   score += 2
    elif ad_ratio >= 1.2: score += 1
    if pct_brk >= 5:      score += 1
    if score >= 4: return "STRONG",  "#2ecc71"
    if score >= 2: return "NEUTRAL", "#f39c12"
    return "WEAK", "#e74c3c"


# ── Breadth tab renderer ───────────────────────────────────────────────────────

def render_breadth_tab(all_results: list[dict], vix_val: float | None) -> None:
    if not all_results:
        st.info("Run a scan first to see breadth data.")
        return

    breadth      = compute_breadth(all_results)
    b_sig, b_col = breadth["breadth_signal"]

    st.markdown(
        f'<div style="background:{b_col}11;border:1px solid {b_col}33;border-radius:8px;'
        f'padding:10px 16px;margin-bottom:14px;">'
        f'<span style="font-family:Syne,sans-serif;font-size:15px;color:{b_col};">'
        f'Market Breadth: <strong>{b_sig}</strong></span></div>',
        unsafe_allow_html=True,
    )

    bm1, bm2, bm3, bm4, bm5, bm6 = st.columns(6)
    bm1.metric("% Above EMA50", f'{breadth["pct_above_ema50"]}%')
    bm2.metric("% in BREAKOUT", f'{breadth["pct_breakout"]}%')
    bm3.metric("Advancing",     breadth["advancing"])
    bm4.metric("Declining",     breadth["declining"])
    bm5.metric("A/D Ratio",     breadth["ad_ratio"])
    bm6.metric("Liquid Stocks", breadth["liquid_count"])

    # FIX-2: gated count
    gated_n = sum(1 for r in all_results if r.get("BreadthGated"))
    if gated_n:
        st.warning(
            f"🔵 **Breadth Gate** — {gated_n} BREAKOUT/CONT signals were capped to WATCH "
            f"(pct_above_ema50={breadth['pct_above_ema50']}%, A/D={breadth['ad_ratio']})"
        )

    _render_interpretation(breadth, vix_val)
    st.markdown("---")
    _render_sector_heatmap(breadth["sector_avg"], all_results)
    st.markdown("---")
    _render_advance_decline(breadth)
    st.markdown("---")
    _render_rs_buckets(all_results)


def _render_interpretation(breadth: dict, vix_val) -> None:
    pct_ema = breadth["pct_above_ema50"]
    adr     = breadth["ad_ratio"]
    brk_pct = breadth["pct_breakout"]

    lines = []
    if pct_ema >= 70:
        lines.append("✅ **Strong internal trend** — 70%+ above EMA50.")
    elif pct_ema >= 50:
        lines.append("🟡 **Mixed breadth** — about half the market participating. Be selective.")
    else:
        lines.append("🔴 **Weak breadth** — majority below EMA50. Avoid chasing.")

    if adr >= 2.0:
        lines.append("✅ **A/D ratio strong** — broad advancing participation.")
    elif adr < 0.8:
        lines.append("🔴 **Declining dominance** — wait for A/D recovery before new longs.")

    if brk_pct >= 5:
        lines.append(f"✅ **Breakout breadth healthy** ({brk_pct}%).")
    elif brk_pct < 1:
        lines.append("🔴 **No breakout breadth** — avoid momentum until breadth improves.")

    if vix_val:
        if vix_val >= VIX_STRESS:
            lines.append(f"🔴 **VIX {vix_val} STRESS** — STRONG BUY blocked. Targets compressed.")
        elif vix_val >= VIX_CAUTION:
            lines.append(f"🟡 **VIX {vix_val} CAUTION** — Targets compressed 25%, SL widened.")
        else:
            lines.append(f"✅ **VIX {vix_val} CALM** — Normal risk parameters.")

    st.markdown("\n\n".join(lines))


def _render_sector_heatmap(sector_avg: dict, all_results: list[dict]) -> None:
    st.subheader("Sector Heatmap")
    if not sector_avg:
        return

    sec_rows = sorted(sector_avg.items(), key=lambda x: -x[1])
    hm_html  = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px;">'
    for sec, score in sec_rows:
        count   = sum(1 for r in all_results if r.get("Sector") == sec)
        bar_col = "#22c55e" if score >= 70 else ("#d97706" if score >= 55 else "#ef4444")
        pct     = min(100, score)
        hm_html += (
            f'<div style="background:#111120;border:1px solid #1e1e40;'
            f'border-radius:7px;padding:10px 12px;">'
            f'<div style="color:#e8e8f4;font-size:11px;font-weight:600;'
            f'font-family:DM Sans,sans-serif;">{sec}</div>'
            f'<div style="color:#6b7090;font-size:10px;'
            f'font-family:JetBrains Mono,monospace;">{count} stocks</div>'
            f'<div style="background:#1e1e40;border-radius:2px;height:4px;margin:6px 0;">'
            f'<div style="background:{bar_col};width:{pct}%;height:4px;border-radius:2px;"></div></div>'
            f'<div style="color:{bar_col};font-size:15px;font-weight:600;'
            f'font-family:JetBrains Mono,monospace;">{score}</div>'
            f'</div>'
        )
    hm_html += "</div>"
    st.markdown(hm_html, unsafe_allow_html=True)


def _render_advance_decline(breadth: dict) -> None:
    dist_data   = {
        "Advancing": breadth["advancing"],
        "Unchanged": breadth["unchanged"],
        "Declining": breadth["declining"],
    }
    dist_colors = {"Advancing": "#22c55e", "Unchanged": "#d97706", "Declining": "#ef4444"}
    total_shown = sum(dist_data.values())

    html = '<div style="display:flex;gap:8px;">'
    for label, count in dist_data.items():
        pct2 = round(count / total_shown * 100, 1) if total_shown else 0
        col  = dist_colors[label]
        html += (
            f'<div style="flex:1;background:#111120;border:1px solid {col}33;'
            f'border-radius:7px;padding:12px;text-align:center;">'
            f'<div style="color:{col};font-size:22px;font-weight:600;'
            f'font-family:JetBrains Mono,monospace;">{count}</div>'
            f'<div style="color:#6b7090;font-size:11px;font-family:DM Sans,sans-serif;">{label}</div>'
            f'<div style="color:{col};font-size:11px;font-family:JetBrains Mono,monospace;">{pct2}%</div>'
            f'</div>'
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def _render_rs_buckets(all_results: list[dict]) -> None:
    st.subheader("RS Rank Distribution")
    buckets = {
        "Top 80-100": 0, "Upper 60-79": 0, "Mid 40-59": 0,
        "Lower 20-39": 0, "Bottom 0-19": 0,
    }
    for r in all_results:
        rk = r.get("RS_Rank", 50)
        if rk >= 80:   buckets["Top 80-100"]  += 1
        elif rk >= 60: buckets["Upper 60-79"] += 1
        elif rk >= 40: buckets["Mid 40-59"]   += 1
        elif rk >= 20: buckets["Lower 20-39"] += 1
        else:           buckets["Bottom 0-19"] += 1

    cols = st.columns(5)
    for col, (label, cnt) in zip(cols, buckets.items()):
        col.metric(label, cnt)
