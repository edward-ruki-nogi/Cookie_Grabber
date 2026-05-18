@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM apply_update v3 — ждём только PID из wait_pids.txt, не все CookieGrabber в системе
REM %1=TARGET %2=STAGING %3=EXE (или legacy PID) %4=EXE legacy %5=PIDFILE

set "TARGET=%~1"
set "STAGING=%~2"
if not "%~4"=="" (
  set "EXE=%~4"
) else (
  set "EXE=%~3"
)
if not exist "%EXE%" set "EXE=%TARGET%\CookieGrabber.exe"

if not "%~5"=="" (
  set "PIDFILE=%~5"
) else (
  set "PIDFILE=%TARGET%\.update_staging\wait_pids.txt"
)

set "LOG=%TARGET%\.update_staging\apply_update.log"
if not exist "%TARGET%\.update_staging" mkdir "%TARGET%\.update_staging" 2>nul
echo === apply_update v3 %DATE% %TIME% === > "%LOG%"
echo TARGET=%TARGET%>>"%LOG%"
echo STAGING=%STAGING%>>"%LOG%"
echo EXE=%EXE%>>"%LOG%"
echo PIDFILE=%PIDFILE%>>"%LOG%"

if not exist "%STAGING%\CookieGrabber.exe" (
  echo ERROR: payload missing CookieGrabber.exe >>"%LOG%"
  exit /b 1
)
if not exist "%EXE%" (
  echo ERROR: exe not found >>"%LOG%"
  exit /b 1
)

if not exist "%PIDFILE%" (
  echo WARN: no pid file, fallback to folder wait >>"%LOG%"
  goto wait_folder
)

echo Waiting for PIDs in %PIDFILE%...>>"%LOG%"
type "%PIDFILE%">>"%LOG%"
set /a WAIT=0
:wait_pids
set "ANY=0"
for /f "usebackq delims=" %%p in ("%PIDFILE%") do (
  if not "%%p"=="" (
    tasklist /FI "PID eq %%p" 2>nul | find "%%p" >nul && set "ANY=1"
  )
)
if "!ANY!"=="1" (
  set /a WAIT+=1
  if !WAIT! GEQ 45 goto kill_folder
  timeout /t 1 /nobreak >nul
  goto wait_pids
)
echo PIDs finished.>>"%LOG%"
goto do_copy

:wait_folder
echo Waiting for CookieGrabber in %TARGET% only...>>"%LOG%"
set /a WAIT=0
:wf_loop
set "ANY=0"
for /f "skip=1 tokens=2 delims=," %%a in ('wmic process where "name='CookieGrabber.exe'" get ProcessId^,ExecutablePath /format:csv 2^>nul') do (
  echo %%a| findstr /i /b /c:"%TARGET%\" >nul && set "ANY=1"
)
if "!ANY!"=="1" (
  set /a WAIT+=1
  if !WAIT! GEQ 45 goto kill_folder
  timeout /t 1 /nobreak >nul
  goto wf_loop
)
goto do_copy

:kill_folder
echo Force-stop CookieGrabber under %TARGET%...>>"%LOG%"
powershell -NoProfile -Command "$t='%TARGET%'; Get-CimInstance Win32_Process -Filter \"Name='CookieGrabber.exe'\" -EA SilentlyContinue | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($t, 'OrdinalIgnoreCase') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }" >>"%LOG%" 2>&1
timeout /t 2 /nobreak >nul

:do_copy
robocopy "%STAGING%" "%TARGET%" /E /IS /IT /NFL /NDL /NJH /NJS /NP >>"%LOG%" 2>&1
set "RC=!ERRORLEVEL!"
echo robocopy exit code !RC!>>"%LOG%"
if !RC! GEQ 8 (
  echo ERROR: robocopy failed >>"%LOG%"
  exit /b 1
)

echo Starting %EXE%>>"%LOG%"
start "" "%EXE%"
echo Done.>>"%LOG%"
endlocal
exit /b 0
