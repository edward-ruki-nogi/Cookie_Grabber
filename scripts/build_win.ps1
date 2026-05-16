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

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $DistDir -DestinationPath $ZipPath -Force

Write-Host "Готово: $DistDir"
Write-Host "Артефакт релиза: $ZipPath"
