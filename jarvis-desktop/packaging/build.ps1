# Build Jarvis Desktop and the Windows installer.
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Output: dist\Jarvis\Jarvis.exe and dist\JarvisSetup-<version>.exe
# Requires: Python 3.10-3.13 and Inno Setup 6 (https://jrsoftware.org/isinfo.php).

param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

try {
    if (-not $Version) {
        $init = Get-Content "jarvis_desktop\__init__.py" -Raw
        if ($init -match '__version__\s*=\s*"([^"]+)"') {
            $Version = $Matches[1]
        } else {
            throw "Could not read __version__ from jarvis_desktop\__init__.py"
        }
    }
    Write-Host "Building Jarvis $Version"

    $python = "python"
    if (Get-Command py -ErrorAction SilentlyContinue) { $python = "py" }

    & $python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

    & $python -m pip install -e ".[dev,build]"
    if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

    & $python -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }

    & $python -m PyInstaller --clean --noconfirm packaging\jarvis-desktop.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    $isccCandidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        throw "Inno Setup 6 not found. Install it from https://jrsoftware.org/isinfo.php and rerun."
    }

    & $iscc "/DAppVersion=$Version" "packaging\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

    Write-Host ""
    Write-Host "App:       dist\Jarvis\Jarvis.exe"
    Write-Host "Installer: dist\JarvisSetup-$Version.exe"
} finally {
    Pop-Location
}
