# Orbis Codex: installs uv (https://docs.astral.sh/uv/) for the current user with the official installer.
# Called by OrbisCodex.cmd only when uv.exe cannot be found. Works on Windows PowerShell 5.1 and PowerShell 7.
# Nothing here touches the game.

$ErrorActionPreference = 'Stop'

Write-Host 'Orbis Codex: uv (Python package manager) not found - installing it from https://astral.sh/uv ...'

# TLS 1.2 must be enabled in the process that downloads (old .NET defaults can be SSL3/TLS1.0 only),
# so it is set inside the child process that runs the official installer.
$install = '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; ' +
           'irm https://astral.sh/uv/install.ps1 | iex'
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command $install
if ($LASTEXITCODE -ne 0) {
    Write-Host "Orbis Codex: the uv installer failed (exit code $LASTEXITCODE)."
    exit 1
}
exit 0
