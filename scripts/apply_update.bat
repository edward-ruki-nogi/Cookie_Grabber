@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM %1 = target dir (install folder)
REM %2 = staging payload dir (extracted update)
REM %3 = exe to restart (full path)

set "TARGET=%~1"
set "STAGING=%~2"
set "EXE=%~3"
set "LOG=%TARGET%\.update_staging\apply_update.log"

if not exist "%TARGET%\.update_staging" mkdir "%TARGET%\.update_staging" 2>nul
echo === apply_update %DATE% %TIME% === > "%LOG%"
echo TARGET=%TARGET%>>"%LOG%"
echo STAGING=%STAGING%>>"%LOG%"
echo EXE=%EXE%>>"%LOG%"

if not exist "%STAGING%\CookieGrabber.exe" (
  echo ERROR: payload missing CookieGrabber.exe >>"%LOG%"
  exit /b 1
)

echo Waiting for CookieGrabber.exe to exit...>>"%LOG%"
set /a WAIT=0
:wait_all
tasklist /FI "IMAGENAME eq CookieGrabber.exe" 2>nul | find /I "CookieGrabber.exe" >nul
if !ERRORLEVEL!==0 (
  set /a WAIT+=1
  if !WAIT! GEQ 180 (
    echo ERROR: timeout waiting for processes >>"%LOG%"
    exit /b 1
  )
  timeout /t 1 /nobreak >nul
  goto wait_all
)

echo All CookieGrabber.exe stopped.>>"%LOG%"
timeout /t 2 /nobreak >nul

robocopy "%STAGING%" "%TARGET%" /E /IS /IT /NFL /NDL /NJH /NJS /NP >>"%LOG%" 2>&1
set "RC=!ERRORLEVEL!"
echo robocopy exit code !RC!>>"%LOG%"
if !RC! GEQ 8 (
  echo ERROR: robocopy failed>>"%LOG%"
  exit /b 1
)

echo Starting %EXE%>>"%LOG%"
start "" "%EXE%"
echo Done.>>"%LOG%"
endlocal
exit /b 0
