@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Activar avisos de Telegram - SportsDB
cd /d "%~dp0"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "scripts\configurar_telegram.py" --visible
echo.
echo Pulsa una tecla para cerrar esta ventana.
pause >nul
