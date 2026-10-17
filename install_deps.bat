@echo off
rem ==============================================================
rem  One-click dependency installer for AI Voice Chat WebUI
rem  Double-click: core deps only
rem  With ASR:     install_deps.bat --asr
rem  Mirror:       install_deps.bat --asr --mirror tsinghua
rem ==============================================================
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

if exist "%~dp0runtime\python.exe" (
  set "PY=%~dp0runtime\python.exe"
) else (
  set "PY=python"
  echo [WARN] runtime\python.exe not found - falling back to "python" on PATH.
)

echo ==============================================================
echo  Using interpreter: %PY%
echo ==============================================================
"%PY%" "%~dp0install_deps.py" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [DONE] Dependencies are ready.
) else (
  echo [FAILED] Exit code %RC% - see the messages above.
)
echo.
pause
exit /b %RC%
