@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   DD日推 · Onsite Club 会展监控
echo   每日 9:00 自动抓取并推送钉钉
echo   (Ctrl+C 停止)
echo ============================================
python dd_scheduler.py
pause
