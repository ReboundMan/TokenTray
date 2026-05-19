# build.ps1 - Build a standalone TokenTray.exe with PyInstaller.
#
# Usage:
#   .\build.ps1                # builds dist\TokenTray.exe using the local venv
#   .\build.ps1 -Clean         # nukes build\, dist\, and .spec first
#   .\build.ps1 -Python <exe>  # use a specific python.exe (e.g. for CI)
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# Pick a python: explicit arg > 3.12 build venv > 3.14 run venv > py launcher
# Note: PyInstaller --onefile is unreliable with Python 3.14 (causes
# STATUS_INVALID_IMAGE_HASH / "Bad Image" on launch). 3.12 is the LTS-class
# version PyInstaller is fully battle-tested against.
if (-not $Python) {
    $candidates = @(
        "C:\PythonEnvs\TokenUsageTray-build312\Scripts\python.exe",
        "C:\PythonEnvs\TokenUsageTray\.venv\Scripts\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $Python = $c; break }
    }
    if (-not $Python) {
        $Python = (Get-Command py -ErrorAction Stop).Source
    }
}
Write-Host "Using Python: $Python"

# Warn if user is building with 3.14 (onefile is broken there).
$pyVer = (& $Python -c "import sys; print(sys.version_info[:2])").Trim()
if ($pyVer -eq "(3, 14)") {
    Write-Warning "Building with Python 3.14 produces a 'Bad Image' onefile due to PyInstaller/Defender hash check. Use Python 3.12 for the build venv."
}

if ($Clean) {
    Write-Host "Cleaning previous build artifacts..."
    Remove-Item -Recurse -Force build, dist, "TokenTray.spec" -ErrorAction SilentlyContinue
}

# Ensure build deps are present.
& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet -r requirements.txt
& $Python -m pip install --quiet "pyinstaller>=6.3"

# Generate icon if missing.
if (-not (Test-Path "assets\tokentray.ico")) {
    Write-Host "Generating icon..."
    & $Python tools\make_icon.py
}

# Build. --onefile gives a single exe (~30-50MB); --windowed = no console.
& $Python -m PyInstaller `
    --onefile `
    --windowed `
    --name TokenTray `
    --icon assets\tokentray.ico `
    --add-data "assets\tokentray.ico;assets" `
    --noconfirm `
    --clean `
    run.pyw

if (-not (Test-Path "dist\TokenTray.exe")) {
    Write-Error "Build failed: dist\TokenTray.exe not produced."
    exit 1
}

$size = [math]::Round((Get-Item "dist\TokenTray.exe").Length / 1MB, 1)
Write-Host "✓ Built dist\TokenTray.exe ($size MB)"
