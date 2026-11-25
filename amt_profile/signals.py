"""Auction Market Theory reads on top of profiles.

All daily features use only the PREVIOUS completed week / month as reference,
so they can go straight into a backtest or ML model without look-ahead.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

from .profile import Profile

MIGRATION_SCORE = {
    "higher": 2, "overlap_higher": 1, "unchanged": 0, "inside": 0,
    "outside": 0, "overlap_lower": -1, "lower": -2,
}
SHAPE_CODE = {"D": 0, "P": 1, "b": -1, "B": 2, "elongated": 3}
QUALITY_CODE = {"normal": 0, "poor": 1, "excess": -1}


# --------------------------------------------------------------------------- #
# Single reads
# --------------------------------------------------------------------------- #
def position(price: float, prof: Profile, atr: Optional[float] = None) -> dict:
    """Where price sits versus value: above (expensive) / inside (fair) / below (cheap)."""
    if price > prof.vah:
        pos = "above"
    elif price < prof.val:
        pos = "below"
    else:
        pos = "inside"
    out = {
        "pos": pos,
        "d_poc": price - prof.poc,
        "d_vah": price - prof.vah,
        "d_val": price - prof.val,
    }
    if atr:
        out.update({k + "_atr": v / atr for k, v in list(out.items()) if k.startswith("d_")})
    return out


def migration(prev: Profile, cur: Profile, unchanged_tol: float = 0.10) -> str:
    """Value-area relationship of ``cur`` versus ``prev`` (Dalton's seven cases).

    higher / lower           : no overlap at all
    unchanged                : both edges within ``unchanged_tol`` x prev VA width
    inside / outside         : cur VA inside / engulfing prev VA
    overlap_higher / _lower  : overlapping, VA midpoint moved up / down
    """
    tol = unchanged_tol * max(prev.va_width, prev.row_size)
    if cur.val > prev.vah:
        return "higher"
    if cur.vah < prev.val:
        return "lower"
    if abs(cur.vah - prev.vah) <= tol and abs(cur.val - prev.val) <= tol:
        return "unchanged"
    if cur.val >= prev.val and cur.vah <= prev.vah:
        return "inside"
    if cur.val <= prev.val and cur.vah >= prev.vah:
        return "outside"
    mid_prev = (prev.vah + prev.val) / 2
    mid_cur = (cur.vah + cur.val) / 2
    return "overlap_higher" if mid_cur > mid_prev else "overlap_lower"


def regime(profiles: Sequence[Profile], lookback: int = 3) -> str:
    """balance / imbalance_up / imbalance_down from the last ``lookback`` migrations."""
    done = [p for p in profiles if "migration" in p.meta]
    if not done:
        return "unknown"
    scores = [MIGRATION_SCORE[p.meta["migration"]] for p in done[-lookback:]]
    s = sum(scores)
    if s >= len(scores) and all(x >= 0 for x in scores):
        return "imbalance_up"
    if s <= -len(scores) and all(x <= 0 for x in scores):
        return "imbalance_down"
    return "balance"


def acceptance(closes: pd.Series, prof: Profile, n: int = 2) -> dict:
    """Consecutive recent closes outside the value area of ``prof``.

    n or more closes outside = price ACCEPTED there (value is migrating).
    """
    c = closes.dropna().to_numpy()
    side, count = None, 0
    for x in c[::-1]:
        s = "above" if x > prof.vah else "below" if x < prof.val else None
        if s is None or (side is not None and s != side):
            break
        side, count = s, count + 1
    return {"side": side, "count": count, "accepted": count >= n}


def failed_auction(high: float, low: float, close: float, level_hi: float, level_lo: float) -> Optional[str]:
    """Probe beyond a reference and close back inside = failed auction (a false breakout).

    'failed_above' : traded above level_hi, closed below it  -> bearish
    'failed_below' : traded below level_lo, closed above it  -> bullish
    'failed_both'  : outside bar that closed back inside
    """
    up = high > level_hi and close < level_hi
    dn = low < level_lo and close > level_lo
    if up and dn:
        return "failed_both"
    if up:
        return "failed_above"
    if dn:
        return "failed_below"
    return None


def summary(profiles: Sequence[Profile], daily: Optional[pd.DataFrame] = None, n_accept: int = 2) -> dict:
    """Snapshot for reports: last completed profile, developing profile, where price is now."""
    done = [p for p in profiles if p.complete]
    ref = done[-1] if done else None
    dev = profiles[-1] if profiles and not profiles[-1].complete else None
    out = {"regime": regime(done), "reference": ref.to_dict() if ref else None,
           "developing": dev.to_dict() if dev else None}
    if daily is not None and ref is not None and len(daily):
        last = daily.iloc[-1]
        atr = atr_series(daily).iloc[-1]
        out["last_date"] = str(daily.index[-1].date())
        out["last_close"] = round(float(last["close"]), 2)
        out["position"] = {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in position(float(last["close"]), ref, atr).items()}
        after = daily[daily.index > pd.Timestamp(ref.end)]
        out["acceptance"] = acceptance(after["close"], ref, n_accept) if len(after) else None
        out["failed_auction_today"] = failed_auction(float(last["high"]), float(last["low"]),
                                                     float(last["close"]), ref.vah, ref.val)
        out["atr"] = None if np.isnan(atr) else round(float(atr), 4)
    return out


# --------------------------------------------------------------------------- #
# Features for ML / regime models
# --------------------------------------------------------------------------- #
def atr_series(daily: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = daily["close"].shift()
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - pc).abs(), (daily["low"] - pc).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n, min_periods=max(2, n // 2)).mean()


def _ref_map(profiles: Sequence[Profile]) -> dict:
    return {p.period: p for p in profiles if p.period is not None and p.complete}


def amt_features(daily: pd.DataFrame, weekly: Sequence[Profile], monthly: Optional[Sequence[Profile]] = None,
                 atr_n: int = 14, n_accept: int = 2) -> pd.DataFrame:
    """Per-day AMT features against the PREVIOUS completed week (w_) and month (m_).

    Columns per prefix:
      pos            +1 above value, 0 inside, -1 below
      d_poc_atr      (close - POC) / ATR, same for d_vah_atr, d_val_atr
      va_width_atr   value-area width / ATR (narrow = balance building)
      migration      score of the reference profile vs the one before (-2..+2)
      outside_run    signed count of consecutive closes outside value (+ above, - below)
      accepted       1 if |outside_run| >= n_accept
      failed         +1 failed_below (bullish), -1 failed_above (bearish), 0 none
      shape          code of the reference profile shape (D=0, P=1, b=-1, B=2, elongated=3)
      hiq / loq      quality of the reference high/low (poor=1, excess=-1, normal=0)
    """
    daily = daily.sort_index()
    atr = atr_series(daily, atr_n)
    out = pd.DataFrame(index=daily.index)
    sets = [("w", weekly, "W-SUN")]
    if monthly:
        sets.append(("m", monthly, "M"))
    for pre, profs, freq in sets:
        refs = _ref_map(profs)
        cols = {c: [] for c in ["pos", "d_poc_atr", "d_vah_atr", "d_val_atr", "va_width_atr",
                                "migration", "outside_run", "accepted", "failed", "shape", "hiq", "loq"]}
        run_key, run = None, 0
        for dt, row in daily.iterrows():
            ref = refs.get(pd.Period(dt, freq) - 1)
            a = atr.loc[dt]
            if ref is None or not np.isfinite(a) or a <= 0:
                for c in cols:
                    cols[c].append(np.nan)
                run_key, run = None, 0
                continue
            if run_key != ref.period:
                run_key, run = ref.period, 0
            c = float(row["close"])
            p = 1 if c > ref.vah else -1 if c < ref.val else 0
            run = (run + p if np.sign(run) == p else p) if p != 0 else 0
            fa = failed_auction(float(row["high"]), float(row["low"]), c, ref.vah, ref.val)
            cols["pos"].append(p)
            cols["d_poc_atr"].append((c - ref.poc) / a)
            cols["d_vah_atr"].append((c - ref.vah) / a)
            cols["d_val_atr"].append((c - ref.val) / a)
            cols["va_width_atr"].append(ref.va_width / a)
            cols["migration"].append(MIGRATION_SCORE.get(ref.meta.get("migration"), np.nan))
            cols["outside_run"].append(run)
            cols["accepted"].append(int(abs(run) >= n_accept))
            cols["failed"].append({"failed_below": 1, "failed_above": -1}.get(fa, 0))
            cols["shape"].append(SHAPE_CODE.get(ref.meta.get("shape"), np.nan))
            cols["hiq"].append(QUALITY_CODE.get(ref.meta.get("high_quality"), np.nan))
            cols["loq"].append(QUALITY_CODE.get(ref.meta.get("low_quality"), np.nan))
        for c, v in cols.items():
            out[f"{pre}_{c}"] = v
    return out
