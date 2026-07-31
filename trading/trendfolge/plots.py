"""Report figures.

All output is PNG through the Agg backend, so nothing here needs a display.

Colour is assigned by the job it does rather than by taste:

* the equity chart compares distinct entities, so it uses the first three
  categorical slots in fixed order -- the strategy is always slot one, the
  benchmark always slot two, whether or not a third series is present;
* returns, walk-forward windows, R multiples and the parameter heatmap all encode
  polarity around zero, so they use the diverging pair with a neutral midpoint;
* drawdown and the bootstrap are single series, so they take one hue and need no
  legend.

The three-slot categorical set was checked with the palette validator in both
light and dark mode: every gate passes, with one contrast warning on the light
surface for the third slot. The mitigation the rule requires is applied here --
every line carries a visible direct label at its right end in addition to the
legend, so identity never rests on colour alone.

Two measures of different scale never share an axis. Where a figure needs to show
both, it becomes stacked small multiples instead.
"""

import os
from typing import Dict, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter  # noqa: E402


def _thousands(value: float, _position: int) -> str:
    """Tick label formatter: 250000 renders as 250k."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:.0f}"


LIGHT = {
    "surface": "#fcfcfb",
    "ink": "#0b0b0b",
    "ink_secondary": "#52514e",
    "ink_muted": "#8a8983",
    "grid": "#e6e5e1",
    "series": ("#2a78d6", "#eb6834", "#1baf7a"),
    "positive": "#2a78d6",
    "negative": "#e34948",
    "midpoint": "#f0efec",
}

DARK = {
    "surface": "#1a1a19",
    "ink": "#ffffff",
    "ink_secondary": "#c3c2b7",
    "ink_muted": "#8a8983",
    "grid": "#333331",
    "series": ("#3987e5", "#d95926", "#199e70"),
    "positive": "#3987e5",
    "negative": "#e66767",
    "midpoint": "#383835",
}

THEMES = {"light": LIGHT, "dark": DARK}

_FIGSIZE = (9.0, 5.0)
_DPI = 140


def theme_for(name: str) -> Dict[str, object]:
    """Look up a theme by name.

    Args:
        name: Either ``light`` or ``dark``.

    Returns:
        The theme dictionary.
    """
    if name not in THEMES:
        raise ValueError(f"unknown theme {name!r}; expected one of {sorted(THEMES)}")
    return THEMES[name]


def _new_figure(theme, rows: int = 1, figsize=None, sharex: bool = False):
    """Create a figure and axes already dressed in the theme's surface colour."""
    figure, axes = plt.subplots(
        rows, 1, figsize=figsize or _FIGSIZE, dpi=_DPI, sharex=sharex
    )
    figure.patch.set_facecolor(theme["surface"])
    axes_list = axes if isinstance(axes, np.ndarray) else np.array([axes])
    for axis in axes_list:
        _dress(axis, theme)
    return figure, axes_list


def _dress(axis, theme) -> None:
    """Apply the recessive axis style: no box, faint grid, muted tick labels."""
    axis.set_facecolor(theme["surface"])
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(theme["grid"])
    axis.grid(True, color=theme["grid"], linewidth=0.8, alpha=0.9)
    axis.set_axisbelow(True)
    axis.tick_params(colors=theme["ink_secondary"], labelsize=9, length=0)


def _title(axis, theme, title: str, subtitle: str = "") -> None:
    """Set a title, and a smaller subtitle line beneath it when supplied."""
    axis.set_title(title, color=theme["ink"], fontsize=12, loc="left", pad=16 if subtitle else 8)
    if subtitle:
        axis.text(
            0.0,
            1.02,
            subtitle,
            transform=axis.transAxes,
            color=theme["ink_secondary"],
            fontsize=9,
            va="bottom",
        )


def _save(figure, path: str, theme) -> str:
    """Write a figure to disk, creating the directory if needed."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    figure.tight_layout()
    figure.savefig(path, facecolor=theme["surface"], bbox_inches="tight")
    plt.close(figure)
    return path


def _diverging_colormap(theme) -> LinearSegmentedColormap:
    """Two-hue diverging map with a neutral midpoint, as the colour rule requires."""
    return LinearSegmentedColormap.from_list(
        "trendfolge_diverging",
        [theme["negative"], theme["midpoint"], theme["positive"]],
    )


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def equity_curve(
    curves: Mapping[str, pd.Series],
    path: str,
    theme_name: str = "light",
    log_scale: bool = True,
    currency: str = "EUR",
) -> str:
    """Plot the strategy against its benchmarks on one shared axis.

    Args:
        curves: Ordered mapping of label to equity curve. The first entry is the
            strategy and always takes the first categorical slot.
        path: Destination PNG path.
        theme_name: ``light`` or ``dark``.
        log_scale: Use a logarithmic value axis, which is the only honest way to
            compare compounding series over many years.
        currency: Account currency, for the axis label.

    Returns:
        The path written.
    """
    theme = theme_for(theme_name)
    figure, axes = _new_figure(theme)
    axis = axes[0]

    for slot, (label, curve) in enumerate(curves.items()):
        clean = curve.dropna()
        if clean.empty:
            continue
        colour = theme["series"][slot % len(theme["series"])]
        axis.plot(clean.index, clean.to_numpy(), color=colour, linewidth=2.0, label=label)
        # Direct label at the right end. This is required, not decorative: the
        # third slot sits below 3:1 contrast on the light surface, and the
        # palette rule allows it only when identity is also carried by a label.
        axis.annotate(
            label,
            xy=(clean.index[-1], clean.iloc[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            color=theme["ink_secondary"],
            fontsize=9,
            va="center",
        )

    if log_scale:
        axis.set_yscale("log")
        # The default log locator labels only the decades, which on an equity
        # curve spanning less than one decade leaves a single readable tick.
        axis.yaxis.set_major_locator(LogLocator(base=10.0, subs=(1.0, 2.0, 5.0)))
        axis.yaxis.set_minor_locator(LogLocator(base=10.0, subs=tuple(np.arange(1.0, 10.0))))
        axis.yaxis.set_major_formatter(FuncFormatter(_thousands))
        axis.yaxis.set_minor_formatter(NullFormatter())
    axis.set_ylabel(f"account value ({currency})", color=theme["ink_secondary"], fontsize=9)
    _title(
        axis,
        theme,
        "Equity curve",
        "logarithmic scale: equal vertical distances are equal percentage moves",
    )
    legend = axis.legend(frameon=False, loc="upper left", fontsize=9)
    for text in legend.get_texts():
        text.set_color(theme["ink_secondary"])
    return _save(figure, path, theme)


def drawdown(equity: pd.Series, path: str, theme_name: str = "light") -> str:
    """Plot the underwater curve of a single equity series."""
    theme = theme_for(theme_name)
    figure, axes = _new_figure(theme, figsize=(9.0, 3.4))
    axis = axes[0]

    series = equity.dropna()
    underwater = (series / series.cummax() - 1.0) * 100.0
    axis.fill_between(
        underwater.index, underwater.to_numpy(), 0.0, color=theme["negative"], alpha=0.28
    )
    axis.plot(underwater.index, underwater.to_numpy(), color=theme["negative"], linewidth=2.0)

    worst = float(underwater.min())
    worst_date = underwater.idxmin()
    axis.annotate(
        f"worst {worst:.1f}%",
        xy=(worst_date, worst),
        xytext=(8, 8),
        textcoords="offset points",
        color=theme["ink_secondary"],
        fontsize=9,
    )
    axis.set_ylabel("drawdown (%)", color=theme["ink_secondary"], fontsize=9)
    _title(axis, theme, "Drawdown from the running peak")
    return _save(figure, path, theme)


def annual_returns(equity: pd.Series, path: str, theme_name: str = "light") -> str:
    """Bar chart of calendar year returns, coloured by sign."""
    from .metrics import yearly_returns

    theme = theme_for(theme_name)
    figure, axes = _new_figure(theme, figsize=(9.0, 4.0))
    axis = axes[0]

    returns = yearly_returns(equity) * 100.0
    if returns.empty:
        _title(axis, theme, "Annual returns", "no complete year in the sample")
        return _save(figure, path, theme)

    labels = [str(date.year) for date in returns.index]
    colours = [
        theme["positive"] if value >= 0 else theme["negative"] for value in returns
    ]
    bars = axis.bar(labels, returns.to_numpy(), color=colours, width=0.68)
    axis.axhline(0.0, color=theme["ink_muted"], linewidth=1.0)

    for bar, value in zip(bars, returns):
        offset = 3 if value >= 0 else -12
        axis.annotate(
            f"{value:.0f}",
            xy=(bar.get_x() + bar.get_width() / 2, value),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            color=theme["ink_secondary"],
            fontsize=8,
        )

    axis.set_ylabel("return (%)", color=theme["ink_secondary"], fontsize=9)
    _title(axis, theme, "Calendar year returns")
    return _save(figure, path, theme)


def exposure(equity: pd.DataFrame, path: str, theme_name: str = "light") -> str:
    """Invested share and open position count, as stacked small multiples.

    The two measures have different units and different scales. Putting them on
    one pair of axes would be a dual-axis chart, which invites the reader to see
    a relationship that the geometry invented, so they get one panel each.
    """
    theme = theme_for(theme_name)
    figure, axes = _new_figure(theme, rows=2, figsize=(9.0, 5.0), sharex=True)

    invested = (equity["positions_value"] / equity["equity"]).clip(lower=0.0) * 100.0
    axes[0].fill_between(
        invested.index, invested.to_numpy(), 0.0, color=theme["series"][0], alpha=0.30
    )
    axes[0].plot(invested.index, invested.to_numpy(), color=theme["series"][0], linewidth=1.6)
    axes[0].set_ylabel("invested (%)", color=theme["ink_secondary"], fontsize=9)
    _title(axes[0], theme, "Exposure")

    axes[1].fill_between(
        equity.index,
        equity["n_positions"].to_numpy(),
        0.0,
        color=theme["series"][1],
        alpha=0.30,
        step="post",
    )
    axes[1].step(
        equity.index, equity["n_positions"].to_numpy(), color=theme["series"][1],
        linewidth=1.6, where="post",
    )
    axes[1].set_ylabel("open positions", color=theme["ink_secondary"], fontsize=9)
    return _save(figure, path, theme)


def trade_r_histogram(trades: pd.DataFrame, path: str, theme_name: str = "light") -> str:
    """Distribution of trade outcomes in R multiples, coloured by sign."""
    theme = theme_for(theme_name)
    figure, axes = _new_figure(theme, figsize=(9.0, 4.0))
    axis = axes[0]

    if trades.empty or "r_multiple" not in trades.columns:
        _title(axis, theme, "Trade outcomes in R", "no trades")
        return _save(figure, path, theme)

    values = trades["r_multiple"].replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        _title(axis, theme, "Trade outcomes in R", "no trades")
        return _save(figure, path, theme)

    counts, edges = np.histogram(values, bins=30)
    centres = (edges[:-1] + edges[1:]) / 2
    colours = [theme["positive"] if centre >= 0 else theme["negative"] for centre in centres]
    axis.bar(centres, counts, width=(edges[1] - edges[0]) * 0.9, color=colours)
    axis.axvline(0.0, color=theme["ink_muted"], linewidth=1.0)

    axis.set_xlabel(
        "profit or loss, in multiples of the risk taken",
        color=theme["ink_secondary"],
        fontsize=9,
    )
    axis.set_ylabel("trades", color=theme["ink_secondary"], fontsize=9)
    _title(
        axis,
        theme,
        "Trade outcomes in R",
        f"{len(values)} trades, median {values.median():.2f}R, mean {values.mean():.2f}R",
    )
    return _save(figure, path, theme)


def walkforward_windows(windows: pd.DataFrame, path: str, theme_name: str = "light") -> str:
    """Out-of-sample growth rate per walk-forward window."""
    theme = theme_for(theme_name)
    figure, axes = _new_figure(theme, figsize=(9.0, 4.0))
    axis = axes[0]

    if windows.empty:
        _title(axis, theme, "Out-of-sample result per window", "no windows")
        return _save(figure, path, theme)

    labels = [
        f"{start.date()}" for start in pd.to_datetime(windows["test_start"])
    ]
    values = windows["oos_cagr"].to_numpy() * 100.0
    colours = [theme["positive"] if value >= 0 else theme["negative"] for value in values]
    axis.bar(labels, values, color=colours, width=0.68)
    axis.axhline(0.0, color=theme["ink_muted"], linewidth=1.0)
    axis.tick_params(axis="x", rotation=60)

    axis.set_ylabel("out-of-sample CAGR (%)", color=theme["ink_secondary"], fontsize=9)
    _title(
        axis,
        theme,
        "Out-of-sample result per window",
        "each bar is a year traded with parameters chosen only from data before it",
    )
    return _save(figure, path, theme)


def sensitivity_panels(
    sweeps: pd.DataFrame,
    parameter: str,
    path: str,
    theme_name: str = "light",
    metrics: Sequence[str] = ("cagr", "calmar", "max_drawdown", "n_trades"),
) -> str:
    """One panel per metric across a parameter sweep.

    Args:
        sweeps: Output of the sweep functions.
        parameter: Which parameter to plot.
        path: Destination PNG path.
        theme_name: ``light`` or ``dark``.
        metrics: Metrics to show, one panel each. They have incompatible units,
            so they are never overlaid on a shared axis.

    Returns:
        The path written.
    """
    theme = theme_for(theme_name)
    subset = sweeps[sweeps["parameter"] == parameter]
    figure, axes = _new_figure(
        theme, rows=len(metrics), figsize=(7.5, 2.1 * len(metrics)), sharex=True
    )

    if subset.empty:
        _title(axes[0], theme, f"Sensitivity to {parameter}", "no data")
        return _save(figure, path, theme)

    for axis, metric in zip(axes, metrics):
        axis.plot(
            subset["value"].to_numpy(),
            subset[metric].to_numpy(),
            color=theme["series"][0],
            linewidth=2.0,
            marker="o",
            markersize=5,
        )
        axis.set_ylabel(metric, color=theme["ink_secondary"], fontsize=9)
        if metric in ("cagr", "calmar"):
            axis.axhline(0.0, color=theme["ink_muted"], linewidth=1.0)

    axes[0].set_title(
        f"Sensitivity to {parameter}", color=theme["ink"], fontsize=12, loc="left"
    )
    axes[-1].set_xlabel(parameter, color=theme["ink_secondary"], fontsize=9)
    return _save(figure, path, theme)


def heatmap(
    matrix: pd.DataFrame,
    path: str,
    theme_name: str = "light",
    label: str = "Calmar",
) -> str:
    """Two-parameter heatmap on a diverging scale centred at zero.

    Every cell carries its value as text, which doubles as the table view the
    accessibility pass asks for.
    """
    theme = theme_for(theme_name)
    figure, axes = _new_figure(theme, figsize=(7.0, 5.0))
    axis = axes[0]

    values = matrix.to_numpy(dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        _title(axis, theme, f"{label} across two parameters", "no data")
        return _save(figure, path, theme)

    extent = max(abs(float(finite.min())), abs(float(finite.max())), 1e-9)
    norm = TwoSlopeNorm(vmin=-extent, vcenter=0.0, vmax=extent)
    image = axis.imshow(
        values, cmap=_diverging_colormap(theme), norm=norm, aspect="auto"
    )

    axis.set_xticks(range(matrix.shape[1]), [str(column) for column in matrix.columns])
    axis.set_yticks(range(matrix.shape[0]), [str(row) for row in matrix.index])
    axis.set_xlabel(str(matrix.columns.name), color=theme["ink_secondary"], fontsize=9)
    axis.set_ylabel(str(matrix.index.name), color=theme["ink_secondary"], fontsize=9)
    axis.grid(False)

    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = values[row, column]
            text = "n/a" if not np.isfinite(value) else f"{value:.2f}"
            axis.text(
                column, row, text, ha="center", va="center",
                color=theme["ink"], fontsize=9,
            )

    colourbar = figure.colorbar(image, ax=axis, fraction=0.04, pad=0.03)
    colourbar.outline.set_visible(False)
    colourbar.ax.tick_params(colors=theme["ink_secondary"], labelsize=8, length=0)
    _title(
        axis,
        theme,
        f"{label} across two parameters",
        "a broad plateau is robustness; a single bright cell is curve fitting",
    )
    return _save(figure, path, theme)


def bootstrap_distribution(
    bootstrap: pd.DataFrame,
    path: str,
    theme_name: str = "light",
    column: str = "total_return",
) -> str:
    """Histogram of a bootstrapped statistic with its 5/50/95 percentiles marked."""
    theme = theme_for(theme_name)
    figure, axes = _new_figure(theme, figsize=(9.0, 4.0))
    axis = axes[0]

    if bootstrap.empty or column not in bootstrap.columns:
        _title(axis, theme, "Bootstrap distribution", "no trades to resample")
        return _save(figure, path, theme)

    values = bootstrap[column].to_numpy() * 100.0
    axis.hist(values, bins=40, color=theme["series"][0], alpha=0.85)

    for percentile, style in ((5, ":"), (50, "-"), (95, ":")):
        mark = float(np.percentile(values, percentile))
        axis.axvline(mark, color=theme["ink_secondary"], linewidth=1.4, linestyle=style)
        axis.annotate(
            f"p{percentile} {mark:.0f}%",
            xy=(mark, axis.get_ylim()[1]),
            xytext=(3, -12),
            textcoords="offset points",
            color=theme["ink_secondary"],
            fontsize=8,
        )

    axis.set_xlabel(f"{column.replace('_', ' ')} (%)", color=theme["ink_secondary"], fontsize=9)
    axis.set_ylabel("resamples", color=theme["ink_secondary"], fontsize=9)
    _title(
        axis,
        theme,
        "Bootstrap of the trade sequence",
        "the width of this band is the honest uncertainty around the headline number",
    )
    return _save(figure, path, theme)
