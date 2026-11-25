"""Ví dụ: tính feature AMT theo tuần và tháng cho NQ, rồi ghép vào tín hiệu của hệ thống.

Chạy:  python examples/build_features.py
Cần:   pip install yfinance
"""
from amt_profile import amt_features, daily_bars, load_yfinance, preset, profiles_by_period, summary

SYMBOL = "NQ"
p = preset(SYMBOL)
common = dict(tick=p["tick"], tz=p["tz"], roll_hour=p["roll_hour"])

bars_30m = load_yfinance(SYMBOL, "30m")   # ~60 ngày: đủ cho profile tuần
bars_1h = load_yfinance(SYMBOL, "1h")     # ~730 ngày: đủ cho profile tháng

weekly = profiles_by_period(bars_30m, by="week", **common)
monthly = profiles_by_period(bars_1h, by="month", **common)
daily = daily_bars(bars_1h, p["tz"], p["roll_hour"])

# Feature mỗi ngày, chỉ dùng tuần/tháng TRƯỚC đã hoàn tất -> không look-ahead
feats = amt_features(daily, weekly, monthly)
print(feats.tail(10).round(2).to_string())

# Ghép vào tín hiệu false-breakout của bạn (index = ngày giao dịch):
# signals = engine.generate_signals(...)
# X = signals.join(feats, how="left")
# meta_model.fit(X[feature_cols], y)

print(summary(weekly, daily))
