[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    Write-Host "==> Python"
    python --version
    python -m pip install --upgrade pip
    python -m pip install -r requirements-build.txt

    Write-Host "==> PyInstaller"
    python -m PyInstaller KarenBot-Installer.spec --noconfirm --clean --log-level INFO

    $Exe = Join-Path $Root "dist\KarenBot-Installer.exe"
    if (-not (Test-Path $Exe)) {
        throw "PyInstaller did not produce $Exe"
    }
    $Item = Get-Item $Exe
    Write-Host ("Built {0} ({1:N1} MB)" -f $Item.FullName, ($Item.Length / 1MB))
} finally {
    Pop-Location
}
