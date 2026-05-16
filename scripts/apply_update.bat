@echo off
setlocal EnableExtensions
REM %1 = target dir (install folder)
REM %2 = staging payload dir (extracted update)
REM %3 = PID to wait for
REM %4 = exe to restart

set "TARGET=%~1"
set "STAGING=%~2"
set "PID=%~3"
set "EXE=%~4"

if not exist "%STAGING%\CookieGrabber.exe" (
  echo Payload missing CookieGrabber.exe in %STAGING%
  exit /b 1
)

:wait_loop
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if %ERRORLEVEL%==0 (
  timeout /t 1 /nobreak >nul
  goto wait_loop
)

timeout /t 2 /nobreak >nul

robocopy "%STAGING%" "%TARGET%" /E /IS /IT /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo robocopy failed with code %ERRORLEVEL%
  exit /b 1
)

start "" "%EXE%"

endlocal
exit /b 0
