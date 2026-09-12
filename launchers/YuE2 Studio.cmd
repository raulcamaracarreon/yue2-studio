@echo off
setlocal EnableExtensions
title YuE2 Studio

set "DISTRO=%YUE2_STUDIO_WSL_DISTRO%"
if "%DISTRO%"=="" set "DISTRO=Ubuntu-24.04"

set "PROJECT=%YUE2_STUDIO_WSL_PROJECT%"
if "%PROJECT%"=="" set "PROJECT=~/projects/yue2-studio"

set "PYTHON=%YUE2_STUDIO_WSL_PYTHON%"
if "%PYTHON%"=="" set "PYTHON=~/venvs/yue2/bin/python"

set "PORT=%YUE2_STUDIO_PORT%"
if "%PORT%"=="" set "PORT=7860"

set "URL=http://127.0.0.1:%PORT%"

curl.exe -s --max-time 1 "%URL%/" >nul 2>&1
if not errorlevel 1 goto OPEN_BROWSER

start "Servidor YuE2 Studio" /min wsl.exe -d %DISTRO% bash -lc "cd %PROJECT% && exec %PYTHON% app.py"

for /L %%I in (1,1,90) do (
    timeout /t 1 /nobreak >nul
    curl.exe -s --max-time 1 "%URL%/" >nul 2>&1
    if not errorlevel 1 goto OPEN_BROWSER
)

echo.
echo ERROR: YuE2 Studio no respondió en %URL%.
echo Revisa la ventana minimizada "Servidor YuE2 Studio" para ver el error de Python.
pause
exit /b 1

:OPEN_BROWSER
start "" "%URL%/"
exit /b 0
