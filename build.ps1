# build.ps1 - Build a standalone TokenTray.exe with PyInstaller.
#
# Usage:
#   .\build.ps1                        # builds dist\TokenTray.exe using the local venv
#   .\build.ps1 -Clean                 # nukes build\, dist\, and .spec first
#   .\build.ps1 -Python <exe>          # use a specific python.exe (e.g. for CI)
#   .\build.ps1 -Clean -Installer -Sign  # signed release build (local only)
#
# Signing (-Sign) uses Azure Artifact Signing with the shared ReboundMan.com LLC
# publisher setup established for WordMD (spec 10024 there). It is local-only:
# no signing credential exists in CI, which builds unsigned for validation and
# gets a -UNSIGNED-suffixed installer it can never accidentally publish.
# Two orderings are load-bearing, both learned the hard way on WordMD:
#   1. TokenTray.exe is signed BEFORE the installer is compiled (and before any
#      zip is made) - Inno's [Files] packages whatever bytes exist at ISCC time.
#   2. Verification asserts the SIGNER IDENTITY, not just chain validity: a
#      misconfigured /dlib silently falls back to local-store signing, and only
#      the subject check catches that.
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$Clean,
    [switch]$Installer,
    [switch]$Sign,
    [ValidateSet("onefile", "onedir")]
    [string]$Mode = "onedir",
    [string]$SignToolPath,
    [string]$DlibPath,
    [string]$MetadataPath = "C:\Code\Signing\.reboundman-signing\metadata.json",
    [string]$TimestampUrl = "http://timestamp.acs.microsoft.com",
    [string]$ExpectedSubject = "ReboundMan.com LLC"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Invoke-ArtifactSign([string]$Target) {
    & $SignToolPath sign /v /fd SHA256 /tr $TimestampUrl /td SHA256 `
        /dlib $DlibPath /dmdf $MetadataPath $Target
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Signing failed for $Target (exit $LASTEXITCODE)."
        exit 1
    }
}

function Assert-Signed([string]$Target) {
    $sig = Get-AuthenticodeSignature $Target
    if ($sig.Status -ne 'Valid') {
        Write-Error "Signature INVALID on $Target - status: $($sig.Status)"
        exit 1
    }
    $subject = $sig.SignerCertificate.Subject
    if ($subject -notmatch [regex]::Escape($ExpectedSubject)) {
        Write-Error ("Signature on $Target is valid but from the WRONG identity " +
            "(expected '$ExpectedSubject', got '$subject'). This is the " +
            "local-certificate-store fallback: the dlib was misconfigured and " +
            "signtool used a local cert instead of Azure Artifact Signing. Do not ship.")
        exit 1
    }
    if (-not $sig.TimeStamperCertificate) {
        Write-Error ("Signature on $Target is NOT timestamped. Artifact Signing " +
            "certs expire in ~3 days, so the signature would stop validating. Do not ship.")
        exit 1
    }
    Write-Host "  signature verified: $(Split-Path $Target -Leaf) - $subject"
}

# --- Signing preflight: fail before the slow build, not after ---------------
if ($Sign) {
    if (-not $SignToolPath) {
        $SignToolPath = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" `
            -Directory -ErrorAction SilentlyContinue |
            Where-Object Name -match '^10\.' | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $SignToolPath -or -not (Test-Path $SignToolPath)) {
        Write-Error "SignTool (x64) not found. Install: winget install -e --id Microsoft.Azure.ArtifactSigningClientTools"
        exit 1
    }
    if (-not $DlibPath) {
        $DlibPath = "$env:LOCALAPPDATA\Microsoft\MicrosoftArtifactSigningClientTools\Azure.CodeSigning.Dlib.dll"
    }
    if (-not (Test-Path $DlibPath)) {
        Write-Error "Artifact Signing dlib not found at $DlibPath"
        exit 1
    }
    if (-not (Test-Path $MetadataPath)) {
        Write-Error "Signing metadata.json not found at $MetadataPath"
        exit 1
    }
    # The ISCC sign parameter travels through an ASCII .cmd file; a non-ASCII
    # path would be mangled there and the dlib would silently fall back to
    # local-store signing, so refuse that case outright.
    foreach ($p in @($SignToolPath, $DlibPath, $MetadataPath)) {
        if ($p -match '[^\x20-\x7E]') {
            Write-Error "Path contains non-ASCII characters, unsafe for the ISCC invocation: $p"
            exit 1
        }
    }
    # The dlib authenticates via DefaultAzureCredential; without a live az
    # session it falls through to an interactive browser prompt against the
    # wrong tenant, so surface the login state up front.
    $acct = az account show --output json 2>$null | ConvertFrom-Json
    if (-not $acct) {
        Write-Error "Not logged in to Azure. Run: az login  (then select the tenant holding the signing account)"
        exit 1
    }
    Write-Host "Signing as  : $($acct.user.name) / tenant $($acct.tenantId)"
}

# Pick a python: explicit arg > 3.12 build venv > 3.14 run venv > py launcher
# Note: PyInstaller --onefile is unreliable across Python versions because
# Defender real-time protection intercepts the temp-extracted DLLs and the
# resulting integrity-check failure surfaces as STATUS_INVALID_IMAGE_HASH
# ("Bad Image" dialog). We default to --onedir for distribution and either
# zip it or wrap it with Inno Setup (-Installer).
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
    $exe = "$dir\TokenTray.exe"
    if (-not (Test-Path $exe)) {
        Write-Error "Build failed: $exe not produced."
        exit 1
    }
    $bytes = (Get-ChildItem $dir -Recurse | Measure-Object Length -Sum).Sum
    $size = [math]::Round($bytes / 1MB, 1)
    Write-Host "Built $dir\ ($size MB)"
}

# Sign the app exe BEFORE packaging (installer or zip): [Files] and
# Compress-Archive both capture whatever bytes exist at that moment.
if ($Sign) {
    Write-Host "Signing TokenTray.exe (before packaging)..."
    Invoke-ArtifactSign $exe
    Assert-Signed $exe
}

if ($Installer) {
    if ($Mode -ne "onedir") {
        Write-Error "-Installer requires -Mode onedir (we wrap dist\TokenTray\)."
        exit 1
    }
    $iscc = @(
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        Write-Error "Inno Setup 6 not found. Install with: winget install JRSoftware.InnoSetup"
        exit 1
    }
    # Stale-output guard: a previous run's installer would otherwise be
    # picked up below and reported (or verified) as this run's output.
    Remove-Item "dist\TokenTray-Setup-*.exe" -Force -ErrorAction SilentlyContinue

    Write-Host "Compiling installer with $iscc ..."
    if ($Sign) {
        # The installer itself is signed by ISCC via the SignTool= directive
        # (guarded by /DSIGN in TokenTray.iss). The tool definition goes
        # through a temp .cmd so the embedded quoting reaches ISCC exactly as
        # written - PowerShell's native-argument re-quoting would otherwise
        # mangle it. $q and $f below are Inno tokens, not PowerShell
        # variables, hence the single-quoted template.
        $template = '/S"artifactsigning=$q{0}$q sign /v /fd SHA256 /tr {1} /td SHA256 /dlib $q{2}$q /dmdf $q{3}$q $f"'
        $signArg = $template -f $SignToolPath, $TimestampUrl, $DlibPath, $MetadataPath
        $cmdFile = Join-Path ([System.IO.Path]::GetTempPath()) "tokentray-iscc-$PID.cmd"
        Set-Content -Path $cmdFile -Value "@echo off`r`n`"$iscc`" /DSIGN $signArg `"installer\TokenTray.iss`"`r`nexit /b %ERRORLEVEL%" -Encoding ASCII
        try {
            & cmd.exe /c $cmdFile
            if ($LASTEXITCODE -ne 0) {
                Write-Error "ISCC failed ($LASTEXITCODE). Check its log for the signtool line it built."
                exit 1
            }
        } finally {
            Remove-Item $cmdFile -ErrorAction SilentlyContinue
        }
    } else {
        & $iscc "installer\TokenTray.iss"
    }
    $setup = Get-ChildItem "dist\TokenTray-Setup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $setup) {
        Write-Error "Inno Setup did not produce a TokenTray-Setup-*.exe in dist\."
        exit 1
    }
    if ($Sign) {
        if ($setup.Name -like "*-UNSIGNED*") {
            Write-Error "Signed build produced an -UNSIGNED-named installer; /DSIGN did not reach ISCC."
            exit 1
        }
        Assert-Signed $setup.FullName
        $hash = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash
        "$hash  $($setup.Name)" | Out-File -Encoding ASCII "$($setup.FullName).sha256.txt"
    }
    $mb = [math]::Round($setup.Length / 1MB, 1)
    Write-Host "Built $($setup.FullName) ($mb MB)"
}
