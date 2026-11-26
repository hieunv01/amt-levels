"""Tính mức POC/VAH/VAL tuần và tháng cho nhiều hợp đồng, ghi JSON + PNG vào thư mục out/.

Chạy:      python run_levels.py
Tùy chọn:  python run_levels.py --symbols NQ ES NKD --out out
           python run_levels.py --cfd NQ=24890 ES=7140      (dịch mức sang giá CFD hiện tại)

- Futures CME trên Yahoo (ES, NQ, YM, RTY, NKD, NIY): volume thật, tải tự động (cần: pip install yfinance).
- KOSPI: chỉ số cash ^KS11, Yahoo có volume thật.
- FDAX, FESX, CAC40, IBEX35, FTSEMIB, Z, HSI, SPI: Yahoo chỉ có chỉ số cash (^GDAXI, ^STOXX50E, ^FCHI,
  ^IBEX, FTSEMIB.MI, ^FTSE, ^HSI, ^AXJO) và volume = 0,
  nên dùng profile TPO (thời gian tại giá). Mức giá là của chỉ số cash, không phải futures.
- HSCEI (CHINAH), HSTECH (HKTECH): chỉ số cash ^HSCE, HSTECH.HK, volume = 0 nên dùng TPO.
- CSI300 (CHINA300): chỉ số cash 000300.SS, có volume thật.
- A50 (CHINA50): Yahoo không có intraday của chỉ số, dùng ETF 2823.HK (volume thật, giá tính bằng HKD
  của ETF). JSON có thêm poc_rel/vah_rel/val_rel: mức CFD = *_rel x giá CHINA50 hiện tại.
  Hoặc chạy --cfd A50=<giá CHINA50> để quy đổi thẳng toàn bộ profile sang đơn vị CFD.
- File data/<MÃ>.csv hoặc .parquet (cột time,open,high,low,close,volume hoặc time,price,size cho tick,
  giờ UTC) luôn được ưu tiên và tính volume profile.
Kết quả: out/<MÃ>_week.json, out/<MÃ>_month.json, out/<MÃ>_week.png, và out/levels_latest.json gộp tất cả.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from amt_profile import daily_bars, load_csv, load_yfinance, preset, profiles_by_period, summary
from amt_profile.data import basis, yf_type
from amt_profile.plot import plot_profiles

DEFAULT_SYMBOLS = ["NQ", "ES", "NKD", "YM", "RTY", "FDAX", "FESX", "CAC40", "IBEX35", "FTSEMIB", "Z", "HSI", "SPI", "KOSPI",
                   "A50", "HSCEI", "HSTECH", "CSI300"]


def find_local(symbol: str, data_dir: Path):
    for ext in (".parquet", ".csv"):
        f = data_dir / f"{symbol}{ext}"
        if f.exists():
            return f
    return None


def _rescale(bars, k: float):
    b = bars.copy()
    cols = [c for c in ("open", "high", "low", "close", "price") if c in b.columns]
    b[cols] = b[cols] * k
    return b


def run_symbol(symbol: str, data_dir: Path, out: Path, cfd_price: float | None) -> dict:
    p = preset(symbol)
    common = dict(tick=p.get("tick"), tz=p.get("tz", "UTC"), roll_hour=p.get("roll_hour", 0.0))
    local = find_local(symbol, data_dir)
    if local:
        bars_w = bars_m = load_csv(str(local), tz="UTC")
        source = f"file:{local.name}"
        kind, src_type = "volume", "futures"
    elif p.get("yf"):
        bars_w = load_yfinance(symbol, "30m")   # ~60 ngày cho profile tuần
        bars_m = load_yfinance(symbol, "1h")    # ~2 năm cho profile tháng
        source = f"yfinance:{p['yf']}"
        kind, src_type = p.get("yf_kind", "volume"), yf_type(p)
    else:
        raise RuntimeError(f"không có dữ liệu cho {symbol}: đặt file {data_dir}/{symbol}.csv")

    scale = 1.0
    if src_type == "etf":
        etf_close = float(bars_w["close"].dropna().iloc[-1])
        if cfd_price:
            # An ETF trades at a fraction of the index, so map it to the CFD by ratio, not by basis.
            scale = cfd_price / etf_close
            bars_w, bars_m = (_rescale(b, scale) for b in (bars_w, bars_m))
            cfd_price, src_type = None, "cfd"
            common["tick"] = None
    result = {"symbol": symbol, "cfd": p.get("cfd"), "source": source, "kind": kind}
    for by, bars in (("week", bars_w), ("month", bars_m)):
        profs = profiles_by_period(bars, by=by, kind=kind, **common)
        daily = daily_bars(bars, common["tz"], common["roll_hour"])
        shift = basis(float(daily["close"].iloc[-1]), cfd_price) if cfd_price else 0.0
        snap = summary(profs, daily)
        payload = {
            "symbol": symbol, "cfd": p.get("cfd"), "by": by, "source": source, "kind": kind,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cfd_shift": shift, "summary": snap,
            "profiles": [(x.shifted(shift) if shift else x).to_dict() for x in profs[-6:]],
        }
        (out / f"{symbol}_{by}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                                                 encoding="utf-8")
        if by == "week":
            plot_profiles(profs, daily, last=5, title=f"{symbol} weekly {'TPO' if kind == 'tpo' else 'volume'} profile",
                          path=str(out / f"{symbol}_week.png"), cfd_shift=shift)
        ref = snap.get("reference") or {}
        result[by] = {k: (round(ref[k] + shift, 2) if k in ("poc", "vah", "val") and k in ref else ref.get(k))
                      for k in ("period", "poc", "vah", "val", "shape", "migration", "high_quality", "low_quality")}
        result[by].update({"regime": snap.get("regime"), "position": (snap.get("position") or {}).get("pos"),
                           "acceptance": snap.get("acceptance"), "failed_auction_today": snap.get("failed_auction_today")})
    result["last_date"] = snap.get("last_date")
    result["last_close_futures"] = snap.get("last_close")
    result["cfd_shift"] = round(shift, 2)
    if shift and snap.get("last_close") is not None:
        result["last_close_cfd"] = round(snap["last_close"] + shift, 2)
    result["levels_in"] = "cfd" if shift else src_type
    if scale != 1.0:
        result["cfd_scale"] = round(scale, 6)
    if src_type == "etf":
        # Levels stay in ETF units; *_rel = level / last ETF close, so CFD level = *_rel x CFD price.
        for by in ("week", "month"):
            for k in ("poc", "vah", "val"):
                if result[by].get(k) is not None:
                    result[by][f"{k}_rel"] = round(result[by][k] / etf_close, 5)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="out")
    ap.add_argument("--cfd", nargs="*", default=[], help="MÃ=giá CFD, ví dụ NQ=24890")
    a = ap.parse_args(argv)
    out, data_dir = Path(a.out), Path(a.data)
    out.mkdir(parents=True, exist_ok=True)
    cfd = {k.upper(): float(v) for k, v in (x.split("=") for x in a.cfd)}

    combined = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbols": {}}
    ok = 0
    for s in a.symbols:
        s = s.upper()
        try:
            r = run_symbol(s, data_dir, out, cfd.get(s))
            combined["symbols"][s] = r
            ok += 1
            w = r["week"]
            print(f"{s:5s} tuần {w['period']}: POC {w['poc']}  VAH {w['vah']}  VAL {w['val']}  "
                  f"| giá {w['position']} | {w['regime']}")
        except Exception as e:  # keep going with the other symbols
            combined["symbols"][s] = {"error": str(e)}
            print(f"{s:5s} LỖI: {e}", file=sys.stderr)
            if not isinstance(e, RuntimeError):
                traceback.print_exc(limit=2)
    (out / "levels_latest.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2, default=str),
                                            encoding="utf-8")
    print(f"\nĐã ghi {out.resolve()}/levels_latest.json ({ok}/{len(a.symbols)} mã thành công)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
