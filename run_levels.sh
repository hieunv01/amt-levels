#!/usr/bin/env bash
# Chạy run_levels.py bằng Python trong .venv, ghi log vào out/run.log
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
mkdir -p out
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
echo "==== $(date '+%F %T') ====" >> out/run.log
"$PY" run_levels.py "$@" >> out/run.log 2>&1
