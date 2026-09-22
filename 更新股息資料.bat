@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在更新 TWSE / TPEx 官方除權息資料...
py -3 update_dividends.py 2>nul || python update_dividends.py
if errorlevel 1 (
  echo.
  echo 更新失敗，請查看 logs\update.log。
) else (
  echo.
  echo 更新完成。
)
pause
