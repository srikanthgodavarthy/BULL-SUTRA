"""
analytics/outcomes.py — Analytics tab: signal log table, win-rate metrics,
phase win-rate breakdown, and log export.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from analytics.signal_log import signal_is_stale, signal_age_label


def render_analytics_tab() -> None:
    st.subheader("Signal Log & Outcome Tracking")

    log = st.session_state.signal_log
    if not log:
        st.info("No signals logged yet. Run a scan to populate.")
        return

    scan_mode_now = st.session_state.get("scan_mode", "Swing")
    log_df = pd.DataFrame(log)

    log_df["stale"] = log_df.apply(
        lambda row: signal_is_stale(row["timestamp"], row.get("mode", scan_mode_now)), axis=1)
    log_df["age"] = log_df.apply(
        lambda row: signal_age_label(row["timestamp"], row.get("mode", scan_mode_now))[0], axis=1)

    _render_summary_metrics(log_df, scan_mode_now)
    edited = _render_editable_log(log_df, log)
    _apply_edits(edited, log, log_df)
    _render_phase_winrate(log, scan_mode_now)
    _render_export(log)


def _render_summary_metrics(log_df: pd.DataFrame, scan_mode_now: str) -> None:
    total_sig = len(log_df)
    pending   = len(log_df[log_df["outcome"] == "Pending"])
    stale_cnt = int(log_df["stale"].sum())
    wins      = len(log_df[log_df["outcome"] == "Win"])
    losses    = len(log_df[log_df["outcome"] == "Loss"])

    active_df     = log_df[~log_df["stale"]]
    active_wins   = len(active_df[active_df["outcome"] == "Win"])
    active_losses = len(active_df[active_df["outcome"] == "Loss"])

    win_rate   = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else None
    active_wr  = (round(active_wins / (active_wins + active_losses) * 100, 1)
                  if (active_wins + active_losses) > 0 else None)

    am1, am2, am3, am4, am5 = st.columns(5)
    am1.metric("Total Signals", total_sig)
    am2.metric("Pending",       pending)
    am3.metric("Expired",       stale_cnt)
    am4.metric("Overall Win%",  f"{win_rate}%" if win_rate is not None else "—")
    am5.metric("Active Win%",   f"{active_wr}%" if active_wr is not None else "—")


def _render_editable_log(log_df: pd.DataFrame, log: list) -> pd.DataFrame | None:
    display_cols = ["timestamp", "symbol", "action", "phase", "score",
                    "confidence", "rs_rank", "entry", "sl", "t1",
                    "age", "outcome", "breadth_gated"]
    display_cols = [c for c in display_cols if c in log_df.columns]

    return st.data_editor(
        log_df[display_cols].tail(100),
        column_config={
            "outcome": st.column_config.SelectboxColumn(
                "Outcome", options=["Pending", "Win", "Loss", "BE"], required=True),
            "age":          st.column_config.TextColumn("Age", disabled=True),
            "rs_rank":      st.column_config.NumberColumn("RS Rank", disabled=True),
            "breadth_gated": st.column_config.CheckboxColumn("B-Gated", disabled=True),
        },
        hide_index=True,
        use_container_width=True,
    )


def _apply_edits(edited: pd.DataFrame | None, log: list, log_df: pd.DataFrame) -> None:
    if edited is not None and len(edited) == len(log_df.tail(100)):
        for i, row in edited.iterrows():
            idx = len(log_df) - 100 + i
            if 0 <= idx < len(log):
                log[idx]["outcome"] = row["outcome"]


def _render_phase_winrate(log: list, scan_mode_now: str) -> None:
    wins   = sum(1 for e in log if e.get("outcome") == "Win")
    losses = sum(1 for e in log if e.get("outcome") == "Loss")
    if wins + losses == 0:
        return

    st.markdown("---")
    st.subheader("Phase Win-Rate (active signals only)")

    phase_stats: dict[str, dict] = {}
    for entry in log:
        if signal_is_stale(entry["timestamp"], entry.get("mode", scan_mode_now)):
            continue
        ph = entry.get("phase", "UNKNOWN")
        oc = entry.get("outcome", "Pending")
        if oc in ("Win", "Loss"):
            if ph not in phase_stats:
                phase_stats[ph] = {"Win": 0, "Loss": 0}
            phase_stats[ph][oc] += 1

    if phase_stats:
        ps_rows = []
        for ph, stats in phase_stats.items():
            w  = stats["Win"]; l = stats["Loss"]
            wr = round(w / (w + l) * 100, 1) if (w + l) > 0 else 0
            ps_rows.append({"Phase": ph, "Wins": w, "Losses": l, "Win Rate": f"{wr}%"})
        st.dataframe(pd.DataFrame(ps_rows), hide_index=True, use_container_width=True)


def _render_export(log: list) -> None:
    if st.button("Export Signal Log"):
        export_df = pd.DataFrame(log).drop(columns=["ExtFlags"], errors="ignore")
        csv       = export_df.to_csv(index=False)
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            "Download", csv, f"NSE_SignalLog_{ts}.csv", "text/csv")
