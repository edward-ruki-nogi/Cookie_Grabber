@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM apply_update v4 — короткое ожидание PID, затем принудительно гасим все exe в TARGET (в т.ч. консоль-хост)

set "TARGET=%~1"
set "STAGING=%~2"
if not "%~4"=="" (set "EXE=%~4") else (set "EXE=%~3")
if not exist "%EXE%" set "EXE=%TARGET%\CookieGrabber.exe"
if not "%~5"=="" (set "PIDFILE=%~5") else (set "PIDFILE=%TARGET%\.update_staging\wait_pids.txt")

set "LOG=%TARGET%\.update_staging\apply_update.log"
if not exist "%TARGET%\.update_staging" mkdir "%TARGET%\.update_staging" 2>nul
echo === apply_update v4 %DATE% %TIME% === > "%LOG%"
echo TARGET=%TARGET%>>"%LOG%"
echo STAGING=%STAGING%>>"%LOG%"
echo EXE=%EXE%>>"%LOG%"

if not exist "%STAGING%\CookieGrabber.exe" (
  echo ERROR: payload missing >>"%LOG%"
  exit /b 1
)

if exist "%PIDFILE%" (
  echo Graceful wait PIDs max 8s >>"%LOG%"
  type "%PIDFILE%">>"%LOG%"
  set /a WAIT=0
  :wait_pids
  set "ANY=0"
  for /f "usebackq delims=" %%p in ("%PIDFILE%") do (
    if not "%%p"=="" tasklist /FI "PID eq %%p" 2>nul | find "%%p" >nul && set "ANY=1"
  )
  if "!ANY!"=="1" (
    set /a WAIT+=1
    if !WAIT! LSS 8 (
      timeout /t 1 /nobreak >nul
      goto wait_pids
    )
  )
)

call :kill_target
timeout /t 2 /nobreak >nul

robocopy "%STAGING%" "%TARGET%" /E /IS /IT /NFL /NDL /NJH /NJS /NP >>"%LOG%" 2>&1
set "RC=!ERRORLEVEL!"
echo robocopy !RC!>>"%LOG%"
if !RC! GEQ 8 exit /b 1

echo Starting %EXE%>>"%LOG%"
start "" "%EXE%"
echo Done.>>"%LOG%"
exit /b 0

:kill_target
echo kill_target >>"%LOG%"
set "LIKE=%TARGET:\=\\%"
wmic process where "ExecutablePath like '%LIKE%%%'" call terminate >>"%LOG%" 2>&1
timeout /t 1 /nobreak >nul
wmic process where "ExecutablePath like '%LIKE%%%'" call terminate >>"%LOG%" 2>&1
goto :eof
