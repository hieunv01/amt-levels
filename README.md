# amt_profile

Module Python tính **volume profile** và **TPO (Market Profile)** theo ngày, tuần hoặc tháng cho hợp đồng tương lai chỉ số. Từ đó module đọc tín hiệu theo **thuyết đấu giá thị trường (AMT)**:

- POC, VAH, VAL (vùng giá trị 70% theo cách tính của CBOT), HVN/LVN và hình dạng profile (D, P, b, B, elongated)
- Vị trí giá so với vùng giá trị: trên (đắt), trong (công bằng), dưới (rẻ)
- Giá trị dịch chuyển giữa hai kỳ (7 trường hợp của Dalton) và trạng thái cân bằng/mất cân bằng
- Chấp nhận (nhiều phiên đóng cửa ngoài vùng giá trị) hay bác bỏ
- Failed auction (thủng VAH/VAL rồi đóng cửa quay lại, tức false breakout)
- Chất lượng đỉnh/đáy theo TPO: poor (phẳng, chưa xong) hay excess (có đuôi, đã bị từ chối)
- Bộ feature hằng ngày **không look-ahead** để đưa vào meta-labeling hoặc regime classifier

```
pip install -e .            # numpy, pandas, matplotlib
pip install -e ".[yfinance]"   # nếu muốn tải dữ liệu Yahoo
pip install -e ".[mt5]"        # nếu lấy dữ liệu từ MetaTrader 5 (Windows)
pytest -q                      # 28 test, dữ liệu tổng hợp, không cần mạng
```

## Chạy nhanh bằng dòng lệnh

```bash
# NQ: profile tuần từ nến 30 phút của Yahoo, dịch mức giá sang CFD NAS100 đang ở 24890
python -m amt_profile --source yfinance --symbol NQ --by week --cfd-price 24890 \
       --json nq_week.json --plot nq_week.png

# NQ: profile tháng (tự dùng nến 1h, khoảng 2 năm)
python -m amt_profile --source yfinance --symbol NQ --by month --last 6

# FDAX: file CSV nến 1 phút của bạn, chỉ phiên ngày, profile tháng
python -m amt_profile --source csv --file fdax_1m.csv --file-tz Europe/Berlin --symbol FDAX --by month --rth

# Dữ liệu tick (cột price,size) từ pipeline footprint: profile chính xác
python -m amt_profile --source csv --file nkd_ticks.parquet --symbol NKD --by week

# TPO (thời gian tại giá) thay vì volume, từ nến 30 phút
python -m amt_profile --source csv --file es_30m.csv --symbol ES --by week --kind tpo

# Từ MT5 (CFD của broker): chỉ có tick volume, xem phần cảnh báo
python -m amt_profile --source mt5 --symbol GER40 --interval M5 --count 60000 --by week
```

Đầu ra (ví dụ trên dữ liệu tổng hợp):

```
FDAX volume profile by week  row=2.0  tz=Europe/Berlin roll=0  levels shifted by +206.62 to CFD
               period done      POC      VAH      VAL     high      low shape migration    hiQ    loQ
2026-11-02/2026-11-08  yes 25183.62 25224.62 25154.62 25275.44 25101.54     D     lower normal excess
2026-11-09/2026-11-15  dev 25049.62 25106.62 25032.62 25175.39 24975.23     D     lower   poor excess

regime: imbalance_down | last close 25010.0 (CFD) is below last completed value (POC dist -1.56 ATR)
| outside run: 6 below (accepted) | failed auction today: None
```

- `done = dev`: kỳ đang hình thành (developing). Tín hiệu luôn so với **kỳ gần nhất đã hoàn tất**.
- `--json` ghi toàn bộ mức giá cùng phần tóm tắt, `--csv` ghi bảng, `--plot` vẽ PNG.

## Chạy tự động mỗi ngày

`run_levels.py` tính profile tuần và tháng cho nhiều mã (mặc định NQ, ES, NKD, YM, RTY, FDAX, FESX, Z, HSI, SPI, KOSPI, A50, HSCEI, HSTECH, CSI300) rồi ghi vào `out/`: file `<MÃ>_week.json`, `<MÃ>_month.json`, ảnh `<MÃ>_week.png`, và `levels_latest.json` gộp tất cả. Log ghi ở `out/run.log`.

**Windows**: mở PowerShell trong thư mục module và chạy một lần:
```powershell
powershell -ExecutionPolicy Bypass -File setup_windows.ps1
```
Script sẽ tạo `.venv`, cài thư viện, chạy thử, rồi đăng ký task "AMT Levels" trong Task Scheduler. Task chạy lúc 05:10, 14:10 và 18:10 từ thứ Hai đến thứ Sáu, có đánh thức máy nếu đang ngủ và chạy bù nếu lỡ giờ vì máy tắt. Muốn chạy tay: `Start-ScheduledTask -TaskName 'AMT Levels'`.

**macOS / Linux**:
```bash
bash setup_unix.sh
```
Script thêm 3 dòng cron (đánh dấu `# amt_levels`), giữ nguyên các dòng cron khác, chạy lại cũng không bị trùng. Lưu ý: cron không đánh thức máy đang ngủ.

Muốn đổi giờ hoặc danh sách mã: sửa `$times` trong `setup_windows.ps1` (hoặc `TIMES` trong `setup_unix.sh`), hoặc thêm tham số vào `run_levels.bat`/`run_levels.sh`, ví dụ `--symbols NQ ES NKD FDAX`. Mã không có trên Yahoo thì đặt file `data/<MÃ>.csv`.

Nguồn dữ liệu Yahoo theo từng mã (cột `kind` và `levels_in` trong JSON cho biết đang dùng loại nào):

| Mã | CFD | Yahoo | Profile |
|---|---|---|---|
| ES, NQ, YM, RTY, NKD | US500, NAS100, US30, US2000, JP225 | futures CME (`ES=F`…) | volume thật |
| KOSPI, CSI300 | KOSPI, CHINA300 | `^KS11`, `000300.SS` (cash) | volume thật |
| FDAX, FESX, Z, HSI, SPI | GER40, EU50, UK100, HK50, AUS200 | `^GDAXI`, `^STOXX50E`, `^FTSE`, `^HSI`, `^AXJO` (cash) | TPO, vì Yahoo trả volume = 0 |
| HSCEI, HSTECH | CHINAH, HKTECH | `^HSCE`, `HSTECH.HK` (cash) | TPO, vì Yahoo trả volume = 0 |
| A50 | CHINA50 | ETF `2823.HK` (iShares FTSE A50, HKD) | volume thật của ETF |

Với mã cash, mức giá là của chỉ số cash (khớp với CFD cash hơn là futures). Profile tháng dùng nến 1h nên TPO tháng thô hơn TPO tuần (nến 30 phút). File futures trong `data/` luôn được ưu tiên và tính bằng volume.

**A50 qua ETF**: Yahoo không có nến intraday của chỉ số FTSE China A50 nên dùng ETF 2823.HK. Giá ETF chỉ bằng khoảng 1/900 chỉ số, nên mức giá phải quy đổi theo tỷ lệ chứ không cộng chênh lệch:
- Mặc định `levels_in = "etf"`: POC/VAH/VAL tính bằng HKD của ETF, kèm `poc_rel`, `vah_rel`, `val_rel` = mức / giá đóng cửa ETF. Mức CFD = `*_rel` × giá CHINA50 hiện tại.
- `--cfd A50=13050`: nhân toàn bộ nến ETF với 13050 / giá đóng cửa ETF, nên profile và ảnh ra thẳng đơn vị CFD (`levels_in = "cfd"`, hệ số ở `cfd_scale`).
- Tỷ lệ ETF/chỉ số trôi dần theo phí quỹ, cổ tức và tỷ giá CNY/HKD, nên profile tháng cũ lệch nhiều hơn profile tuần.

## Dùng trong code

```python
from amt_profile import (load_csv, preset, profiles_by_period, daily_bars,
                         summary, amt_features, composite_profile)

p = preset("FDAX")                              # tick, tz, roll_hour, rth, CFD tương ứng
bars = load_csv("fdax_1m.csv", tz="Europe/Berlin")

weekly  = profiles_by_period(bars, by="week",  tick=p["tick"], tz=p["tz"], roll_hour=p["roll_hour"])
monthly = profiles_by_period(bars, by="month", tick=p["tick"], tz=p["tz"], roll_hour=p["roll_hour"])
daily   = daily_bars(bars, p["tz"], p["roll_hour"])

ref = [w for w in weekly if w.complete][-1]
print(ref.poc, ref.vah, ref.val, ref.meta["shape"], ref.meta["migration"],
      ref.meta["high_quality"], ref.meta["low_quality"], ref.meta["hvn"], ref.meta["lvn"])

snap  = summary(weekly, daily)                 # vị trí giá, chấp nhận, failed auction hôm nay
feats = amt_features(daily, weekly, monthly)   # DataFrame feature theo ngày

# Một profile cho vùng cân bằng tùy chọn (ví dụ từ đáy 8/10 đến nay)
bal = composite_profile(bars, start="2026-10-08", end=None, tick=p["tick"])

# Dịch mức giá futures sang CFD
cfd_ref = ref.shifted(25010 - daily["close"].iloc[-1])
```

### Các feature của `amt_features`

Tiền tố `w_` là so với **tuần trước**, `m_` là so với **tháng trước**. Cả hai chỉ dùng kỳ đã hoàn tất.

| cột | ý nghĩa |
|---|---|
| `pos` | +1 trên vùng giá trị, 0 trong, −1 dưới |
| `d_poc_atr`, `d_vah_atr`, `d_val_atr` | khoảng cách từ giá đóng cửa tới POC/VAH/VAL, tính theo ATR(14) |
| `va_width_atr` | độ rộng vùng giá trị theo ATR (hẹp = đang tích lũy cân bằng) |
| `migration` | giá trị kỳ tham chiếu dịch chuyển so với kỳ trước nó: −2…+2 |
| `outside_run` | số phiên đóng cửa liên tiếp ngoài vùng giá trị (+ phía trên, − phía dưới) |
| `accepted` | 1 nếu outside_run ≥ 2 (giá mới đã được chấp nhận) |
| `failed` | +1 failed auction dưới VAL (tín hiệu tăng), −1 trên VAH (tín hiệu giảm) |
| `shape`, `hiq`, `loq` | hình dạng profile, chất lượng đỉnh/đáy (poor=1, excess=−1) |

Gợi ý dùng: đưa `w_failed`, `w_pos`, `w_accepted`, `w_d_poc_atr`, `m_migration` vào meta-model của chiến lược false breakout fade. Đưa `w_migration`, `m_migration` và `va_width_atr` vào regime classifier để phân biệt cân bằng với mất cân bằng.

## Nguồn dữ liệu và cảnh báo

- **Dùng volume của futures, không dùng của CFD.** Volume của CFD trên MT5 (`tick_volume`) chỉ đếm số lần giá thay đổi, không phải số hợp đồng. Dùng nó cho TPO thì được, cho volume profile thì chỉ ước lượng thô. Cách nên làm: tính vùng giá trị từ futures (FDAX, NQ, ES, NKD...), rồi dịch mức giá sang CFD bằng `--cfd-price` hoặc `Profile.shifted(basis)`. Basis thay đổi theo cổ tức, lãi suất và kỳ đáo hạn, nên tính lại mỗi lần chạy và lấy hai giá ở cùng một thời điểm.
- **Yahoo (`=F`) là hợp đồng gần nhất nối liền, không điều chỉnh.** Ngày chuyển kỳ hạn sẽ có khoảng nhảy giá. Profile của tuần có ngày chuyển kỳ hạn nên xem với dè dặt.
- **Giới hạn lịch sử của yfinance:** nến 1m khoảng 7 ngày, 5m/15m/30m khoảng 60 ngày, 1h khoảng 730 ngày. Profile tuần nên dùng 30m hoặc mịn hơn, profile tháng dùng 1h.
- **Ticker chỉ số của Yahoo** (^GDAXI, ^N225…) thường có volume bằng 0 hoặc lúc có lúc không. Module báo lỗi nếu không có volume.
- **Futures châu Âu và châu Á** (FDAX, FESX, HSI, SPI, A50) không có trên Yahoo. Hãy xuất dữ liệu từ nguồn bạn có (Bookmap, Rithmic, CQG, IB, broker) ra CSV hoặc Parquet với các cột `time, open, high, low, close, volume`, hoặc `time, price, size` cho tick.
- **Bước giá (tick)** của HSI, SPI và A50 để trống trong preset và được tự suy ra từ dữ liệu. Nên kiểm tra lại với đặc tả của sàn rồi truyền vào `--tick`.

## Quy ước và thuật toán

- **Lưới giá chung:** hàng thứ i chứa các giá trong khoảng `[i·row, (i+1)·row)`. Mọi kỳ dùng chung một lưới nên các mức giá so sánh được với nhau. Nếu không truyền `--row-size`, module tự chọn kích thước "đẹp" (bội số của tick) sao cho được khoảng 120 hàng theo biên độ trung vị của mỗi kỳ.
- **Phân bổ volume của một nến:** mặc định chia đều từ low tới high (`method="uniform"`). Ngoài ra có thể dồn hết vào giá đóng cửa (`close`) hoặc vào giá điển hình (`typical`). Dữ liệu tick thì chính xác tuyệt đối.
- **POC** là hàng có nhiều volume nhất. Nếu bằng nhau, chọn hàng gần giữa biên độ nhất.
- **Vùng giá trị (CBOT):** bắt đầu từ POC, mỗi bước so sánh tổng 2 hàng phía trên với 2 hàng phía dưới, cộng phía lớn hơn vào (bằng nhau thì chọn phía trên), lặp lại cho tới khi đủ 70%.
- **Chất lượng đỉnh/đáy** được đánh giá bằng TPO 30 phút, kể cả khi profile chính là volume. Đáy hoặc đỉnh bị chạm bởi từ 2 kỳ TPO trở lên (phẳng) là `poor`. Có đuôi từ 2 hàng trở lên chỉ có 1 TPO là `excess`.
- **Giờ chuyển phiên (`roll_hour`):** với CME là 17 giờ Chicago (nến Chủ nhật 18:00 tính cho thứ Hai), với Eurex là 0. Tuần tính từ thứ Hai đến Chủ nhật.
- **Kỳ cuối cùng** luôn được đánh dấu là đang hình thành, trừ khi dùng `--last-complete`.

## Giới hạn cần biết

- Volume profile dựng từ nến OHLC là xấp xỉ: nến càng mịn càng chính xác, dữ liệu tick là chuẩn nhất.
- Hình dạng profile và HVN/LVN là quy tắc kinh nghiệm để mô tả, không phải tín hiệu đã được kiểm chứng. Nên backtest các feature trong hệ thống của bạn trước khi dùng để lọc lệnh.
- Con số "80%" của quy tắc 80% trong Market Profile không được đưa vào module vì chưa thấy kiểm chứng độc lập.

## Cấu trúc

```
amt_profile/
  profile.py   phân bổ volume, POC, vùng giá trị, HVN/LVN, hình dạng, chất lượng cực trị, TPO
  periods.py   ngày giao dịch theo múi giờ và giờ chuyển phiên, profile theo kỳ, nến ngày, profile tùy chọn
  signals.py   vị trí, dịch chuyển giá trị, cân bằng, chấp nhận, failed auction, tóm tắt, feature ML
  data.py      preset hợp đồng, đọc CSV/Parquet, yfinance, MT5, basis
  plot.py      vẽ profile cạnh nhau bằng matplotlib
  cli.py       dòng lệnh (python -m amt_profile)
examples/build_features.py   ví dụ ghép feature vào tín hiệu
tests/                       25 test với dữ liệu tổng hợp
```
