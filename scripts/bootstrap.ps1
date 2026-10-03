# Orbis Codex bootstrap (Windows PowerShell 5.1+ / PowerShell 7+).
# Ensures uv is installed, lets uv provide Python 3.12 and the locked dependencies, then runs the `e7` app.
# Usage: OrbisCodex.cmd [e7 arguments...]   e.g.  OrbisCodex.cmd doctor
# Every argument is forwarded verbatim to `e7`. Nothing here touches the game.

$ErrorActionPreference = 'Stop'
$AppArgs = @($args)
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe')
    )
    if ($env:CARGO_HOME) { $candidates += (Join-Path $env:CARGO_HOME 'bin\uv.exe') }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Host 'Orbis Codex: uv (Python package manager) not found - installing it from https://astral.sh/uv ...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
    if ($LASTEXITCODE -ne 0) { Write-Error "uv installer failed (exit code $LASTEXITCODE)." }
    $uv = Find-Uv
    if (-not $uv) {
        Write-Error 'uv was installed but could not be found. Open a new terminal and run OrbisCodex.cmd again, or install uv manually: https://docs.astral.sh/uv/'
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.venv'))) {
    Write-Host 'Orbis Codex: first start - installing Python 3.12 and dependencies (this can take a few minutes) ...'
}

# --frozen: install exactly what uv.lock pins; --project: keep the caller's working directory for relative paths.
& $uv run --frozen --project $RepoRoot e7 @AppArgs
exit $LASTEXITCODE
