"""
ui/styles.py — Global CSS and design tokens injected once at startup.
"""

import streamlit as st

DARK_BG     = "#07070f"
CARD_BG     = "#111120"
BORDER      = "#1e1e40"
TEXT_PRIMARY = "#e8e8f4"
TEXT_MUTED   = "#6b7090"
AMBER        = "#f59e0b"
GREEN        = "#22c55e"
RED          = "#ef4444"

PHASE_COLORS = {
    "IDLE":    "#555577",
    "SETUP":   "#b87333",
    "ENTRY":   "#2255cc",
    "CONT":    "#22aa55",
    "BREAKOUT": "#00dd88",
    "EXIT":    "#cc4444",
}

ACTION_COLOR_MAP = {
    "STRONG BUY": (f"{AMBER}22", f"{AMBER}55", AMBER),
    "BUY":        ("#22c55e1a", "#22c55e44", GREEN),
    "WATCH":      ("#f59e0b11", "#f59e0b33", "#d97706"),
    "SKIP":       ("#6b709011", "#6b709033", TEXT_MUTED),
}


def inject_global_css() -> None:
    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700
  &family=DM+Sans:wght@400;500;600&family=Syne:wght@600;700&display=swap');

html, body, [class*="css"] { background: #07070f; color: #e8e8f4; }
.stApp               { background: #07070f; }
.stDataFrame         { background: #111120; }
.stButton>button     { background:#1a1a35; border:1px solid #2a2a55;
                       color:#e8e8f4; border-radius:8px; }
.stButton>button[kind="primary"] { background:#f59e0b; color:#1a0a00; font-weight:700; }
[data-testid="metric-container"] {
    background:#111120; border:1px solid #1e1e40;
    border-radius:8px; padding:10px;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def action_colors(act: str) -> tuple[str, str, str]:
    """Returns (bg, border, text) CSS colour strings for an action label."""
    return ACTION_COLOR_MAP.get(act, ACTION_COLOR_MAP["SKIP"])
