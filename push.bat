@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   DD日推 · 一键提交并推送到 GitHub
echo   (git add -A  →  git commit  →  git push)
echo ============================================
echo.
set /p msg=请输入提交说明（直接回车用时间戳）:
if "%msg%"=="" set msg=update %date:~0,10% %time:~0,5%

git add -A
git commit -m "%msg%"
if errorlevel 1 (
  echo.
  echo [提示] 没有可提交的改动，或提交失败，已跳过推送。
  pause
  exit /b 1
)

git push
if errorlevel 1 (
  echo.
  echo [错误] 推送失败，请检查网络/远端后重试。
  pause
  exit /b 1
)

echo.
echo [完成] 已推送到 GitHub。
pause
