@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "http://localhost:8765/"
echo 台股股息月曆已啟動：http://localhost:8765/
echo 關閉此視窗即可停止服務。
py -3 -m http.server 8765 2>nul || python -m http.server 8765
