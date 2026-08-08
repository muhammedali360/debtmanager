"""Design tokens: surfaces, type scale, chart palette, and the Plotly template.

Two rules shape everything here.

**Elevation is a colour, not a shadow.** ``canvas`` is the page, ``surface`` is
every card, table, and input sitting on it, and ``raised`` is the fill inside a
surface (table headers, inline callouts). They read as three distinct planes in
both modes — the light set previously separated by a ratio of 1.01, which is to
say not at all, so tables and tiles dissolved into the page.

**Colour never carries identity alone.** The categorical slots were validated
with the dataviz validator (light: worst adjacent CVD ΔE 24.2; dark: 10.3, the
floor band), and every chart still ships a legend, 2px gaps between stacked
fills, and a table view underneath it.

Every text role clears 4.5:1 against both the surface and the canvas it can
land on — ``muted`` used to sit at 3.6:1, which is why the small labels looked
washed out.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

CATEGORICAL_LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
                     "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
CATEGORICAL_DARK = ["#3987e5", "#199e70", "#c98500", "#008300",
                    "#9085e9", "#e66767", "#d55181", "#d95926"]

LIGHT = {
    "canvas": "#f2f2ef",       # the page itself
    "surface": "#ffffff",      # cards, tables, inputs
    "raised": "#f7f7f4",       # fills inside a surface
    "border": "#e4e3dd",
    "border_strong": "#d3d2ca",
    "text": "#111110",
    "text_secondary": "#5b5a55",
    "muted": "#6f6e68",
    "grid": "#ebeae4",
    "axis": "#cfcec5",
    "categorical": CATEGORICAL_LIGHT,
    "principal": "#2a78d6",   # blue — money that reduces what you owe
    "interest": "#e34948",    # red — money the lender keeps
    "good": "#0a8f38",
    "warning": "#b07800",
    "serious": "#c2571f",
    "critical": "#c2352f",
}

DARK = {
    "canvas": "#0c0c0b",
    "surface": "#171716",
    "raised": "#1f1f1d",
    "border": "#2e2e2b",
    "border_strong": "#3d3d39",
    "text": "#f5f5f2",
    "text_secondary": "#b5b4ad",
    "muted": "#8f8e87",
    "grid": "#262624",
    "axis": "#33332f",
    "categorical": CATEGORICAL_DARK,
    "principal": "#3987e5",
    "interest": "#e66767",
    "good": "#3fb964",
    "warning": "#e0a92c",
    "serious": "#ec835a",
    "critical": "#e8635c",
}

# The platform UI face — SF Pro on Apple, Segoe on Windows, Roboto on Android.
# Deliberately not a webfont: the app promises nothing leaves the machine, and a
# Google Fonts request would quietly make that untrue.
FONT = ('system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", '
        'Arial, sans-serif')

# Legends live in the top margin. This is the room they get, and the HTML card
# header above the plot is what keeps a title from ever landing on top of them.
LEGEND_MARGIN = 34


def palette(dark: bool) -> dict:
    return DARK if dark else LIGHT


def series_color(index: int, dark: bool) -> str:
    """Fixed-order categorical assignment. Never cycles past 8 — callers fold
    the tail into 'Other' instead."""
    c = palette(dark)["categorical"]
    return c[min(index, len(c) - 1)]


def register_template(dark: bool) -> str:
    """Install the Plotly template for the current mode and return its name.

    No ``title`` styling here on purpose: chart titles are rendered as HTML in
    the card header above the plot, so Plotly's title and its legend can no
    longer be laid out into the same strip of margin.
    """
    p = palette(dark)
    name = "debt_dark" if dark else "debt_light"
    tpl = go.layout.Template()
    tpl.layout = go.Layout(
        font=dict(family=FONT, size=13, color=p["text_secondary"]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=p["categorical"],
        margin=dict(l=4, r=4, t=LEGEND_MARGIN, b=4),
        hoverlabel=dict(font=dict(family=FONT, size=13), bgcolor=p["surface"],
                        bordercolor=p["border_strong"], font_color=p["text"]),
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
