# TokenTray release checklist

Releases are built and signed locally. CI (`.github/workflows/release.yml`,
"Build validation") only proves the chain compiles; its artifact is
`-UNSIGNED`-suffixed and never publishable.

## One-time machine setup (already done on JJ's main machine)

Shared with WordMD (its spec 10024 has the full walkthrough and every trap):

1. Azure Artifact Signing account `reboundman-signing` (wus2), identity
   validation Completed, certificate profile `WordMD-PublicTrustCert`
   (Public Trust; shared by all ReboundMan.com LLC apps; the Basic tier
   allows one Public Trust profile per account).
2. Client tools: `winget install -e --id Microsoft.Azure.ArtifactSigningClientTools`
   (verify publisher is Microsoft first with `winget show`).
3. Azure CLI: `winget install -e --id Microsoft.AzureCLI`, then `az login`
   and select the tenant that holds the signing account (not the consumer
   "Microsoft Services" tenant).
4. Signing metadata at `C:\Code\Signing\.reboundman-signing\metadata.json`
   (outside all repos and OneDrive; names the endpoint, account, and
   certificate profile; carries no secret, RBAC is the access control).

## Per release

1. Bump the version in all five spots: `VERSION`, `_version.py`,
   `tokentray/_version.py`, `pyproject.toml`, `installer/TokenTray.iss`
   (`MyAppVersion`).
2. Add the `CHANGELOG.md` entry; move the punchlist item to Done if any.
3. Run the tests: `python -m pytest -q` (build venv:
   `C:\PythonEnvs\TokenUsageTray-build312`).
4. Commit, push `main`, tag `v<version>`, push the tag (this triggers the CI
   build validation; it publishes nothing).
5. **Signed build**:

   ```powershell
   .\build.ps1 -Clean -Installer -Sign
   ```

   The script preflights the signing paths and the `az` session, signs
   `TokenTray.exe` before packaging, compiles the installer with `/DSIGN`
   (ISCC signs the installer and uninstaller via `SignTool=`), verifies
   both signatures including the signer identity, and writes the
   `.sha256.txt`.
6. Package the zip from the already-signed exe and hash it:

   ```powershell
   Compress-Archive -Path "dist\TokenTray\*" -DestinationPath "dist\TokenTray-v<version>-win64.zip" -Force
   ```
7. Release draft-first, so `releases/latest` is never assetless:

   ```powershell
   gh release create v<version> -R ReboundMan/TokenTray --draft --generate-notes --verify-tag
   gh release upload v<version> -R ReboundMan/TokenTray "dist\TokenTray-Setup-<version>.exe" "dist\TokenTray-Setup-<version>.exe.sha256.txt" "dist\TokenTray-v<version>-win64.zip" "dist\TokenTray-v<version>-win64.zip.sha256.txt"
   gh release edit v<version> -R ReboundMan/TokenTray --draft=false --latest
   ```

## Smoke tests (all three, every release)

- **Installer signature**: right-click `dist\TokenTray-Setup-<version>.exe`,
  Properties, Digital Signatures: ReboundMan.com LLC, timestamped.
- **Installed app signature**: after installing, check the installed
  `TokenTray.exe` the same way. Checking only the installer wrapper is the
  gap that lets an unsigned inner exe ship (WordMD review finding).
- **SmartScreen**: run the downloaded installer on a machine or profile that
  has not seen it. The execution-time "Windows protected your PC" block is
  the metric. Edge's milder download-reputation advisory can linger until
  per-file download volume accrues; that one is expected early on.
