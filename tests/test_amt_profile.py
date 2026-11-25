import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from amt_profile import (acceptance, amt_features, auto_row_size, build_profile, daily_bars,
                         extreme_quality, failed_auction, migration, position, profile_from_bars,
                         profile_from_ticks, profiles_by_period, regime, tpo_profile, trading_dates,
                         value_area)
from amt_profile.profile import poc_index


def synth_bars(start="2026-10-04 23:00", weeks=4, freq="30min", center=25000.0, drift_per_week=0.0,
               seed=7, tz="UTC"):
    """Mean-reverting walk around a centre that can drift week to week; more volume near centre."""
    rng = np.random.default_rng(seed)
    n = int(weeks * 7 * 24 * 3600 / pd.Timedelta(freq).total_seconds())
    idx = pd.date_range(start, periods=n, freq=freq, tz=tz)
    idx = idx[idx.dayofweek < 5]
    px, out = center, []
    for i, t in enumerate(idx):
        c = center + drift_per_week * (t - idx[0]).days / 7
        px += -0.05 * (px - c) + rng.normal(0, 8)
        hi = px + abs(rng.normal(0, 6))
        lo = px - abs(rng.normal(0, 6))
        op = px + rng.normal(0, 2)
        vol = 1000 * np.exp(-((px - c) / 60) ** 2) + 50
        out.append((op, hi, lo, px, vol))
    return pd.DataFrame(out, index=idx, columns=["open", "high", "low", "close", "volume"])


# ---------------------------------------------------------------- core maths
def test_value_area_cbot_example():
    vols = np.array([10, 20, 30, 50, 100, 60, 40, 20, 10], float)
    assert poc_index(vols) == 4
    # up pair (60+40=100) beats down pair (50+30=80) -> then down pair (80) beats up (30)
    assert value_area(vols, 4, 0.70) == (2, 6)


def test_poc_tie_prefers_middle():
    assert poc_index(np.array([5, 1, 1, 5, 1, 1, 1], float)) == 3


def test_uniform_spread_and_levels():
    bars = pd.DataFrame({"open": [101], "high": [104.5], "low": [100.0], "close": [103], "volume": [50]},
                        index=pd.DatetimeIndex(["2026-11-02 10:00"], tz="UTC"))
    p = profile_from_bars(bars, row_size=1.0)
    assert np.allclose(p.volumes, 10.0)          # rows 100,101,102,103,104
    assert p.prices[0] == 100 and p.prices[-1] == 104
    assert p.val == 100 and p.vah <= 105


def test_value_area_share_and_contains_poc():
    p = profile_from_bars(synth_bars(weeks=1), row_size=5.0)
    assert p.va_share >= 0.70
    assert p.val <= p.poc <= p.vah
    assert p.prices[0] <= p.val and p.vah <= p.prices[-1] + p.row_size


def test_ticks_exact():
    ticks = pd.DataFrame({"price": [100, 100, 101, 102, 102, 102], "size": [1, 2, 1, 1, 1, 5]},
                         index=pd.date_range("2026-11-02", periods=6, freq="s", tz="UTC"))
    p = profile_from_ticks(ticks, row_size=1.0)
    assert list(p.volumes) == [3, 1, 7]
    assert p.poc == 102.5


def test_auto_row_size_is_tick_multiple():
    r = auto_row_size(2000, tick=0.25, target_rows=120)
    assert r >= 0.25 and abs(r / 0.25 - round(r / 0.25)) < 1e-9
    assert 2000 / r < 400


def test_shifted_moves_all_levels():
    p = profile_from_bars(synth_bars(weeks=1), row_size=5.0)
    q = p.shifted(12.5)
    assert q.poc == pytest.approx(p.poc + 12.5)
    assert q.vah == pytest.approx(p.vah + 12.5) and q.val == pytest.approx(p.val + 12.5)


def test_double_distribution_gives_b_shape_and_lvn():
    rng = np.random.default_rng(1)
    prices = np.concatenate([rng.normal(100, 2, 4000), rng.normal(130, 2, 4000)])
    ticks = pd.DataFrame({"price": np.round(prices, 1), "size": 1.0},
                         index=pd.date_range("2026-11-02", periods=len(prices), freq="s", tz="UTC"))
    p = profile_from_ticks(ticks, row_size=1.0)
    assert p.meta["shape"] == "B"
    assert len(p.meta["hvn"]) == 2 and len(p.meta["lvn"]) == 1
    assert 105 < p.meta["lvn"][0] < 125


def test_weekly_volume_profile_judges_extremes_with_tpo():
    profs = profiles_by_period(synth_bars(weeks=2, freq="5min"), by="week", tick=0.5)
    for p in profs:
        assert p.meta["high_quality"] in ("excess", "poor", "normal")
        assert "tpo_poc" in p.meta


# ---------------------------------------------------------------- sessions / periods
def test_trading_dates_cme_roll():
    idx = pd.DatetimeIndex(["2026-11-22 23:30", "2026-11-20 20:00", "2026-11-23 22:59"], tz="UTC")
    td = trading_dates(idx, "America/Chicago", 17)
    # Sun 17:30 CT -> Monday; Fri 14:00 CT -> Friday; Mon 16:59 CT -> Monday
    assert list(td.strftime("%Y-%m-%d")) == ["2026-11-23", "2026-11-20", "2026-11-23"]


def test_weekly_profiles_and_migration():
    bars = synth_bars(weeks=4, drift_per_week=150)
    profs = profiles_by_period(bars, by="week", tz="America/Chicago", roll_hour=17, tick=0.25)
    assert len(profs) >= 4
    assert profs[-1].complete is False and all(p.complete for p in profs[:-1])
    assert len({p.row_size for p in profs}) == 1             # one shared grid
    migs = [p.meta["migration"] for p in profs[1:]]
    assert all(m in ("higher", "overlap_higher") for m in migs[:2]), migs
    assert regime(profs[:-1], lookback=2) == "imbalance_up"


def test_tpo_excess_and_poor():
    idx = pd.date_range("2026-11-02 08:00", periods=6, freq="30min", tz="UTC")
    # one bar spikes to 110 alone (tail) -> excess high; three bars hit the same low 100 -> poor low
    bars = pd.DataFrame({"high": [104, 110, 104, 104, 103, 103], "low": [100, 103, 100, 101, 100, 101]},
                        index=idx)
    p = tpo_profile(bars, row_size=1.0)
    assert extreme_quality(p, "high") == "excess"
    assert extreme_quality(p, "low") == "poor"


# ---------------------------------------------------------------- AMT reads
def _va(vah, val, poc=None, row=1.0):
    return SimpleNamespace(vah=vah, val=val, poc=poc if poc is not None else (vah + val) / 2,
                           va_width=vah - val, row_size=row)


@pytest.mark.parametrize("prev,cur,exp", [
    ((110, 100), (125, 115), "higher"),
    ((110, 100), (95, 85), "lower"),
    ((110, 100), (110.5, 100.5), "unchanged"),
    ((110, 100), (108, 103), "inside"),
    ((110, 100), (115, 95), "outside"),
    ((110, 100), (114, 104), "overlap_higher"),
    ((110, 100), (106, 96), "overlap_lower"),
])
def test_migration_cases(prev, cur, exp):
    assert migration(_va(*prev), _va(*cur)) == exp


def test_failed_auction_and_acceptance():
    assert failed_auction(105, 101, 103, level_hi=104, level_lo=100) == "failed_above"
    assert failed_auction(103, 99, 101, level_hi=104, level_lo=100) == "failed_below"
    assert failed_auction(106, 98, 102, level_hi=104, level_lo=100) == "failed_both"
    assert failed_auction(106, 101, 105, level_hi=104, level_lo=100) is None   # accepted above
    ref = _va(105, 100)
    acc = acceptance(pd.Series([103, 104, 106, 107]), ref, n=2)
    assert acc == {"side": "above", "count": 2, "accepted": True}
    assert acceptance(pd.Series([106, 103]), ref)["count"] == 0


def test_position():
    ref = _va(105, 100, poc=102)
    assert position(106, ref)["pos"] == "above"
    assert position(99, ref, atr=2)["d_poc_atr"] == pytest.approx(-1.5)


def test_features_use_previous_week_only():
    bars = synth_bars(weeks=4, drift_per_week=100)
    tz, roll = "America/Chicago", 17
    weekly = profiles_by_period(bars, by="week", tz=tz, roll_hour=roll, tick=0.25)
    daily = daily_bars(bars, tz, roll)
    f = amt_features(daily, weekly)
    first_week = weekly[0].period
    in_first = [d for d in f.index if pd.Period(d, "W-SUN") == first_week]
    assert f.loc[in_first, "w_pos"].isna().all()               # no reference yet
    # a day in week 2 must be measured against week 1's POC
    d2 = next(d for d in f.index if pd.Period(d, "W-SUN") == weekly[1].period and np.isfinite(f.loc[d, "w_pos"]))
    from amt_profile import atr_series
    atr = atr_series(daily).loc[d2]
    assert f.loc[d2, "w_d_poc_atr"] * atr + weekly[0].poc == pytest.approx(daily.loc[d2, "close"])
    # changing future data must not change past features
    bars2 = bars.copy()
    last_week_mask = trading_dates(bars2.index, tz, roll).to_period("W-SUN") == weekly[-1].period
    bars2.loc[last_week_mask, ["open", "high", "low", "close"]] += 500
    f2 = amt_features(daily_bars(bars2, tz, roll),
                      profiles_by_period(bars2, by="week", tz=tz, roll_hour=roll, tick=0.25,
                                         row_size=weekly[0].row_size))
    past = [d for d in f.index if pd.Period(d, "W-SUN") < weekly[-1].period]
    pd.testing.assert_frame_equal(f.loc[past], f2.loc[past])


# ---------------------------------------------------------------- loaders (mocked, no network)
def test_load_yfinance_mocked(monkeypatch):
    import sys
    import types
    raw = synth_bars(weeks=1).rename(columns=str.title)
    raw["Adj Close"] = raw["Close"]
    raw.index.name = "Datetime"
    raw.columns = pd.MultiIndex.from_product([raw.columns, ["NQ=F"]])   # newer yfinance layout
    calls = {}

    def download(ticker, **kw):
        calls["ticker"], calls["kw"] = ticker, kw
        return raw

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=download))
    from amt_profile.data import load_yfinance
    df = load_yfinance("NQ", "30m", 400)
    assert calls["ticker"] == "NQ=F" and calls["kw"]["period"] == "60d"   # capped at yfinance limit
    assert list(df.columns) == ["open", "high", "low", "close", "volume"] and df.index.tz is not None


def test_load_mt5_mocked_prefers_real_volume(monkeypatch):
    import sys
    import types
    n = 10
    rates = pd.DataFrame({"time": np.arange(n) * 300 + 1_763_000_000, "open": 1.0, "high": 2.0, "low": 0.5,
                          "close": 1.5, "tick_volume": 7, "spread": 1, "real_volume": 0}).to_records(index=False)
    fake = types.SimpleNamespace(initialize=lambda: True, shutdown=lambda: None, last_error=lambda: None,
                                 TIMEFRAME_M5=5, copy_rates_from_pos=lambda s, tf, a, b: rates)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    from amt_profile.data import load_mt5
    df = load_mt5("GER40", "M5", n)
    assert df.attrs["volume_source"] == "tick" and (df["volume"] == 7).all()


# ---------------------------------------------------------------- CLI
def test_cli_csv_end_to_end(tmp_path):
    bars = synth_bars(weeks=3, drift_per_week=-120)
    path = tmp_path / "fdax_30m.csv"
    bars.reset_index(names="time").to_csv(path, index=False)
    out = tmp_path / "out.json"
    png = tmp_path / "out.png"
    from amt_profile.cli import main
    rc = main(["--source", "csv", "--file", str(path), "--symbol", "FDAX", "--by", "week",
               "--cfd-price", "25010", "--json", str(out), "--plot", str(png)])
    assert rc == 0
    j = json.loads(out.read_text())
    assert j["summary"]["position"]["pos"] in ("above", "inside", "below")
    assert "cfd_shift" in j["summary"] and png.stat().st_size > 10_000
    assert all({"poc", "vah", "val"} <= set(p) for p in j["profiles"])
