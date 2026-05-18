@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM %1 target, %2 staging, %3 exe (new) OR %3 pid + %4 exe (legacy)

set "TARGET=%~1"
set "STAGING=%~2"
if not "%~4"=="" (
  set "EXE=%~4"
) else (
  set "EXE=%~3"
)
if not exist "%EXE%" set "EXE=%TARGET%\CookieGrabber.exe"

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

if not exist "%EXE%" (
  echo ERROR: exe not found: %EXE%>>"%LOG%"
  exit /b 1
)

echo Waiting for CookieGrabber.exe in %TARGET%...>>"%LOG%"
set /a WAIT=0
:wait_target
powershell -NoProfile -Command ^
  "$t='%TARGET%'; $n=@(Get-Process -Name CookieGrabber -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path.StartsWith($t, [System.StringComparison]::OrdinalIgnoreCase)) }); exit ($n.Count -gt 0 ? 0 : 1)"
if !ERRORLEVEL!==0 (
  set /a WAIT+=1
  if !WAIT! GEQ 120 (
    echo ERROR: timeout waiting for processes in target folder >>"%LOG%"
    exit /b 1
  )
  timeout /t 1 /nobreak >nul
  goto wait_target
)

echo No CookieGrabber.exe in target folder.>>"%LOG%"
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
