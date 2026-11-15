"""Dark trading-terminal theme (references: dense chart + KPI strip)."""

from __future__ import annotations

import streamlit as st

DARK_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root {
  --mda-bg: #070b14;
  --mda-panel: #0e1524;
  --mda-panel-2: #121a2b;
  --mda-border: #1c2740;
  --mda-text: #e8eefc;
  --mda-muted: #8b9bb8;
  --mda-up: #22c55e;
  --mda-down: #ef4444;
  --mda-accent: #2dd4bf;
  --mda-pill: #182338;
}
html, body, [class*="css"]  {
  font-family: "IBM Plex Sans", sans-serif;
}
.stApp {
  background:
    radial-gradient(900px 420px at 8% -8%, #142038 0%, transparent 55%),
    linear-gradient(180deg, #0a101c 0%, var(--mda-bg) 45%);
  color: var(--mda-text);
}
[data-testid="stSidebar"] { display: none; }
header[data-testid="stHeader"] { background: transparent; }
h1, h2, h3, h4 { color: var(--mda-text) !important; letter-spacing: 0.01em; }
.mda-top {
  display: flex; align-items: center; justify-content: space-between;
  margin: 0.2rem 0 0.8rem 0;
}
.mda-brand {
  font-size: 1.55rem; font-weight: 700; letter-spacing: 0.02em;
}
.mda-badge {
  background: rgba(34,197,94,0.12); color: var(--mda-up);
  border: 1px solid rgba(34,197,94,0.35);
  border-radius: 999px; padding: 0.28rem 0.75rem;
  font-size: 0.78rem; font-weight: 600;
}
.mda-panel {
  background: rgba(14, 21, 36, 0.94);
  border: 1px solid var(--mda-border);
  border-radius: 12px;
  padding: 0.85rem 1rem;
  margin-bottom: 0.75rem;
}
.mda-kpi-label {
  color: var(--mda-muted); font-size: 0.78rem; margin-bottom: 0.2rem;
}
.mda-kpi-value {
  font-family: "IBM Plex Mono", monospace;
  font-size: 1.35rem; font-weight: 600; color: var(--mda-text);
}
.mda-kpi-up { color: var(--mda-up); }
.mda-kpi-down { color: var(--mda-down); }
.mda-sub {
  color: var(--mda-muted); font-size: 0.8rem; margin-top: 0.15rem;
}
.mda-section-title {
  font-size: 0.95rem; font-weight: 600; margin: 0.2rem 0 0.55rem;
}
div[data-testid="stMetricValue"] {
  font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums;
}
div[data-testid="stDataFrame"] { border: 1px solid var(--mda-border); border-radius: 8px; }
</style>
"""


def apply_theme() -> None:
    st.markdown(DARK_CSS, unsafe_allow_html=True)


def brand_header(*, authenticated: bool) -> None:
    badge = "Authenticated" if authenticated else "Signed out"
    st.markdown(
        f"""
        <div class="mda-top">
          <div class="mda-brand">MDA Trading Dashboard</div>
          <div class="mda-badge">{badge}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi_card(label: str, value: str, *, tone: str = "", sub: str = "") -> None:
    tone_class = f" mda-kpi-{tone}" if tone in {"up", "down"} else ""
    sub_html = f'<div class="mda-sub">{sub}</div>' if sub else ""
    st.markdown(
        f"""
        <div class="mda-panel">
          <div class="mda-kpi-label">{label}</div>
          <div class="mda-kpi-value{tone_class}">{value}</div>
          {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
