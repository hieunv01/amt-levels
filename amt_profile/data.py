"""Data loaders and instrument presets.

Volume matters: build value areas from FUTURES (real exchange volume), then shift
the levels onto your CFD with ``Profile.shifted(basis)``. CFD / MT5 "tick_volume"
counts price changes, not contracts, and Yahoo index tickers (^GDAXI, ^N225...)
often carry zero or patchy volume.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

# tz / roll_hour describe the exchange session; rth is the regular (day) session in local time.
# tick is the exchange minimum price step; None = infer from data.
INSTRUMENTS: dict[str, dict] = {
    # CME Globex: trading day starts 17:00 Chicago time
    "ES":   dict(tick=0.25, yf="ES=F",  tz="America/Chicago", roll_hour=17, rth=("08:30", "15:00"), cfd="US500"),
    "NQ":   dict(tick=0.25, yf="NQ=F",  tz="America/Chicago", roll_hour=17, rth=("08:30", "15:00"), cfd="NAS100"),
    "YM":   dict(tick=1.0,  yf="YM=F",  tz="America/Chicago", roll_hour=17, rth=("08:30", "15:00"), cfd="US30"),
    "NKD":  dict(tick=5.0,  yf="NKD=F", tz="America/Chicago", roll_hour=17, rth=None, cfd="JP225"),
    "NIY":  dict(tick=5.0,  yf="NIY=F", tz="America/Chicago", roll_hour=17, rth=None, cfd="JP225"),
    "RTY":  dict(tick=0.1,  yf="RTY=F", tz="America/Chicago", roll_hour=17, rth=("08:30", "15:00"), cfd="US2000"),
    # Eurex: calendar day in Berlin time
    "FDAX": dict(tick=0.5,  yf="^GDAXI", yf_kind="tpo", tz="Europe/Berlin", roll_hour=0, rth=("09:00", "17:30"), cfd="GER40"),
    "FESX": dict(tick=1.0,  yf="^STOXX50E", yf_kind="tpo", tz="Europe/Berlin", roll_hour=0, rth=("09:00", "17:30"), cfd="EU50"),
    # ICE FTSE 100
    "Z":    dict(tick=0.5,  yf="^FTSE", yf_kind="tpo", tz="Europe/London", roll_hour=0, rth=("08:00", "16:30"), cfd="UK100"),
    # Asia: tick left to inference, check your exchange spec
    "HSI":  dict(tick=None, yf="^HSI", yf_kind="tpo", tz="Asia/Hong_Kong", roll_hour=17.25, rth=("09:15", "16:30"), cfd="HK50"),
    "SPI":  dict(tick=None, yf="^AXJO", yf_kind="tpo", tz="Australia/Sydney", roll_hour=17.1, rth=("09:50", "16:30"), cfd="AUS200"),
    # Yahoo has no intraday A50 feed (XIN9.FGI is daily only): needs data/A50.csv
    "A50":  dict(tick=None, yf=None, tz="Asia/Singapore", roll_hour=17.0, rth=("09:00", "16:35"), cfd="CHINA50"),
    # KOSPI cash index: Yahoo carries real exchange volume
    "KOSPI": dict(tick=None, yf="^KS11", tz="Asia/Seoul", roll_hour=0, rth=("09:00", "15:30"), cfd="KOSPI"),
}

# yf = Yahoo ticker used when no local data file exists. A "^" ticker is the cash index, not the
# futures contract. yf_kind="tpo": Yahoo reports zero volume for that index, so build time-at-price
# profiles from its bars instead of volume profiles. A local futures file always uses volume.

# yfinance intraday history limits (approximate): 1m 7 days; 2m-30m 60 days; 1h ~730 days.
YF_MAX_DAYS = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "90m": 60, "1h": 730}


def preset(symbol: str) -> dict:
    return dict(INSTRUMENTS.get(symbol.upper(), {}))


def _standardise(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c: c.lower().strip() for c in df.columns}
    df = df.rename(columns=cols)
    alias = {"vol": "volume", "tick_volume": "tick_volume", "qty": "size", "quantity": "size",
             "last": "price", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    df = df.rename(columns={k: v for k, v in alias.items() if k in df.columns and v not in df.columns})
    return df


def load_csv(path: str, time_col: Optional[str] = None, tz: str = "UTC", **read_kw) -> pd.DataFrame:
    """Load OHLCV bars or ticks from CSV/Parquet.

    Bars : time, open, high, low, close, volume
    Ticks: time, price, size [, side]
    ``tz`` is the time zone of naive timestamps in the file.
    """
    df = pd.read_parquet(path) if str(path).endswith((".parquet", ".pq")) else pd.read_csv(path, **read_kw)
    df = _standardise(df)
    tc = time_col or next((c for c in ("time", "timestamp", "datetime", "date") if c in df.columns), None)
    if tc is None:
        raise ValueError("no time column (time/timestamp/datetime/date)")
    idx = pd.to_datetime(df[tc], utc=False)
    if getattr(idx.dt, "tz", None) is None:
        idx = idx.dt.tz_localize(tz)
    df = df.drop(columns=[tc]).set_index(pd.DatetimeIndex(idx)).sort_index()
    if "volume" not in df.columns and "tick_volume" in df.columns:
        df["volume"] = df["tick_volume"]
    return df


def load_yfinance(symbol: str, interval: str = "30m", days: Optional[int] = None) -> pd.DataFrame:
    """Download bars with yfinance (pip install yfinance). ``symbol`` = preset key or Yahoo ticker."""
    import yfinance as yf  # optional dependency

    ticker = INSTRUMENTS.get(symbol.upper(), {}).get("yf") or symbol
    days = days or YF_MAX_DAYS.get(interval, 60)
    days = min(days, YF_MAX_DAYS.get(interval, days))
    df = yf.download(ticker, period=f"{days}d", interval=interval, auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker} {interval}")
    if isinstance(df.columns, pd.MultiIndex):  # newer yfinance returns (field, ticker) columns
        df.columns = df.columns.get_level_values(0)
    df = _standardise(df.reset_index())
    tc = next(c for c in ("datetime", "date") if c in df.columns)
    idx = pd.DatetimeIndex(pd.to_datetime(df[tc]))
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return df.drop(columns=[tc]).set_index(idx)[["open", "high", "low", "close", "volume"]].sort_index()


def load_mt5(symbol: str, timeframe: str = "M5", count: int = 50000) -> pd.DataFrame:
    """Bars from a running MetaTrader 5 terminal (pip install MetaTrader5, Windows).

    Uses real_volume when the broker provides it (exchange futures), otherwise tick_volume.
    For CFDs tick_volume is activity, not contracts: fine for TPO, rough for volume profile.
    """
    import MetaTrader5 as mt5  # optional dependency

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        tf = getattr(mt5, f"TIMEFRAME_{timeframe.upper()}")
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    finally:
        mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"no MT5 data for {symbol}")
    df = pd.DataFrame(rates)
    # MT5 'time' is the broker's server time stored as epoch seconds; treat as UTC and pass
    # src_tz/tz explicitly if your broker server runs on another clock.
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    if "real_volume" in df and df["real_volume"].sum() > 0:
        df["volume"], src = df["real_volume"], "real"
    else:
        df["volume"], src = df["tick_volume"], "tick"
    out = df[["open", "high", "low", "close", "volume"]].copy()
    out.attrs["volume_source"] = src
    return out


def basis(futures_close: float, cfd_close: float) -> float:
    """Offset to add to futures levels to get CFD levels (sample both at the same moment)."""
    return float(cfd_close - futures_close)
