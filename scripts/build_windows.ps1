[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $Exe = Join-Path $Root "dist\KarenBot-Installer.exe"
    if (Test-Path $Exe) {
        Remove-Item -LiteralPath $Exe -Force
    }

    Write-Host "==> Python"
    python --version
    if ($LASTEXITCODE -ne 0) { throw "python --version failed" }
    python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    python -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw "build dependency installation failed" }

    Write-Host "==> PyInstaller"
    python -m PyInstaller KarenBot-Installer.spec --noconfirm --clean --log-level INFO
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    if (-not (Test-Path $Exe)) {
        throw "PyInstaller did not produce $Exe"
    }
    $Item = Get-Item $Exe
    Write-Host ("Built {0} ({1:N1} MB)" -f $Item.FullName, ($Item.Length / 1MB))
} finally {
    Pop-Location
}
