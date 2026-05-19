# build.ps1 - Build a standalone TokenTray.exe with PyInstaller.
#
# Usage:
#   .\build.ps1                # builds dist\TokenTray.exe using the local venv
#   .\build.ps1 -Clean         # nukes build\, dist\, and .spec first
#   .\build.ps1 -Python <exe>  # use a specific python.exe (e.g. for CI)
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$Clean,
    [ValidateSet("onefile", "onedir")]
    [string]$Mode = "onedir"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# Pick a python: explicit arg > 3.12 build venv > 3.14 run venv > py launcher
# Note: PyInstaller --onefile is unreliable across Python versions because
# Defender real-time protection intercepts the temp-extracted DLLs and the
# resulting integrity-check failure surfaces as STATUS_INVALID_IMAGE_HASH
# ("Bad Image" dialog). We default to --onedir for distribution and zip it.
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
Write-Host "Build mode  : $Mode"

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

$modeFlag = if ($Mode -eq "onefile") { "--onefile" } else { "--onedir" }

& $Python -m PyInstaller `
    $modeFlag `
    --windowed `
    --name TokenTray `
    --icon assets\tokentray.ico `
    --add-data "assets\tokentray.ico;assets" `
    --noconfirm `
    --clean `
    run.pyw

if ($Mode -eq "onefile") {
    $exe = "dist\TokenTray.exe"
    if (-not (Test-Path $exe)) {
        Write-Error "Build failed: $exe not produced."
        exit 1
    }
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "Built $exe ($size MB)"
} else {
    $dir = "dist\TokenTray"
    if (-not (Test-Path "$dir\TokenTray.exe")) {
        Write-Error "Build failed: $dir\TokenTray.exe not produced."
        exit 1
    }
    $bytes = (Get-ChildItem $dir -Recurse | Measure-Object Length -Sum).Sum
    $size = [math]::Round($bytes / 1MB, 1)
    Write-Host "Built $dir\ ($size MB across $(Get-ChildItem $dir -Recurse -File | Measure-Object).Count files)"
}
