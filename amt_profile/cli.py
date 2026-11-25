"""Command line.

Examples
--------
  # NQ weekly volume profiles from Yahoo 30m bars, mapped to your NAS100 CFD
  python -m amt_profile --source yfinance --symbol NQ --by week --cfd-price 24890 --json nq_week.json --plot nq.png

  # FDAX monthly profile from your own 1-minute CSV, day session only
  python -m amt_profile --source csv --file fdax_1m.csv --symbol FDAX --by month --rth

  # Exact profile from tick data (price,size) of your footprint pipeline
  python -m amt_profile --source csv --file nkd_ticks.parquet --symbol NKD --by week
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import pandas as pd

from . import data as D
from .periods import daily_bars, profiles_by_period
from .signals import summary


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="amt_profile", description="Volume profile / TPO + AMT reads")
    ap.add_argument("--source", choices=["yfinance", "csv", "mt5"], required=True)
    ap.add_argument("--symbol", required=True, help="preset (ES, NQ, NKD, FDAX...) or raw ticker/symbol")
    ap.add_argument("--file", help="CSV/Parquet path for --source csv")
    ap.add_argument("--file-tz", default="UTC", help="time zone of naive timestamps in the file")
    ap.add_argument("--interval", default=None, help="yfinance interval (30m, 1h...) or MT5 timeframe (M5, M30...)")
    ap.add_argument("--days", type=int, default=None, help="history to download (yfinance)")
    ap.add_argument("--count", type=int, default=50000, help="bars to pull (mt5)")
    ap.add_argument("--by", choices=["day", "week", "month"], default="week")
    ap.add_argument("--kind", choices=["volume", "tpo"], default="volume")
    ap.add_argument("--row-size", type=float, default=None)
    ap.add_argument("--tick", type=float, default=None)
    ap.add_argument("--va", type=float, default=0.70, help="value-area share (default 0.70)")
    ap.add_argument("--tz", default=None, help="exchange time zone (default from preset)")
    ap.add_argument("--roll-hour", type=float, default=None, help="hour a new trading day starts")
    ap.add_argument("--rth", action="store_true", help="regular session only (preset window)")
    ap.add_argument("--session", default=None, help="custom session window HH:MM-HH:MM")
    ap.add_argument("--last", type=int, default=6, help="profiles to print/plot")
    ap.add_argument("--last-complete", action="store_true", help="treat the last period as finished")
    ap.add_argument("--cfd-price", type=float, default=None,
                    help="current CFD price; levels are shifted by (CFD - futures last close)")
    ap.add_argument("--json", default=None, help="write levels + summary as JSON")
    ap.add_argument("--csv", default=None, help="write one row per profile as CSV")
    ap.add_argument("--plot", default=None, help="write PNG chart")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    pre = D.preset(a.symbol)
    tz = a.tz or pre.get("tz", "UTC")
    roll = a.roll_hour if a.roll_hour is not None else pre.get("roll_hour", 0.0)
    session = tuple(a.session.split("-")) if a.session else (pre.get("rth") if a.rth else None)
    tick = a.tick or pre.get("tick")

    if a.source == "yfinance":
        interval = a.interval or ("30m" if a.by in ("day", "week") else "1h")
        bars = D.load_yfinance(a.symbol, interval, a.days)
    elif a.source == "mt5":
        bars = D.load_mt5(a.symbol, a.interval or "M5", a.count)
    else:
        if not a.file:
            print("--file is required for --source csv", file=sys.stderr)
            return 2
        bars = D.load_csv(a.file, tz=a.file_tz)

    profs = profiles_by_period(bars, by=a.by, row_size=a.row_size, tick=tick, tz=tz, roll_hour=roll,
                               session=session, kind=a.kind, va_pct=a.va, last_complete=a.last_complete)
    if not profs:
        print("no profiles built (check session window / data)", file=sys.stderr)
        return 1
    is_ticks = "price" in bars.columns and "high" not in bars.columns
    if is_ticks:
        px = bars["price"]
        b = pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "volume": bars["size"]})
    else:
        b = bars
    daily = daily_bars(b, tz, roll, session)

    shift = 0.0
    if a.cfd_price is not None:
        shift = D.basis(float(daily["close"].iloc[-1]), a.cfd_price)

    show = profs[-a.last:]
    rows = []
    for p in show:
        q = p.shifted(shift) if shift else p
        rows.append({"period": q.label, "done": "yes" if q.complete else "dev",
                     "POC": round(q.poc, 2), "VAH": round(q.vah, 2), "VAL": round(q.val, 2),
                     "high": round(q.high, 2), "low": round(q.low, 2), "shape": q.meta.get("shape"),
                     "migration": q.meta.get("migration", ""), "hiQ": q.meta.get("high_quality"),
                     "loQ": q.meta.get("low_quality")})
    table = pd.DataFrame(rows)
    hdr = f"{a.symbol} {a.kind} profile by {a.by}  row={profs[0].row_size}  tz={tz} roll={roll}"
    if session:
        hdr += f" session={session[0]}-{session[1]}"
    if shift:
        hdr += f"  levels shifted by {shift:+.2f} to CFD"
    print(hdr)
    print(table.to_string(index=False))

    snap = summary(profs, daily)
    if shift:
        snap["cfd_shift"] = shift
        for k in ("reference", "developing"):
            if snap.get(k):
                for f in ("poc", "vah", "val", "high", "low"):
                    snap[k][f] = round(snap[k][f] + shift, 4)
                snap[k]["hvn"] = [round(x + shift, 4) for x in snap[k].get("hvn", [])]
                snap[k]["lvn"] = [round(x + shift, 4) for x in snap[k].get("lvn", [])]
        if snap.get("last_close") is not None:
            snap["last_close_cfd"] = round(snap["last_close"] + shift, 2)
    pos = snap.get("position", {})
    acc = snap.get("acceptance") or {}
    close_txt = f"{snap.get('last_close_cfd', snap.get('last_close'))}"
    print(f"\nregime: {snap['regime']} | last close {close_txt}{' (CFD)' if shift else ''} is "
          f"{pos.get('pos')} last completed value (POC dist {pos.get('d_poc_atr', float('nan')):+.2f} ATR)"
          f" | outside run: {acc.get('count', 0)} {acc.get('side') or ''}"
          f"{' (accepted)' if acc.get('accepted') else ''} | failed auction today: {snap.get('failed_auction_today')}")

    if a.json:
        payload = {
            "symbol": a.symbol, "cfd": pre.get("cfd"), "by": a.by, "kind": a.kind,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "volume_source": bars.attrs.get("volume_source", a.source),
            "summary": snap,
            "profiles": [(p.shifted(shift) if shift else p).to_dict() for p in profs[-a.last:]],
        }
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    if a.csv:
        table.to_csv(a.csv, index=False)
    if a.plot:
        from .plot import plot_profiles
        plot_profiles(profs, daily, last=min(a.last, 6), title=hdr, path=a.plot, cfd_shift=shift)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
