"""Core volume / TPO profile maths.

Rows are aligned to a global grid (row index = floor(price / row_size)), so the
same price always falls in the same row in every period. That keeps POC/VAH/VAL
comparable week to week and month to month.

Conventions
-----------
- ``prices[i]`` is the LOWER edge of row i.
- POC  = centre of the POC row.
- VAH  = upper edge of the highest row in the value area.
- VAL  = lower edge of the lowest row in the value area.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Sequence

import numpy as np
import pandas as pd

EPS = 1e-9


# --------------------------------------------------------------------------- #
# Data structure
# --------------------------------------------------------------------------- #
@dataclass
class Profile:
    row_size: float
    prices: np.ndarray          # lower edge of each row, ascending
    volumes: np.ndarray         # volume (or TPO count) per row
    poc_idx: int
    va_lo_idx: int
    va_hi_idx: int
    high: float                 # traded high of the period
    low: float                  # traded low of the period
    kind: str = "volume"        # "volume" | "tpo"
    period: Optional[pd.Period] = None
    start: Optional[pd.Timestamp] = None
    end: Optional[pd.Timestamp] = None
    complete: bool = True
    va_pct: float = 0.70
    meta: dict = field(default_factory=dict)

    # ---- key levels -------------------------------------------------------
    @property
    def poc(self) -> float:
        return float(self.prices[self.poc_idx] + self.row_size / 2)

    @property
    def vah(self) -> float:
        return float(self.prices[self.va_hi_idx] + self.row_size)

    @property
    def val(self) -> float:
        return float(self.prices[self.va_lo_idx])

    @property
    def total(self) -> float:
        return float(self.volumes.sum())

    @property
    def va_width(self) -> float:
        return self.vah - self.val

    @property
    def va_share(self) -> float:
        """Share of volume actually inside the value area (>= va_pct)."""
        t = self.total
        return float(self.volumes[self.va_lo_idx:self.va_hi_idx + 1].sum() / t) if t else 0.0

    @property
    def label(self) -> str:
        if self.period is not None:
            return str(self.period)
        if self.start is not None and self.end is not None:
            return f"{self.start:%Y-%m-%d}..{self.end:%Y-%m-%d}"
        return "profile"

    def row_of(self, price: float) -> int:
        """Index of the row containing ``price`` (may be out of range)."""
        first = int(round(self.prices[0] / self.row_size))
        return int(np.floor(price / self.row_size + EPS)) - first

    def volume_between(self, lo: float, hi: float) -> float:
        i0 = max(self.row_of(lo), 0)
        i1 = min(self.row_of(hi), len(self.volumes) - 1)
        return float(self.volumes[i0:i1 + 1].sum()) if i1 >= i0 else 0.0

    def shifted(self, offset: float) -> "Profile":
        """Copy of the profile with every price moved by ``offset``.

        Use it to translate futures levels to your CFD (CFD = futures + basis).
        """
        p = Profile(**{**self.__dict__})
        p.prices = self.prices + offset
        p.high = self.high + offset
        p.low = self.low + offset
        p.meta = {**self.meta, "shift": self.meta.get("shift", 0.0) + offset}
        return p

    def to_dict(self, rows: bool = False) -> dict:
        d = {
            "period": self.label,
            "kind": self.kind,
            "complete": self.complete,
            "start": None if self.start is None else str(self.start),
            "end": None if self.end is None else str(self.end),
            "row_size": self.row_size,
            "poc": round(self.poc, 6),
            "vah": round(self.vah, 6),
            "val": round(self.val, 6),
            "high": round(self.high, 6),
            "low": round(self.low, 6),
            "va_width": round(self.va_width, 6),
            "va_share": round(self.va_share, 4),
            "total": round(self.total, 4),
        }
        d.update({k: v for k, v in self.meta.items() if not isinstance(v, np.ndarray)})
        if rows:
            d["rows"] = [[float(p), float(v)] for p, v in zip(self.prices, self.volumes)]
        return d


# --------------------------------------------------------------------------- #
# Row helpers
# --------------------------------------------------------------------------- #
_NICE = np.array([1, 2, 2.5, 4, 5, 10])


def auto_row_size(price_range: float, tick: Optional[float] = None, target_rows: int = 120) -> float:
    """Pick a 'nice' row size giving roughly ``target_rows`` rows over ``price_range``.

    The result is a multiple of ``tick`` when a tick is given.
    """
    if price_range <= 0:
        return tick or 1.0
    raw = price_range / max(target_rows, 1)
    if tick:
        mult = max(1.0, raw / tick)
        mag = 10 ** np.floor(np.log10(mult))
        nice = _NICE[np.searchsorted(_NICE, mult / mag - EPS)] if mult / mag <= 10 else 10
        size = float(np.round(nice * mag)) * tick
        return max(tick, size)
    mag = 10 ** np.floor(np.log10(raw))
    nice = _NICE[np.searchsorted(_NICE, raw / mag - EPS)] if raw / mag <= 10 else 10
    return float(nice * mag)


def infer_tick(prices: Sequence[float], max_samples: int = 20000) -> Optional[float]:
    """Smallest positive price increment seen in the data (rough tick estimate)."""
    p = np.unique(np.asarray(prices, dtype=float)[:max_samples])
    if len(p) < 2:
        return None
    d = np.diff(p)
    d = d[d > EPS]
    if not len(d):
        return None
    t = float(np.min(d))
    # round away float noise (e.g. 0.2500000001)
    digits = max(0, -int(np.floor(np.log10(t))) + 2)
    return round(t, digits)


def _row_index(price: np.ndarray, row: float) -> np.ndarray:
    return np.floor(np.asarray(price, dtype=float) / row + EPS).astype(np.int64)


def _spread(lo_idx: np.ndarray, hi_idx: np.ndarray, weights: np.ndarray, split: bool):
    """Spread each bar's weight over the rows lo..hi (inclusive).

    split=True  -> weight divided equally between rows (volume profile)
    split=False -> each row receives the full weight (TPO count)
    Returns (row_indices, row_weights) ready for np.bincount.
    """
    n = (hi_idx - lo_idx + 1).astype(np.int64)
    n = np.maximum(n, 1)
    per = weights / n if split else weights.astype(float)
    starts = np.repeat(lo_idx, n)
    offsets = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    return starts + offsets, np.repeat(per, n)


# --------------------------------------------------------------------------- #
# Value area and POC
# --------------------------------------------------------------------------- #
def poc_index(vols: np.ndarray) -> int:
    """Row with the most volume. Ties -> the one closest to the middle of the range."""
    mx = vols.max()
    cand = np.flatnonzero(np.isclose(vols, mx))
    mid = (len(vols) - 1) / 2
    return int(cand[np.argmin(np.abs(cand - mid))])


def value_area(vols: np.ndarray, poc_idx: int, pct: float = 0.70, pair: bool = True):
    """CBOT value-area method.

    Start at the POC and repeatedly compare the next two rows above with the next
    two rows below; add the pair with more volume (above wins ties) until ``pct``
    of total volume is enclosed. ``pair=False`` compares single rows instead.
    Returns (lo_idx, hi_idx) inclusive.
    """
    n = len(vols)
    total = float(vols.sum())
    if n == 0 or total <= 0:
        return poc_idx, poc_idx
    target = pct * total
    lo = hi = poc_idx
    acc = float(vols[poc_idx])
    step = 2 if pair else 1
    while acc < target - EPS:
        can_up, can_dn = hi < n - 1, lo > 0
        if not (can_up or can_dn):
            break
        up_n = min(step, n - 1 - hi)
        dn_n = min(step, lo)
        up = float(vols[hi + 1:hi + 1 + up_n].sum()) if can_up else -np.inf
        dn = float(vols[lo - dn_n:lo].sum()) if can_dn else -np.inf
        if up >= dn:
            hi += up_n
            acc += up
        else:
            lo -= dn_n
            acc += dn
    return lo, hi


def build_profile(row_idx: np.ndarray, weights: np.ndarray, row_size: float, high: float, low: float,
                  va_pct: float = 0.70, kind: str = "volume", **kw) -> Profile:
    """Turn (row index, weight) pairs into a Profile with POC and value area."""
    if len(row_idx) == 0:
        raise ValueError("no data to build a profile")
    first = int(row_idx.min())
    vols = np.bincount(row_idx - first, weights=weights).astype(float)
    prices = (np.arange(len(vols)) + first) * row_size
    p_idx = poc_index(vols)
    lo, hi = value_area(vols, p_idx, va_pct)
    prof = Profile(row_size=row_size, prices=prices, volumes=vols, poc_idx=p_idx,
                   va_lo_idx=lo, va_hi_idx=hi, high=float(high), low=float(low),
                   kind=kind, va_pct=va_pct, **kw)
    prof.meta["shape"] = profile_shape(prof)
    hvn, lvn = nodes(prof)
    prof.meta["hvn"] = [round(x, 6) for x in hvn]
    prof.meta["lvn"] = [round(x, 6) for x in lvn]
    prof.meta["high_quality"] = extreme_quality(prof, "high")
    prof.meta["low_quality"] = extreme_quality(prof, "low")
    return prof


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def profile_from_bars(bars: pd.DataFrame, row_size: float, method: str = "uniform",
                      va_pct: float = 0.70, **kw) -> Profile:
    """Volume profile from OHLCV bars.

    method:
      "uniform" (default) - each bar's volume spread evenly from low to high
      "close"             - all volume at the close
      "typical"           - all volume at (H+L+C)/3
    Finer bars give a truer profile: 1m/5m bars for weekly profiles, 30m/1h for monthly.
    Tick data (profile_from_ticks) is exact.
    """
    b = bars.dropna(subset=["high", "low", "close", "volume"])
    b = b[b["volume"] > 0]
    if b.empty:
        raise ValueError("bars have no volume (index CFDs/Yahoo indices often report 0)")
    vol = b["volume"].to_numpy(float)
    if method == "uniform":
        lo = _row_index(b["low"].to_numpy(), row_size)
        hi = _row_index(b["high"].to_numpy(), row_size)
        idx, w = _spread(lo, np.maximum(hi, lo), vol, split=True)
    elif method == "close":
        idx, w = _row_index(b["close"].to_numpy(), row_size), vol
    elif method == "typical":
        tp = (b["high"] + b["low"] + b["close"]).to_numpy() / 3
        idx, w = _row_index(tp, row_size), vol
    else:
        raise ValueError(f"unknown method {method!r}")
    return build_profile(idx, w, row_size, b["high"].max(), b["low"].min(), va_pct, "volume", **kw)


def profile_from_ticks(ticks: pd.DataFrame, row_size: float, va_pct: float = 0.70, **kw) -> Profile:
    """Exact volume profile from trades: columns price, size."""
    t = ticks.dropna(subset=["price", "size"])
    idx = _row_index(t["price"].to_numpy(), row_size)
    return build_profile(idx, t["size"].to_numpy(float), row_size, t["price"].max(), t["price"].min(),
                         va_pct, "volume", **kw)


def tpo_profile(bars: pd.DataFrame, row_size: float, va_pct: float = 0.70, **kw) -> Profile:
    """Market Profile (TPO) from bars, normally 30-minute bars.

    Each bar is one TPO period and marks every row it traded through once.
    The TPO count shows where the market spent TIME (the original Steidlmayer view).
    """
    b = bars.dropna(subset=["high", "low"])
    lo = _row_index(b["low"].to_numpy(), row_size)
    hi = _row_index(b["high"].to_numpy(), row_size)
    idx, w = _spread(lo, np.maximum(hi, lo), np.ones(len(b)), split=False)
    return build_profile(idx, w, row_size, b["high"].max(), b["low"].min(), va_pct, "tpo", **kw)


# --------------------------------------------------------------------------- #
# Structure: nodes, shape, extremes
# --------------------------------------------------------------------------- #
def _smooth(v: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(v) < k:
        return v.astype(float)
    kern = np.ones(k) / k
    return np.convolve(np.pad(v.astype(float), k // 2, mode="edge"), kern, mode="valid")[:len(v)]


def _prominence(v: np.ndarray, i: int) -> float:
    """Topographic prominence of peak i: height above the higher of the two saddles."""
    left = v[:i][::-1]
    right = v[i + 1:]

    def saddle(side):
        m = v[i]
        for x in side:
            if x > v[i]:
                return m
            m = min(m, x)
        return m  # reached the edge without a higher peak

    return float(v[i] - max(saddle(left), saddle(right)))


def nodes(prof: Profile, smooth: Optional[int] = None, min_prom: float = 0.20, lvn_ratio: float = 0.6):
    """High-volume nodes (prominent peaks) and low-volume nodes (troughs between two HVNs).

    smooth  : rows in the moving average (default ~1/20 of the profile height, min 3)
    min_prom: a peak must stand out by this share of the biggest row (prominence filter)
    lvn_ratio: the trough between two HVNs is an LVN if below lvn_ratio x the smaller peak
    Returns (hvn_prices, lvn_prices) as row centres, HVNs sorted by price.
    """
    n = len(prof.volumes)
    k = smooth or max(3, (n // 20) | 1)
    v = _smooth(prof.volumes, k)
    if n < 3:
        return [prof.poc], []
    vmax = v.max()
    cand = [i for i in range(n)
            if (i == 0 or v[i] >= v[i - 1]) and (i == n - 1 or v[i] > v[i + 1])]
    peaks = [i for i in cand if _prominence(v, i) >= min_prom * vmax or v[i] == vmax]
    peaks = sorted(set(peaks))
    troughs = []
    for a, b in zip(peaks, peaks[1:]):
        t = a + int(np.argmin(v[a:b + 1]))
        if v[t] <= lvn_ratio * min(v[a], v[b]):
            troughs.append(t)
    half = prof.row_size / 2
    return ([float(prof.prices[i] + half) for i in peaks],
            [float(prof.prices[i] + half) for i in troughs])


def profile_shape(prof: Profile) -> str:
    """Rough shape label.

    B          - double distribution (two HVNs split by an LVN)
    elongated  - value area covers >= 65% of the range: volume spread thin, trending profile
    P / b      - POC in the upper / lower 35% of the range (short covering / long liquidation)
    D          - balanced, POC near the middle
    """
    n = len(prof.volumes)
    if n < 5:
        return "D"
    hvn, lvn = nodes(prof)
    if len(lvn) >= 1 and len(hvn) >= 2:
        return "B"
    if (prof.va_hi_idx - prof.va_lo_idx + 1) / n >= 0.65:
        return "elongated"
    pos = prof.poc_idx / (n - 1)
    if pos >= 0.65:
        return "P"
    if pos <= 0.35:
        return "b"
    return "D"


def extreme_quality(prof: Profile, side: str = "high", rows: int = 2,
                    poor_ratio: float = 0.35, excess_ratio: float = 0.12) -> str:
    """Quality of the period's high or low: 'excess' | 'poor' | 'normal'.

    TPO profile (best): 'poor' if the extreme row was hit by 2+ TPO periods (flat top/bottom);
    'excess' if there is a tail of >= ``rows`` single-TPO rows at the extreme.
    Volume profile (approximation): compare the mean volume of the extreme ``rows`` rows with
    the POC row -> heavy extreme = 'poor', thin extreme = 'excess'.
    A poor extreme is an unfinished auction that is often revisited; excess means the other side
    rejected price there decisively.
    """
    v = prof.volumes
    if len(v) < rows + 2:
        return "normal"
    edge = v[::-1] if side == "high" else v
    if prof.kind == "tpo":
        if edge[0] >= 2:
            return "poor"
        tail = 0
        for c in edge:
            if c <= 1:
                tail += 1
            else:
                break
        return "excess" if tail >= rows else "normal"
    r = float(edge[:rows].mean() / v[prof.poc_idx]) if v[prof.poc_idx] else 0.0
    if r >= poor_ratio:
        return "poor"
    if r <= excess_ratio:
        return "excess"
    return "normal"
