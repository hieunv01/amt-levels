#!/usr/bin/env bash
# Cài đặt một lần trên macOS/Linux: tạo .venv, cài thư viện, chạy thử, thêm lịch cron.
# Chạy:  bash setup_unix.sh
# Giờ chạy theo giờ của máy; đổi ở biến TIMES ("phút giờ").
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
TIMES=("10 5" "10 14" "10 18")
MARK="# amt_levels"

echo "1/4 Tạo môi trường Python (.venv)..."
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q -U pip
.venv/bin/python -m pip install -q -e ".[yfinance]"
chmod +x run_levels.sh

echo "2/4 Chạy thử..."
mkdir -p out
.venv/bin/python run_levels.py || echo "Lần chạy thử có lỗi, xem thông báo ở trên."

echo "3/4 Thêm lịch cron (thứ Hai đến thứ Sáu)..."
{
  crontab -l 2>/dev/null | grep -v "$MARK" || true
  for t in "${TIMES[@]}"; do echo "$t * * 1-5 \"$DIR/run_levels.sh\" $MARK"; done
} | crontab -

echo "4/4 Xong. Lịch hiện tại:"
crontab -l | grep "$MARK"
echo "Kết quả: $DIR/out/levels_latest.json   Log: $DIR/out/run.log"
echo "Gỡ lịch: crontab -l | grep -v '$MARK' | crontab -"
echo "Lưu ý macOS: cron không đánh thức máy đang ngủ; có thể cần cấp Full Disk Access cho cron."
