@echo off
rem Chạy run_levels.py bằng Python trong .venv, ghi log vào out\run.log
cd /d "%~dp0"
if not exist out mkdir out
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
echo ==== %date% %time% ==== >> out\run.log
"%PY%" run_levels.py %* >> out\run.log 2>&1
