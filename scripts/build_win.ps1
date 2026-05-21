# Сборка Windows onedir и zip для GitHub Release.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==> pip install -e .[build]"
python -m pip install -e ".[build]"

Write-Host "==> playwright install chromium"
python -m playwright install chromium

Write-Host "==> pyinstaller"
python -m PyInstaller --noconfirm CookieGrabber.spec

$DistDir = Join-Path $Root "dist\CookieGrabber"
$ApplyBat = Join-Path $Root "scripts\apply_update.bat"
Copy-Item -Force $ApplyBat (Join-Path $DistDir "apply_update.bat")

$Version = (python -c "from importlib.metadata import version; print(version('cookie-grabber'))").Trim()
$ZipName = "CookieGrabber-$Version-win64.zip"
$ZipPath = Join-Path $Root "dist\$ZipName"

# Копия для zip во TEMP — иначе блокировка, если CookieGrabber запущен из dist\
$StageRoot = Join-Path $env:TEMP "CookieGrabber-zip-stage"
$WrapDir = Join-Path $StageRoot "CookieGrabber"
if (Test-Path $StageRoot) { Remove-Item -Recurse -Force $StageRoot }
New-Item -ItemType Directory -Force -Path $WrapDir | Out-Null
robocopy $DistDir $WrapDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy stage failed: $LASTEXITCODE" }

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $WrapDir -DestinationPath $ZipPath -CompressionLevel Optimal -Force
Remove-Item -Recurse -Force $StageRoot

Write-Host "Готово: $DistDir"
Write-Host "Артефакт релиза: $ZipPath"
