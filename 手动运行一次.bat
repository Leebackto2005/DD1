@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   DD日推 · 立即执行一次监控并推送钉钉
echo ============================================
python dd_main.py --once
pause
