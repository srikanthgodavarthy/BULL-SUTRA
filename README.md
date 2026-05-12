# Bull Sutra Pro v11 — Distributed Architecture

```
bull_sutra/
│
├── app.py                  ← Thin orchestrator (scan pipeline + Streamlit layout)
│
├── core/                   ← Pure logic, no Streamlit I/O
│   ├── scoring.py          ← score_stock(), action_label(), math helpers
│   ├── phases.py           ← Phase constants, detect_phase_and_entry(), history
│   ├── exhaustion.py       ← detect_exhaustion(), ext_phase_override()
│   ├── confidence.py       ← compute_confidence(), confidence_label()
│   ├── targets.py          ← _compute_targets(), vix_target_mult()
│   ├── liquidity.py        ← liquidity_ok(), _intraday_vol_avg()  [FIX-4]
│   └── rs_rank.py          ← compute_rs_ranks(), _52w_return()    [PERF-4]
│
├── data/                   ← I/O with external APIs, all @st.cache_data
│   ├── fetch.py            ← fetch_nifty(), fetch_vix(), _fetch_one_with_daily()
│   ├── htf.py              ← prefetch_htf_parallel(), _htf_trend_from_df() [FIX-1]
│   ├── oi.py               ← fetch_oi_data(), oi_sentiment()
│   └── indices.py          ← fetch_indices()
│
├── ui/                     ← Rendering only, returns HTML strings or calls st.*
│   ├── styles.py           ← inject_global_css(), design tokens, action_colors()
│   ├── cards.py            ← make_card(), render_card_grid()
│   ├── table.py            ← render_results_table(), render_export_button()
│   ├── breadth.py          ← compute_breadth(), render_breadth_tab()  [FIX-2]
│   └── detail.py           ← render_detail_tab(), position_size()     [FIX-5]
│
└── analytics/
    ├── signal_log.py       ← log_scan_signals(), signal_is_stale(), age_label()
    └── outcomes.py         ← render_analytics_tab(), win-rate breakdown
```

## Running

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Design principles

| Layer | Rule |
|-------|------|
| `core/` | Zero Streamlit imports. Pure functions, no side-effects. |
| `data/` | All network I/O here. `@st.cache_data` on every public fetch. |
| `ui/` | Rendering only — no scoring logic. Returns HTML strings or calls `st.*`. |
| `analytics/` | Log mutation + read-only rendering. Touches `st.session_state` only. |
| `app.py` | Wires everything together. Owns the scan pipeline and tab layout. |

## v11 fixes

| Fix | Module |
|-----|--------|
| FIX-1 HTF closed-candle | `data/htf.py` |
| FIX-2 Breadth gating | `ui/breadth.py` + `app.py` |
| FIX-3 Structural BRK filter | `core/phases.py` |
| FIX-4 Intraday vol normalisation | `core/liquidity.py` |
| FIX-5 Capital cap | `ui/detail.py` → `position_size()` |
| FIX-6 EMA de-duplication | `core/scoring.py` |
