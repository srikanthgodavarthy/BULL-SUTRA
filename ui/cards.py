"""
ui/cards.py — Stock result cards (Ready-to-Trade and Watchlist).
"""

import streamlit as st

from core.phases import PHASE_COLORS, get_phase_arrow
from core.confidence import confidence_label
from core.scoring import SECTOR_MAP
from ui.styles import action_colors


def _phase_text_color(hex_color: str) -> str:
    _map = {
        "#00dd88": "#064e3b", "#22aa55": "#064e3b",
        "#2255cc": "#dbeafe", "#b87333": "#431407",
        "#555577": "#c4c6d0", "#cc4444": "#fee2e2",
    }
    return _map.get(hex_color, "#e8e8f4")


def make_card(i: int, r: dict, border_color: str,
              show_entry: bool = True, is_stale: bool = False) -> str:
    chg = r["%Change"]
    cs  = f"+{chg}%" if chg >= 0 else f"{chg}%"
    cc  = "#22c55e" if chg >= 0 else "#ef4444"
    arr = "▲" if chg >= 0 else "▼"

    act                    = r["Action"]
    act_bg, act_brd, act_txt = action_colors(act)

    ph     = r.get("Phase", "IDLE")
    pc     = PHASE_COLORS.get(ph, "#555")
    ph_txt = _phase_text_color(pc)

    conf              = r.get("Confidence", 0)
    conf_lbl, conf_col = confidence_label(conf)

    rsr    = r.get("RS_Rank", 50)
    rs_col = "#22c55e" if rsr >= 80 else ("#d97706" if rsr >= 60 else "#6b7090")

    entry_str = (f'₹{r["Entry"]:,.2f}'
                 if show_entry and r["Entry"] != r["LTP"] else "")

    ext_n      = r.get("ExtN", 0)
    ext_labels = r.get("ExtLabels", [])
    ph_arrow   = get_phase_arrow(r["Symbol"])
    sector     = r.get("Sector", SECTOR_MAP.get(r["Symbol"], "—"))

    vol_conf    = r.get("VolConf", False)
    vol_label   = "High" if vol_conf else "Above Avg"
    htf_up      = r.get("HTFUp", True)
    trend_label = "↑ Bullish" if htf_up else "↓ Bearish"
    trend_col   = "#22c55e" if htf_up else "#ef4444"
    rsi_val     = r.get("RSI", "—")

    num_bg  = "#22c55e" if act in ("BUY", "STRONG BUY") else "#d97706" if act == "WATCH" else "#3a3a60"
    num_txt = "#064e3b" if act in ("BUY", "STRONG BUY") else "#431407" if act == "WATCH" else "#c4c6d0"

    phase_icon = {"BREAKOUT": "📈", "CONT": "🔄", "ENTRY": "⚡",
                  "SETUP": "🔍", "IDLE": "💤", "EXIT": "🚪"}.get(ph, "")

    # ── Exhaustion badges ──────────────────────────────────────────────────────
    ext_html = ""
    for lbl in ext_labels[:2]:
        ec_bg  = "#3b1a0a" if ext_n >= 3 else "#2a1e00"
        ec_brd = "#ef444466" if ext_n >= 3 else "#f59e0b66"
        ec_txt = "#fca5a5" if ext_n >= 3 else "#fbbf24"
        ext_html += (
            f'<div style="margin-top:6px;background:{ec_bg};border:1px solid {ec_brd};'
            f'border-radius:5px;padding:5px 10px;font-size:11px;color:{ec_txt};'
            f'font-family:DM Sans,sans-serif;display:flex;align-items:center;gap:6px;">'
            f'⚠ {lbl}</div>'
        )

    # ── Inline badges ──────────────────────────────────────────────────────────
    breadth_badge = (
        '<span style="background:#1e2a40;border:1px solid #3b5998;color:#93b4ff;'
        'padding:1px 5px;border-radius:3px;font-size:9px;margin-left:4px;">B-GATE</span>'
        if r.get("BreadthGated") else ""
    )
    golden_badge = (
        '<span style="background:#f59e0b22;border:1px solid #f59e0b55;color:#f59e0b;'
        'padding:1px 5px;border-radius:3px;font-size:9px;margin-left:4px;">GOLDEN</span>'
        if r.get("InGolden") else ""
    )
    stale_html = (
        '<span style="color:#6b7090;font-size:10px;margin-left:6px;">⏱ stale</span>'
        if is_stale else ""
    )

    ltp_str   = f'&#8377;{r["LTP"]:,.2f}'
    entry_div = (
        f'<div style="font-family:JetBrains Mono,monospace;color:#f59e0b;'
        f'font-size:12px;margin-top:5px;">&#9889; {entry_str}</div>'
        if entry_str else ""
    )

    ph_txt_full = f'{phase_icon} {ph}' + (f' {ph_arrow}' if ph_arrow else '')

    conf_badge = (
        f'<span style="background:#1e1e40;border:1px solid {conf_col}55;'
        f'padding:6px 10px;border-radius:6px;font-size:10px;font-weight:600;'
        f'font-family:DM Sans,sans-serif;">'
        f'<span style="color:#6b7090;font-size:9px;display:block;">{conf_lbl}</span>'
        f'<span style="color:{conf_col};font-weight:700;">{conf}%</span></span>'
    )

    # ── Assemble card HTML ─────────────────────────────────────────────────────
    parts = [
        f'<div style="background:#111120;border:1px solid {border_color};'
        f'border-radius:12px;overflow:hidden;width:360px;min-width:320px;'
        f'max-width:380px;flex:1 1 360px;">',

        # Header row
        f'<div style="display:flex;align-items:center;padding:12px 16px 10px;'
        f'border-bottom:1px solid #1e1e40;gap:10px;">',
        f'<div style="background:{num_bg};color:{num_txt};font-family:JetBrains Mono,'
        f'monospace;font-size:12px;font-weight:700;padding:4px 8px;border-radius:6px;'
        f'min-width:32px;text-align:center;">{i+1:02d}</div>',
        f'<div style="font-family:Syne,sans-serif;color:#e8e8f4;font-size:16px;'
        f'font-weight:700;letter-spacing:-0.3px;flex:1;">'
        f'{r["Symbol"]}{golden_badge}{breadth_badge}</div>',
        f'<span style="background:{act_bg};border:1px solid {act_brd};color:{act_txt};'
        f'padding:4px 10px;border-radius:5px;font-size:11px;font-weight:700;'
        f'font-family:DM Sans,sans-serif;">{act}</span>',
        f'<span style="background:#1e1e40;color:#6b7090;font-family:JetBrains Mono,'
        f'monospace;font-size:11px;padding:4px 8px;border-radius:5px;">{r["Score"]}</span>',
        stale_html, '</div>',

        # Body
        '<div style="display:flex;padding:14px 16px;gap:0;">',

        # Left: LTP
        f'<div style="flex:0 0 45%;padding-right:16px;border-right:1px solid #1e1e40;">',
        f'<div style="font-family:JetBrains Mono,monospace;color:#e8e8f4;font-size:26px;'
        f'font-weight:600;line-height:1;">{ltp_str}</div>',
        f'<div style="font-family:JetBrains Mono,monospace;color:{cc};font-size:13px;'
        f'margin-top:4px;font-weight:500;">{cs} {arr}</div>',
        entry_div, '</div>',

        # Right: phase + confidence + RS
        '<div style="flex:1;padding-left:16px;">',
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">',
        f'<span style="background:{pc};color:{ph_txt};padding:6px 12px;border-radius:6px;'
        f'font-size:11px;font-weight:700;font-family:DM Sans,sans-serif;">{ph_txt_full}</span>',
        conf_badge,
        f'<span style="background:#1e1e40;border:1px solid {rs_col}55;padding:6px 10px;'
        f'border-radius:6px;font-size:12px;font-weight:700;font-family:JetBrains Mono,'
        f'monospace;color:{rs_col};">RS{rsr}</span>',
        '</div>', ext_html, '</div>', '</div>',

        # Footer strip
        '<div style="display:flex;align-items:center;padding:9px 16px;'
        'border-top:1px solid #1e1e40;background:#0d0d1a;">',

        _footer_col("RSI", str(rsi_val)),
        _divider(),
        _footer_col("Trend", trend_label, trend_col),
        _divider(),
        _footer_col("Volume", vol_label),
        _divider(),
        _footer_col("Sector", sector),

        '</div></div>',
    ]
    return "".join(parts)


def _footer_col(label: str, value: str, value_color: str = "#e8e8f4") -> str:
    return (
        f'<div style="flex:1;">'
        f'<span style="color:#6b7090;font-size:9px;display:block;'
        f'text-transform:uppercase;letter-spacing:0.5px;">{label}</span>'
        f'<span style="color:{value_color};font-size:12px;">{value}</span>'
        f'</div>'
    )


def _divider() -> str:
    return '<div style="width:1px;background:#1e1e40;height:28px;margin:0 6px;"></div>'


def render_card_grid(cards_html_list: list[str]) -> None:
    wrapper = (
        '<div style="display:flex;flex-wrap:wrap;gap:12px;align-items:stretch;">'
        + "".join(cards_html_list)
        + "</div>"
    )
    st.markdown(wrapper, unsafe_allow_html=True)
    st.markdown(
        '<div style="text-align:center;color:#3a3a60;font-size:10px;'
        'font-family:JetBrains Mono,monospace;padding:4px 0 2px;">'
        'ⓘ Data is indicator based. Confirm with price action.</div>',
        unsafe_allow_html=True,
    )
