@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=C:\Users\zhuge\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo   ============================================
echo     赤舟作文批改工作台
echo     3 秒后自动打开浏览器...
echo     关闭这个窗口 = 停止服务
echo   ============================================
echo.

start "" cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8790"
"%PY%" server.py

echo.
echo Server stopped.
pause
