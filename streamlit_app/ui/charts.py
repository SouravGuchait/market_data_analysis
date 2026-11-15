"""Plotly chart builders from backend candle/indicator payloads."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots


def _float(value: object) -> float | None:
    candidate: object = value
    if isinstance(value, bool) or value is None:
        candidate = None
    elif isinstance(value, str):
        with contextlib.suppress(ValueError):
            candidate = float(value)
        if isinstance(candidate, str):
            candidate = None
    if isinstance(candidate, int | float) and not isinstance(candidate, bool):
        return float(candidate)
    return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def candle_figure(rows: Sequence[Mapping[str, object]], *, title: str) -> go.Figure:
    times = [_text(row.get("timestamp") or row.get("bucket_start")) for row in rows]
    opens = [_float(row.get("open")) for row in rows]
    highs = [_float(row.get("high")) for row in rows]
    lows = [_float(row.get("low")) for row in rows]
    closes = [_float(row.get("close")) for row in rows]
    volumes = [_float(row.get("volume")) for row in rows]
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.74, 0.26],
    )
    fig.add_trace(
        go.Candlestick(
            x=times,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            name="OHLC",
            increasing_line_color="#22c55e",
            decreasing_line_color="#ef4444",
            increasing_fillcolor="#22c55e",
            decreasing_fillcolor="#ef4444",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(x=times, y=volumes, name="Volume", marker_color="#2dd4bf", opacity=0.55),
        row=2,
        col=1,
    )
    fig.update_layout(
        title={"text": title, "x": 0.01, "xanchor": "left", "font": {"size": 14}},
        template="plotly_dark",
        paper_bgcolor="#070b14",
        plot_bgcolor="#0e1524",
        xaxis_rangeslider_visible=False,
        margin={"l": 48, "r": 16, "t": 40, "b": 24},
        height=620,
        legend={"orientation": "h", "y": 1.08},
        font={"family": "IBM Plex Sans", "color": "#e8eefc"},
    )
    fig.update_xaxes(gridcolor="#1c2740", zeroline=False)
    fig.update_yaxes(gridcolor="#1c2740", zeroline=False, tickfont={"family": "IBM Plex Mono"})
    return fig
