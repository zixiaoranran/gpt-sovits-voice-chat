@echo off
rem ==============================================================
rem  ONE-CLICK DEPLOY  -  install deps + auto config + launch
rem  Double-click this file. That's it.
rem ==============================================================
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

if exist "%~dp0runtime\python.exe" (
  set "PY=%~dp0runtime\python.exe"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo.
    echo [FAIL] No Python found.
    echo.
    echo   runtime\python.exe is missing, and there is no "python" on PATH.
    echo   Two ways out:
    echo     1^) Use the GPT-SoVITS integrated package ^(it ships runtime\python.exe^)
    echo     2^) Install Python 3.10/3.11 ^(NOT 3.13+^), then run this file again
    echo.
    echo   Note: this project must be placed in the GPT-SoVITS root folder,
    echo         next to api_v2.py.
    echo.
    pause
    exit /b 1
  )
  set "PY=python"
  echo [WARN] runtime\python.exe not found - using "python" from PATH.
  echo        ^(If it is Python 3.13+, gradio 4.44.1 will not install.^)
)

:menu
echo.
echo ==============================================================
echo   AI Voice Chat WebUI  -  one-click deploy
echo   interpreter: %PY%
echo ==============================================================
echo   [1] Deploy and start   (recommended)
echo   [2] Deploy only        (no launch)
echo   [3] Check environment  (nothing is installed/written)
echo   [4] Install deps only
echo   [0] Exit
echo.
set "CH="
set /p CH=Choose [1]: 
if "%CH%"=="" set "CH=1"

if "%CH%"=="1" goto run
if "%CH%"=="2" goto depsonly_noRun
if "%CH%"=="3" goto check
if "%CH%"=="4" goto depsonly
if "%CH%"=="0" goto end
echo Invalid choice.
goto menu

:run
"%PY%" "%~dp0deploy.py" %*
goto done

:depend_only_noRun
"%PY%" "%~dp0deploy.py" --no-run %*
goto done

:check
"%PY%" "%~dp0deploy.py" --check-only %*
goto done

:dependonly
"%PY%" "%~dp0install_deps.py" %*
goto done

:done
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo [DONE]) else (echo [EXIT %RC%] - see messages above)
echo.
pause
exit /b %RC%

:end
endlocal
exit /b 0
