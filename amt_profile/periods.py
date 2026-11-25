"""Split intraday data into trading days / weeks / months and build one profile per period.

Futures sessions do not follow the calendar day. ``roll_hour`` says when a new
trading day starts in the exchange time zone:

- CME (ES, NQ, YM, NKD, NIY): 17 -> Sunday 18:00 CT belongs to Monday
- Eurex (FDAX, FESX): 0 (calendar day, Berlin time)
- HKEX HSI futures night session from 17:15 HKT: 17.25
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from .profile import (Profile, auto_row_size, extreme_quality, infer_tick, profile_from_bars,
                      profile_from_ticks, tpo_profile)

FREQ = {"day": "D", "week": "W-SUN", "month": "M"}


def to_local(index: pd.DatetimeIndex, tz: str, src_tz: str = "UTC") -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        idx = idx.tz_localize(src_tz)
    return idx.tz_convert(tz)


def trading_dates(index: pd.DatetimeIndex, tz: str = "UTC", roll_hour: float = 0.0,
                  src_tz: str = "UTC") -> pd.DatetimeIndex:
    """Trading date (naive, midnight) of every timestamp."""
    local = to_local(index, tz, src_tz)
    shift = pd.Timedelta(hours=(24 - roll_hour) % 24)
    return (local + shift).normalize().tz_localize(None)


def session_mask(index: pd.DatetimeIndex, session: Optional[Sequence[str]], tz: str,
                 src_tz: str = "UTC") -> np.ndarray:
    """True for bars inside a local session window, e.g. ("08:30", "15:15").

    Windows that cross midnight (e.g. ("17:00", "16:00")) are supported.
    """
    if not session:
        return np.ones(len(index), dtype=bool)
    local = to_local(index, tz, src_tz)
    mins = local.hour * 60 + local.minute
    h0, m0 = map(int, session[0].split(":"))
    h1, m1 = map(int, session[1].split(":"))
    a, b = h0 * 60 + m0, h1 * 60 + m1
    if a <= b:
        return np.asarray((mins >= a) & (mins < b))
    return np.asarray((mins >= a) | (mins < b))


def period_of(dates: pd.DatetimeIndex, by: str) -> pd.PeriodIndex:
    if by not in FREQ:
        raise ValueError(f"by must be one of {list(FREQ)}")
    return pd.DatetimeIndex(dates).to_period(FREQ[by])


def daily_bars(bars: pd.DataFrame, tz: str = "UTC", roll_hour: float = 0.0,
               session: Optional[Sequence[str]] = None, src_tz: str = "UTC") -> pd.DataFrame:
    """Daily OHLCV indexed by trading date, built from intraday bars."""
    b = bars[session_mask(bars.index, session, tz, src_tz)]
    td = trading_dates(b.index, tz, roll_hour, src_tz)
    g = b.groupby(td)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum() if "volume" in b else np.nan,
    })
    out.index.name = "date"
    return out


def profiles_by_period(data: pd.DataFrame, by: str = "week", row_size: Optional[float] = None,
                       tick: Optional[float] = None, tz: str = "UTC", roll_hour: float = 0.0,
                       session: Optional[Sequence[str]] = None, kind: str = "volume",
                       method: str = "uniform", va_pct: float = 0.70, target_rows: int = 120,
                       last_complete: bool = False, src_tz: str = "UTC",
                       min_rows_per_period: int = 1) -> list[Profile]:
    """One profile per trading day / week / month.

    data   : OHLCV bars (open, high, low, close, volume) or ticks (price, size), DatetimeIndex.
    kind   : "volume" (volume at price) or "tpo" (time at price, use 30-minute bars).
    row_size: price height of a row; default picks a 'nice' size from the median period range
              so all periods share one grid.
    last_complete: the last period is marked developing (complete=False) unless True.
    """
    is_ticks = "price" in data.columns and "high" not in data.columns
    d = data[session_mask(data.index, session, tz, src_tz)]
    if d.empty:
        return []
    td = trading_dates(d.index, tz, roll_hour, src_tz)
    per = period_of(td, by)

    if row_size is None:
        if is_ticks:
            hi, lo = d["price"].groupby(per).max(), d["price"].groupby(per).min()
            tick = tick or infer_tick(d["price"].to_numpy())
        else:
            hi, lo = d["high"].groupby(per).max(), d["low"].groupby(per).min()
            tick = tick or infer_tick(d["close"].to_numpy())
        row_size = auto_row_size(float((hi - lo).median()), tick, target_rows)

    out: list[Profile] = []
    keys = pd.unique(per)
    for k in keys:
        m = np.asarray(per == k)
        chunk = d[m]
        if len(chunk) < min_rows_per_period:
            continue
        dates = td[m]
        kw = dict(period=k, start=dates.min(), end=dates.max())
        if is_ticks:
            p = profile_from_ticks(chunk, row_size, va_pct, **kw)
        elif kind == "tpo":
            p = tpo_profile(chunk, row_size, va_pct, **kw)
        else:
            p = profile_from_bars(chunk, row_size, method, va_pct, **kw)
        if p.kind == "volume":
            _set_tpo_quality(p, chunk, row_size, is_ticks)
        out.append(p)
    if out and not last_complete:
        out[-1].complete = False
    for prev, cur in zip(out, out[1:]):
        from .signals import migration  # local import avoids a cycle
        cur.meta["migration"] = migration(prev, cur)
    return out


def _set_tpo_quality(p: Profile, chunk: pd.DataFrame, row_size: float, is_ticks: bool,
                     tpo_minutes: int = 30) -> None:
    """Judge the high/low of a volume profile with 30-minute TPOs (the Market Profile definition).

    A volume profile built from bars almost always has thin extremes, so its own
    'excess' read says little. TPOs show whether the extreme was hit once (tail =
    excess) or several times (flat = poor).
    """
    if is_ticks:
        px = chunk["price"]
        hl = pd.DataFrame({"high": px.resample(f"{tpo_minutes}min").max(),
                           "low": px.resample(f"{tpo_minutes}min").min()})
    else:
        hl = pd.DataFrame({"high": chunk["high"].resample(f"{tpo_minutes}min").max(),
                           "low": chunk["low"].resample(f"{tpo_minutes}min").min()})
    hl = hl.dropna()
    if len(hl) < 3:
        return
    t = tpo_profile(hl, row_size)
    p.meta["high_quality"] = extreme_quality(t, "high")
    p.meta["low_quality"] = extreme_quality(t, "low")
    p.meta["tpo_poc"] = round(t.poc, 6)


def composite_profile(data: pd.DataFrame, start=None, end=None, row_size: Optional[float] = None,
                      tick: Optional[float] = None, kind: str = "volume", method: str = "uniform",
                      va_pct: float = 0.70, target_rows: int = 150) -> Profile:
    """One profile over an arbitrary window (e.g. the current balance area)."""
    d = data.loc[start:end] if (start is not None or end is not None) else data
    is_ticks = "price" in d.columns and "high" not in d.columns
    if row_size is None:
        if is_ticks:
            rng = float(d["price"].max() - d["price"].min())
            tick = tick or infer_tick(d["price"].to_numpy())
        else:
            rng = float(d["high"].max() - d["low"].min())
            tick = tick or infer_tick(d["close"].to_numpy())
        row_size = auto_row_size(rng, tick, target_rows)
    kw = dict(start=d.index.min(), end=d.index.max())
    if is_ticks:
        return profile_from_ticks(d, row_size, va_pct, **kw)
    if kind == "tpo":
        return tpo_profile(d, row_size, va_pct, **kw)
    return profile_from_bars(d, row_size, method, va_pct, **kw)
