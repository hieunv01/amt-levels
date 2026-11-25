# Cài đặt một lần trên Windows: tạo .venv, cài thư viện, chạy thử, đăng ký lịch tự chạy.
# Chạy:  powershell -ExecutionPolicy Bypass -File setup_windows.ps1
# Đổi giờ chạy ở dòng $times (giờ của máy, ví dụ giờ Việt Nam).

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

Write-Host "1/4 Tạo môi trường Python (.venv)..."
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -q -U pip
& .\.venv\Scripts\python.exe -m pip install -q -e ".[yfinance]"

Write-Host "2/4 Chạy thử..."
New-Item -ItemType Directory -Force out | Out-Null
& .\.venv\Scripts\python.exe run_levels.py
if ($LASTEXITCODE -ne 0) { Write-Warning "Lần chạy thử có lỗi, xem thông báo ở trên." }

Write-Host "3/4 Đăng ký lịch tự chạy (thứ Hai đến thứ Sáu)..."
$times = @("05:10", "14:10", "18:10")
$action = New-ScheduledTaskAction -Execute "$dir\run_levels.bat" -WorkingDirectory $dir
$days = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
$triggers = $times | ForEach-Object { New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $_ }
# WakeToRun: đánh thức máy nếu đang ngủ; StartWhenAvailable: chạy bù nếu lỡ giờ vì máy tắt
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "AMT Levels" -Action $action -Trigger $triggers -Settings $settings `
    -Description "Tính POC/VAH/VAL tuần, tháng -> out\levels_latest.json" -Force | Out-Null

Write-Host "4/4 Xong. Lịch: $($times -join ', ') thứ Hai - thứ Sáu."
Write-Host "Kết quả: $dir\out\levels_latest.json   Log: $dir\out\run.log"
Write-Host "Chạy tay bất cứ lúc nào:  Start-ScheduledTask -TaskName 'AMT Levels'"
Write-Host "Gỡ lịch:                  Unregister-ScheduledTask -TaskName 'AMT Levels' -Confirm:`$false"
