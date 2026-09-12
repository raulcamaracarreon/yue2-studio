@echo off
setlocal EnableExtensions
set "DISTRO=%YUE2_STUDIO_WSL_DISTRO%"
if "%DISTRO%"=="" set "DISTRO=Ubuntu-24.04"
wsl.exe -d %DISTRO% bash -lc "pkill -f '[p]ython.*app.py' 2>/dev/null || true"
exit /b 0
