"""Matplotlib view: last N profiles side by side, plus daily closes of each period."""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

from .profile import Profile

VA_COLOR = "#6250d6"
OUT_COLOR = "#b4b2a9"
POC_COLOR = "#2a78d6"
PRICE_COLOR = "#e34948"


def plot_profiles(profiles: Sequence[Profile], daily: Optional[pd.DataFrame] = None, last: int = 4,
                  title: str = "", path: Optional[str] = None, cfd_shift: float = 0.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ps = list(profiles)[-last:]
    if not ps:
        raise ValueError("no profiles to plot")
    if cfd_shift:
        ps = [p.shifted(cfd_shift) for p in ps]
    lo = min(p.prices[0] for p in ps)
    hi = max(p.prices[-1] + p.row_size for p in ps)
    fig, axes = plt.subplots(1, len(ps), figsize=(3.2 * len(ps), 6.5), sharey=True)
    axes = np.atleast_1d(axes)
    for ax, p in zip(axes, ps):
        inva = np.zeros(len(p.volumes), dtype=bool)
        inva[p.va_lo_idx:p.va_hi_idx + 1] = True
        ax.barh(p.prices + p.row_size / 2, p.volumes, height=p.row_size,
                color=np.where(inva, VA_COLOR, OUT_COLOR), alpha=0.85, linewidth=0)
        xmax = p.volumes.max()
        ax.set_xlim(0, xmax * 1.55)
        ax.axhline(p.poc, color=POC_COLOR, lw=1.4, xmax=1 / 1.55)
        ax.axhline(p.vah, color="#555", lw=0.9, ls="--", xmax=1 / 1.55)
        ax.axhline(p.val, color="#555", lw=0.9, ls="--", xmax=1 / 1.55)
        # labels in the right margin, pushed apart so they never overlap
        gap = (hi - lo) * 0.028
        labels = sorted(((p.val, "VAL"), (p.poc, "POC"), (p.vah, "VAH")))
        ys, last_y = [], -np.inf
        for y, _ in labels:
            last_y = max(y, last_y + gap)
            ys.append(last_y)
        for (y, t), yl in zip(labels, ys):
            ax.annotate(f"{t} {y:,.1f}", xy=(xmax, y), xytext=(xmax * 1.03, yl), fontsize=7.5,
                        va="center", ha="left", arrowprops=dict(arrowstyle="-", lw=0.5, color="#888"))
        if daily is not None and p.start is not None:
            d = daily.loc[pd.Timestamp(p.start):pd.Timestamp(p.end)]
            if len(d):
                xs = np.linspace(xmax * 0.15, xmax * 0.95, len(d))
                ax.plot(xs, d["close"].to_numpy() + cfd_shift, color=PRICE_COLOR, lw=1, marker="o", ms=2.5)
        state = "" if p.complete else " (đang hình thành)"
        mig = p.meta.get("migration", "")
        ax.set_title(f"{p.label}{state}\n{p.meta.get('shape', '')}  {mig}", fontsize=8.5)
        ax.set_xticks([])
        ax.set_ylim(lo, hi)
        ax.spines[["top", "right", "bottom"]].set_visible(False)
    if title:
        fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    if path:
        fig.savefig(path, dpi=130)
        plt.close(fig)
    return fig
