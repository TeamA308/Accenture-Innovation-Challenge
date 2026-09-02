@echo off
REM ControlPlane.ai - one command to run everything on Windows.
setlocal
cd /d "%~dp0"
where py >nul 2>&1 && (py -3 run.py %*) || (python run.py %*)
endlocal
