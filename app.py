"""
app.py — Bull Sutra Pro v11
Thin orchestrator: wires together core / data / ui / analytics packages.

Scan pipeline
─────────────
Pass 1 : parallel OHLCV + daily context fetch       (PERF-2, PERF-3)
Pass 2 : parallel HTF pre-fetch                     (FIX-1, PERF-1)
Pass 2b: vectorised RS ranks                        (PERF-4)
Pass 3 : parallel scoring                           (PERF-1, PERF-7)
Post   : phase transitions recorded in main thread  (PERF-7)
Post   : breadth-based gating                       (FIX-2)
"""

import os
import concurrent.futures
import warnings
import logging
from datetime import datetime

import streamlit as st

# ── Silence noisy third-party loggers ────────────────────────────────────────
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

# ── Internal packages ─────────────────────────────────────────────────────────
from core.scoring   import score_stock, SECTOR_MAP, _market_regime
from core.phases    import (
    PHASE_BRK, PHASE_CONT, PHASE_ENTRY, PHASE_SETUP, PHASE_IDLE,
    record_phase_transition, phase_transition_conf_bonus, get_phase_arrow,
    PHASE_COLORS, PHASE_ORDER,
)
from core.rs_rank   import compute_rs_ranks, _52w_return

from data.fetch     import (
    fetch_nifty, fetch_vix,
    _fetch_one_with_daily, to_nse,
)
from data.htf       import prefetch_htf_parallel
from data.oi        import fetch_oi_data, oi_sentiment
from data.indices   import fetch_indices

from ui.styles      import inject_global_css, action_colors
from ui.cards       import make_card, render_card_grid
from ui.table       import render_results_table, render_export_button
from ui.breadth     import compute_breadth, _breadth_signal, render_breadth_tab
from ui.detail      import render_detail_tab

from analytics.signal_log import log_scan_signals, signal_is_stale, signal_age_label
from analytics.outcomes   import render_analytics_tab

# ── Universe lists ─────────────────────────────────────────────────────────────
try:
    from nse500 import nse500_symbols
    NSE500 = list(dict.fromkeys(
        [s.strip().upper().replace(".NS", "") for s in nse500_symbols]
    ))
except ImportError:
    NSE500 = [
        "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","SBIN",
        "BHARTIARTL","KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI","TITAN",
        "NESTLEIND","WIPRO","ULTRACEMCO","POWERGRID","NTPC","BAJFINANCE","HCLTECH",
        "SUNPHARMA","TECHM","INDUSINDBK","ONGC","COALINDIA","TATASTEEL","JSWSTEEL",
        "HINDALCO","TATAMOTORS","M&M","BAJAJFINSV","DIVISLAB","DRREDDY","CIPLA",
        "EICHERMOT","ADANIENT","ADANIPORTS","BPCL","TATACONSUM","BRITANNIA",
        "HEROMOTOCO","APOLLOHOSP","GRASIM","SBILIFE","HDFCLIFE","ICICIPRULI","VEDL","NMDC",
    ]

NIFTY50 = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","SBIN",
    "BHARTIARTL","KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI","TITAN",
    "NESTLEIND","WIPRO","ULTRACEMCO","POWERGRID","NTPC","BAJFINANCE","HCLTECH",
    "SUNPHARMA","TECHM","INDUSINDBK","ONGC","COALINDIA","TATASTEEL","JSWSTEEL",
    "HINDALCO","TATAMOTORS","M&M","BAJAJFINSV","DIVISLAB","DRREDDY","CIPLA",
    "EICHERMOT","ADANIENT","ADANIPORTS","BPCL","TATACONSUM","BRITANNIA",
    "HEROMOTOCO","APOLLOHOSP","GRASIM","SBILIFE","HDFCLIFE","ICICIPRULI","BAJAJ-AUTO","UPL",
]

MODE_CFG = {
    "Intraday":   dict(period="5d",  interval="5m",  score_th=65, validity_hours=4),
    "Swing":      dict(period="1y",  interval="1d",  score_th=70, validity_hours=72),
    "Positional": dict(period="2y",  interval="1d",  score_th=70, validity_hours=240),
}

VIX_CALM    = 15
VIX_CAUTION = 20
VIX_STRESS  = 25
LIQUIDITY_MIN_CR = 5.0

ACTION_THRESHOLDS = dict(strong_buy=75, buy=58, watch=42)


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_scan(symbols: list, mode: str, progress_bar, status_text,
             vix_val: float = None, min_liq_cr: float = LIQUIDITY_MIN_CR):

    total    = len(symbols)
    min_bars = 60 if mode == "Intraday" else 50
    rejected = 0

    nifty = fetch_nifty(mode)
    market_bullish, regime_label = _market_regime(nifty)

    if not market_bullish:
        st.warning(
            f"⚠️ **Market Regime: {regime_label}** — EMA20 below EMA50. "
            "Scores haircut 15%. Targets compressed."
        )

    # ── Pass 1: OHLCV + daily context ─────────────────────────────────────────
    status_text.text("Pass 1/3: Fetching OHLCV + daily context (parallel)…")
    data         = {}
    daily_closes = {}
    args_list    = [(sym, mode, min_bars) for sym in symbols]
    MAX_WORKERS  = min(6, os.cpu_count() or 4, total)
    completed    = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one_with_daily, a): a[0] for a in args_list}
        for fut in concurrent.futures.as_completed(futures):
            sym, df, daily_df = fut.result()
            completed += 1
            progress_bar.progress(completed / total * 0.40)
            if df is not None:
                data[sym] = df
                if daily_df is not None:
                    daily_closes[sym] = daily_df["Close"]
            else:
                rejected += 1

    # ── Pass 2: HTF ───────────────────────────────────────────────────────────
    status_text.text("Pass 2/3: Pre-fetching HTF data (parallel)…")
    progress_bar.progress(0.40)
    htf_map = prefetch_htf_parallel(list(data.keys()), mode, status_text, progress_bar)

    # ── Pass 2b: Vectorised RS ranks ──────────────────────────────────────────
    status_text.text("Pass 2b/3: Computing RS ranks (vectorised)…")
    sym_52w_returns = {sym: _52w_return(df["Close"]) for sym, df in data.items()}
    rs_rank_map     = compute_rs_ranks(sym_52w_returns)

    # ── PERF-7: snapshot phase history (no session_state in worker threads) ───
    phase_history_snapshot = dict(st.session_state.get("phase_history", {}))

    # ── Pass 3: Parallel scoring ──────────────────────────────────────────────
    status_text.text("Pass 3/3: Scoring stocks (parallel)…")
    results     = []
    liq_skipped = 0
    n_data      = len(data)
    scored      = 0

    def _score_one(sym):
        df      = data[sym]
        htf_up, _ = htf_map.get(sym, (True, "HTF-UNKNOWN"))
        rs_rank   = rs_rank_map.get(sym, 50)
        return sym, score_stock(
            df, nifty, mode,
            daily_close            = daily_closes.get(sym),
            market_bullish         = market_bullish,
            vix_val                = vix_val,
            min_liquidity_cr       = min_liq_cr,
            sym                    = sym,
            htf_up                 = htf_up,
            rs_rank                = rs_rank,
            phase_history_snapshot = phase_history_snapshot,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, n_data)) as pool:
        futures = {pool.submit(_score_one, sym): sym for sym in data}
        for fut in concurrent.futures.as_completed(futures):
            sym, res = fut.result()
            scored  += 1
            progress_bar.progress(0.65 + scored / n_data * 0.30)
            if scored % 20 == 0:
                status_text.text(f"Pass 3/3: Scored {scored}/{n_data}…")
            if res:
                res["Regime"] = regime_label
                res["Symbol"] = sym
                res["Sector"] = SECTOR_MAP.get(sym, "Other")
                if not res["LiquidityOK"]:
                    liq_skipped += 1
                results.append(res)

    # ── PERF-7: phase transitions in main thread ──────────────────────────────
    for res in results:
        sym   = res["Symbol"]
        phase = res["_detected_phase"]
        record_phase_transition(sym, phase)
        res["PhaseBonus"] = phase_transition_conf_bonus(sym)

    # ── FIX-2: Breadth-based gating ──────────────────────────────────────────
    breadth_pulse = compute_breadth(results)
    pct_ema50_now = breadth_pulse.get("pct_above_ema50", 100)
    ad_ratio_now  = breadth_pulse.get("ad_ratio", 2.0)
    breadth_weak  = (pct_ema50_now < 40) and (ad_ratio_now < 0.8)

    if breadth_weak:
        gated_count = 0
        for res in results:
            if res.get("Phase") in (PHASE_BRK, PHASE_CONT):
                if res["Action"] in ("STRONG BUY", "BUY"):
                    res["Action"]       = "WATCH"
                    res["BreadthGated"] = True
                    gated_count        += 1
        if gated_count:
            st.warning(
                f"⚠️ **Breadth Gate active** — only {pct_ema50_now}% above EMA50, "
                f"A/D ratio {ad_ratio_now:.2f}. "
                f"{gated_count} BREAKOUT/CONT signals capped to WATCH."
            )

    progress_bar.progress(1.0)
    results.sort(key=lambda x: x["Score"], reverse=True)
    return results, rejected, liq_skipped


# ═══════════════════════════════════════════════════════════════════════════════
# STREAMLIT APP
# ═══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="NSE Master Scanner Pro",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_global_css()

# ── Session state defaults ────────────────────────────────────────────────────
for key, default in [
    ("results",         []),
    ("scan_time",       None),
    ("rejected",        0),
    ("liq_skipped",     0),
    ("scan_mode",       "Swing"),
    ("signal_log",      []),
    ("phase_history",   {}),
    ("account_size",    500_000),
    ("risk_pct",        0.02),
    ("max_capital_pct", 0.20),
    ("phase_filter",    "All Phases"),
    ("show_illiquid",   False),
    ("min_liq_cr",      5.0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── PERF-5: pre-warm caches on startup ───────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _prewarm():
    fetch_vix()
    fetch_nifty("Swing")
_prewarm()

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    '<div style="font-family:Syne,sans-serif;font-size:28px;font-weight:700;'
    'letter-spacing:-1px;color:#e8e8f4;padding:8px 0 4px;">'
    'BULL SUTRA <span style="color:#f59e0b;">''</span>'
    '<span style="font-size:13px;color:#6b7090;font-family:JetBrains Mono,monospace;'
    'font-weight:400;">PRO · v11</span></div>',
    unsafe_allow_html=True,
)

# ── Global controls ───────────────────────────────────────────────────────────
gc1, gc2, gc3, gc4, gc5 = st.columns([2, 2, 1, 2, 2])
with gc1:
    universe_opt = st.radio("Universe", ["NSE 500", "Nifty 50"], horizontal=True)
with gc2:
    mode_opt = st.radio("Mode", ["Swing", "Intraday", "Positional"], horizontal=True)
with gc3:
    scan_btn = st.button("SCAN", type="primary", use_container_width=True)
with gc4:
    filter_opt = st.selectbox(
        "Filter",
        ["BUY + STRONG BUY", "STRONG BUY only", "WATCH + BUY", "All Results"],
        label_visibility="collapsed",
    )
with gc5:
    search_q = st.text_input(
        "Search symbol", placeholder="e.g. RELIANCE",
        label_visibility="collapsed",
    )

# ── VIX banner ────────────────────────────────────────────────────────────────
vix_val, vix_label = fetch_vix()
_vix_colors = {
    "CALM":    ("#22c55e", "#14532d"),
    "CAUTION": ("#f59e0b", "#78350f"),
    "STRESS":  ("#ef4444", "#7f1d1d"),
    "UNKNOWN": ("#6b7090", "#374151"),
}
vc, vtc = _vix_colors.get(vix_label, _vix_colors["UNKNOWN"])

st.markdown(
    f'<div style="background:{vc}18;border:1px solid {vc}44;'
    f'border-radius:7px;padding:7px 14px;margin:6px 0;display:flex;'
    f'align-items:center;gap:12px;font-family:JetBrains Mono,monospace;">'
    f'<span style="background:{vc};color:{vtc};padding:2px 8px;'
    f'border-radius:4px;font-size:11px;font-weight:700;">'
    f'VIX {vix_val if vix_val else "—"} · {vix_label}</span>'
    + (f'<span style="color:#ef4444;font-size:11px;">'
       f'⚠ High VIX: STRONG BUY blocked · targets compressed</span>'
       if (vix_val and vix_val >= VIX_STRESS) else "")
    + (f'<span style="color:#f59e0b;font-size:11px;">'
       f'⚡ Elevated VIX: targets compressed · SL widened</span>'
       if (vix_val and VIX_CAUTION <= vix_val < VIX_STRESS) else "")
    + "</div>",
    unsafe_allow_html=True,
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_scanner, tab_breadth, tab_detail, tab_analytics, tab_settings = st.tabs(
    ["Scanner", "Breadth Engine", "Detail", "Analytics", "Settings"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS TAB
# ═══════════════════════════════════════════════════════════════════════════════

with tab_settings:
    st.subheader("Scanner Settings")
    sc1, sc2 = st.columns(2)
    with sc1:
        st.session_state.min_liq_cr = st.slider(
            "Min Liquidity (₹ Cr daily traded value)", 1.0, 50.0,
            float(st.session_state.min_liq_cr), 1.0)
        st.session_state.phase_filter = st.selectbox(
            "Phase Filter (Scanner)",
            ["All Phases", "ENTRY", "SETUP", "CONT", "BREAKOUT", "IDLE", "EXIT"],
            index=["All Phases", "ENTRY", "SETUP", "CONT", "BREAKOUT", "IDLE", "EXIT"].index(
                st.session_state.get("phase_filter", "All Phases")))
        st.session_state.show_illiquid = st.checkbox(
            "Show illiquid stocks (below liquidity floor)",
            value=st.session_state.show_illiquid)
        st.markdown("---")
        st.markdown("**Position Sizing**")
        st.session_state.account_size = st.number_input(
            "Account Size (₹)", min_value=10_000, max_value=10_000_000,
            value=int(st.session_state.account_size), step=10_000)
        st.session_state.risk_pct = st.slider(
            "Risk per trade (%)", 0.5, 5.0,
            float(st.session_state.risk_pct * 100), 0.5) / 100.0
        st.session_state.max_capital_pct = st.slider(
            "Max capital per trade (% of account)  ← FIX-5", 5, 50,
            int(st.session_state.max_capital_pct * 100), 5) / 100.0
        st.caption(
            f"Current cap: ₹{st.session_state.account_size * st.session_state.max_capital_pct:,.0f} "
            f"per position ({int(st.session_state.max_capital_pct * 100)}% of account)"
        )
    with sc2:
        st.markdown("**Action Thresholds**")
        st.markdown("""
| Score | Action |
|-------|--------|
| ≥ 75  | STRONG BUY |
| ≥ 58  | BUY |
| ≥ 42  | WATCH |
| < 42  | SKIP |
""")
        st.markdown("**Signal Validity**")
        st.markdown("""
| Mode | Window |
|------|--------|
| Intraday | 4 h |
| Swing | 72 h |
| Positional | 240 h |
""")
        st.markdown("**v11 Fixes**")
        st.markdown("""
| Fix | What changed |
|-----|-------------|
| FIX-1 HTF closed-candle | Drops live bar before EMA calc |
| FIX-2 Breadth gating | BRK/CONT capped to WATCH when breadth weak |
| FIX-3 Structural BRK | Hard vol gate + anti-blowoff guard |
| FIX-4 Intraday vol norm | Time-scaled vol_avg for partial sessions |
| FIX-5 Capital cap | Max capital % clamp in sizer |
| FIX-6 EMA de-dup | Fresh-cross bonus replaces double-count |
""")


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

if scan_btn:
    symbols = NSE500 if universe_opt == "NSE 500" else NIFTY50
    n       = len(symbols)
    est     = "~60s" if n <= 50 else ("~90s" if n <= 150 else "~2 min")
    prog    = st.progress(0)
    stat    = st.empty()

    with st.spinner(f"Scanning {universe_opt} ({n} stocks) · {mode_opt} · {est}"):
        results, rejected, liq_skipped = run_scan(
            symbols, mode_opt, prog, stat,
            vix_val=vix_val, min_liq_cr=st.session_state.min_liq_cr,
        )

    st.session_state.results     = results
    st.session_state.rejected    = rejected
    st.session_state.liq_skipped = liq_skipped
    st.session_state.scan_mode   = mode_opt
    st.session_state.scan_time   = (
        datetime.now().strftime("%H:%M:%S") + f" ({universe_opt} · {mode_opt})"
    )

    log_scan_signals(results, mode_opt)
    prog.empty(); stat.empty()

    st.success(
        f"✅ {len(results)} scanned · {rejected} rejected · "
        f"{liq_skipped} below liquidity floor · {mode_opt}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SCANNER TAB
# ═══════════════════════════════════════════════════════════════════════════════

with tab_scanner:
    indices      = fetch_indices(mode_opt)
    oi_nifty     = fetch_oi_data("NIFTY")
    oi_banknifty = fetch_oi_data("BANKNIFTY")

    # ── Index cards ────────────────────────────────────────────────────────────
    ic1, ic2, ic3 = st.columns(3)
    for (name, oi_data), col in zip(
        [("Nifty 50", oi_nifty), ("BankNifty", oi_banknifty), ("Sensex", None)],
        [ic1, ic2, ic3],
    ):
        d = indices.get(name)
        with col:
            if not d:
                st.markdown(
                    f'<div style="color:#6b7090;font-size:12px;">{name}: unavailable</div>',
                    unsafe_allow_html=True)
                continue

            chg_val = d["chg"]; pct_val = d["pct"]; ltp_val = d["value"]
            cs  = f"+{pct_val:.2f}%" if chg_val >= 0 else f"{pct_val:.2f}%"
            cc  = "#22c55e" if chg_val >= 0 else "#ef4444"
            ar  = "▲" if chg_val >= 0 else "▼"
            act = d["action"]
            score_bar_color = (
                "#f59e0b" if act == "STRONG BUY" else
                "#22c55e" if act == "BUY" else
                "#f59e0b" if act == "WATCH" else "#6b7090"
            )
            sp = int(min(d["score"], 100))

            oi_badge = ""
            if oi_data:
                s_label, s_col = oi_sentiment(oi_data["pcr"])
                pd_   = oi_data["max_pain"] - int(ltp_val)
                pa    = "↑" if pd_ > 0 else ("↓" if pd_ < 0 else "=")
                oi_badge = (
                    f'<div style="margin-top:6px;padding:5px 8px;background:#09090f;'
                    f'border-radius:5px;border:1px solid #1e1e40;font-family:JetBrains Mono,monospace;">'
                    f'<span style="color:#6b7090;font-size:9px;">PCR </span>'
                    f'<span style="background:{s_col}22;border:1px solid {s_col}44;'
                    f'color:{s_col};padding:1px 5px;border-radius:3px;font-size:9px;font-weight:600;">'
                    f'{oi_data["pcr"]} {s_label}</span>'
                    f'<span style="color:#6b7090;font-size:9px;margin-left:6px;">Pain </span>'
                    f'<span style="color:#f59e0b;font-size:9px;font-weight:600;">'
                    f'₹{oi_data["max_pain"]:,} {pa}{abs(pd_):,}</span>'
                    f'<br><span style="color:#ef4444;font-size:9px;">C▶₹{oi_data["call_wall"]:,}  </span>'
                    f'<span style="color:#22c55e;font-size:9px;">P▶₹{oi_data["put_wall"]:,}</span>'
                    f'</div>'
                )

            st.markdown(
                f'<div style="background:#111120;border:1px solid #1e1e40;'
                f'border-radius:10px;padding:14px 16px;">'
                f'<div style="font-family:DM Sans,sans-serif;color:#6b7090;'
                f'font-size:10px;text-transform:uppercase;letter-spacing:1px;">{name}</div>'
                f'<div style="font-family:JetBrains Mono,monospace;color:#e8e8f4;'
                f'font-size:22px;font-weight:600;margin:4px 0 2px;">{ltp_val:,.1f}</div>'
                f'<div style="font-family:JetBrains Mono,monospace;color:{cc};font-size:12px;">'
                f'{ar} {cs}</div>'
                f'<div style="margin:8px 0 4px;background:#1e1e40;border-radius:3px;height:3px;">'
                f'<div style="background:{score_bar_color};width:{sp}%;height:3px;'
                f'border-radius:3px;transition:width 0.3s;"></div></div>'
                f'<div style="display:flex;align-items:center;gap:6px;margin-top:4px;">'
                f'<span style="background:{score_bar_color}22;border:1px solid {score_bar_color}44;'
                f'color:{score_bar_color};padding:2px 7px;border-radius:3px;'
                f'font-size:10px;font-weight:600;font-family:DM Sans,sans-serif;">{act}</span>'
                f'<span style="font-family:JetBrains Mono,monospace;color:#3a3a60;font-size:10px;">'
                f'RSI {d["rsi"]} · {d["trend"]}</span>'
                f'</div>'
                + oi_badge + "</div>",
                unsafe_allow_html=True,
            )

    st.markdown('<div style="border-top:1px solid #1e1e40;margin:16px 0;"></div>',
                unsafe_allow_html=True)

    # ── Filter results ─────────────────────────────────────────────────────────
    results = list(st.session_state.results)
    if filter_opt == "BUY + STRONG BUY":
        results = [r for r in results if r["Action"] in ("BUY", "STRONG BUY")]
    elif filter_opt == "STRONG BUY only":
        results = [r for r in results if r["Action"] == "STRONG BUY"]
    elif filter_opt == "WATCH + BUY":
        results = [r for r in results if r["Action"] in ("WATCH", "BUY", "STRONG BUY")]

    _pf = st.session_state.get("phase_filter", "All Phases")
    if _pf != "All Phases":
        results = [r for r in results if r.get("Phase") == _pf]

    if not st.session_state.get("show_illiquid", False):
        results = [r for r in results if r.get("LiquidityOK", True)]

    if search_q:
        results = [r for r in results if search_q.upper() in r["Symbol"]]

    # ── Ready-to-Trade cards ───────────────────────────────────────────────────
    if st.session_state.results:
        ACTIONABLE_PHASES = {PHASE_ENTRY, PHASE_CONT, PHASE_BRK}
        actionable = [
            r for r in st.session_state.results
            if r.get("Phase") in ACTIONABLE_PHASES and r["Action"] in ("BUY", "STRONG BUY")
        ]
        phase_rank = {PHASE_BRK: 0, PHASE_CONT: 1, PHASE_ENTRY: 2}
        actionable.sort(key=lambda x: (phase_rank.get(x.get("Phase"), 9), -x["Score"]))
        top_act = actionable[:15]

        scan_mode_now = st.session_state.scan_mode
        stale_syms = {
            e["symbol"]
            for e in st.session_state.signal_log
            if signal_is_stale(e["timestamp"], e.get("mode", scan_mode_now))
        }

        if top_act:
            with st.expander(
                f"READY TO TRADE — {len(top_act)} stocks in ENTRY / CONT / BREAKOUT",
                expanded=True,
            ):
                cards = [
                    make_card(i, r, "#22c55e55",
                              show_entry=True, is_stale=r["Symbol"] in stale_syms)
                    for i, r in enumerate(top_act)
                ]
                render_card_grid(cards)
        else:
            st.info("No stocks in ENTRY / CONT / BREAKOUT phase.")

        watchlist = [
            r for r in st.session_state.results
            if r.get("Phase") in (PHASE_SETUP, PHASE_IDLE)
            and r["Score"] >= 58
            and r["Action"] in ("BUY", "STRONG BUY")
        ][:10]
        if watchlist:
            with st.expander(
                f"WATCHLIST — {len(watchlist)} high-score, not yet ready",
                expanded=False,
            ):
                cards = [
                    make_card(i, r, "#f59e0b55", show_entry=False,
                              is_stale=r["Symbol"] in stale_syms)
                    for i, r in enumerate(watchlist)
                ]
                render_card_grid(cards)

    # ── Results table ──────────────────────────────────────────────────────────
    if results:
        render_results_table(results)
        render_export_button(results, st.session_state.scan_mode)
    elif st.session_state.results:
        st.warning("No stocks match current filters.")
    else:
        st.info("Select Universe + Mode, then press SCAN.")


# ═══════════════════════════════════════════════════════════════════════════════
# BREADTH, DETAIL, ANALYTICS TABS
# ═══════════════════════════════════════════════════════════════════════════════

with tab_breadth:
    render_breadth_tab(st.session_state.results, vix_val)

with tab_detail:
    render_detail_tab(st.session_state.results, vix_val)

with tab_analytics:
    render_analytics_tab()
