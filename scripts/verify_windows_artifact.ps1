[CmdletBinding()]
param(
    [string]$Exe = "dist\KarenBot-Installer.exe"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ExePath = if ([System.IO.Path]::IsPathRooted($Exe)) { $Exe } else { Join-Path $Root $Exe }
if (-not (Test-Path $ExePath)) {
    throw "Installer binary not found: $ExePath"
}

$Item = Get-Item $ExePath
if ($Item.Length -lt 5MB) {
    throw ("Installer is unexpectedly small: {0} bytes" -f $Item.Length)
}

# PE header: machine 0x8664 = x86-64.  This catches accidental 32-bit builds.
$Bytes = [System.IO.File]::ReadAllBytes($ExePath)
$PeOffset = [BitConverter]::ToInt32($Bytes, 0x3C)
$Machine = [BitConverter]::ToUInt16($Bytes, $PeOffset + 4)
if ($Machine -ne 0x8664) {
    throw ("Expected x86-64 PE machine 0x8664, got 0x{0:X4}" -f $Machine)
}

$Version = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($ExePath)
$VersionFile = Join-Path $Root "VERSION"
$ExpectedVersion = (Get-Content -LiteralPath $VersionFile -Raw).Trim()
if ($Version.FileVersion -ne $ExpectedVersion -or $Version.ProductVersion -ne $ExpectedVersion) {
    throw (
        "Artifact version mismatch: expected {0}, FileVersion={1}, ProductVersion={2}" -f
        $ExpectedVersion, $Version.FileVersion, $Version.ProductVersion
    )
}
Write-Host ("PE machine: 0x{0:X4} (x86-64)" -f $Machine)
Write-Host ("FileVersion: {0}; ProductVersion: {1}" -f $Version.FileVersion, $Version.ProductVersion)
Write-Host ("Size: {0:N1} MB" -f ($Item.Length / 1MB))

Write-Host "==> --check"
$Check = Start-Process -FilePath $ExePath -ArgumentList "--check" -Wait -PassThru
if ($Check.ExitCode -ne 0) {
    throw "--check failed with exit code $($Check.ExitCode)"
}

$Hash = (Get-FileHash -Algorithm SHA256 $ExePath).Hash.ToLower()
$HashFile = Join-Path $Root "dist\SHA256SUMS-windows.txt"
"$Hash  KarenBot-Installer.exe" | Out-File -Encoding ascii $HashFile
Write-Host "SHA256: $Hash"
