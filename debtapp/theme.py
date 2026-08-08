"""Chart palette and Plotly template.

The categorical slots and both surface sets were validated with the dataviz
validator (light: worst adjacent CVD ΔE 24.2; dark: 10.3, the floor band — which
is why every chart here also ships a legend, 2px gaps between stacked fills, and
a table view, so identity is never carried by colour alone).
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

CATEGORICAL_LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
                     "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
CATEGORICAL_DARK = ["#3987e5", "#199e70", "#c98500", "#008300",
                    "#9085e9", "#e66767", "#d55181", "#d95926"]

LIGHT = {
    "surface": "#fcfcfb",
    "plane": "#f9f9f7",
    "text": "#0b0b0b",
    "text_secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "categorical": CATEGORICAL_LIGHT,
    "principal": "#2a78d6",   # blue — money that reduces what you owe
    "interest": "#e34948",    # red — money the lender keeps
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

DARK = {
    "surface": "#1a1a19",
    "plane": "#0d0d0d",
    "text": "#ffffff",
    "text_secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "categorical": CATEGORICAL_DARK,
    "principal": "#3987e5",
    "interest": "#e66767",
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def palette(dark: bool) -> dict:
    return DARK if dark else LIGHT


def series_color(index: int, dark: bool) -> str:
    """Fixed-order categorical assignment. Never cycles past 8 — callers fold
    the tail into 'Other' instead."""
    c = palette(dark)["categorical"]
    return c[min(index, len(c) - 1)]


def register_template(dark: bool) -> str:
    """Install the Plotly template for the current mode and return its name."""
    p = palette(dark)
    name = "debt_dark" if dark else "debt_light"
    tpl = go.layout.Template()
    tpl.layout = go.Layout(
        font=dict(family=FONT, size=13, color=p["text_secondary"]),
        title=dict(font=dict(size=16, color=p["text"]), x=0, xanchor="left", pad=dict(b=12)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=p["categorical"],
        margin=dict(l=8, r=8, t=48, b=8),
        hoverlabel=dict(font=dict(family=FONT, size=13), bgcolor=p["surface"],
                        bordercolor=p["axis"], font_color=p["text"]),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    font=dict(size=12, color=p["text_secondary"]),
                    bgcolor="rgba(0,0,0,0)", title_text=""),
        xaxis=dict(showgrid=False, zeroline=False, linecolor=p["axis"], linewidth=1,
                   ticks="outside", tickcolor=p["axis"], ticklen=4,
                   tickfont=dict(color=p["muted"], size=12)),
        yaxis=dict(gridcolor=p["grid"], gridwidth=1, zeroline=False, showline=False,
                   ticks="", tickfont=dict(color=p["muted"], size=12)),
    )
    pio.templates[name] = tpl
    return name
