@echo off
setlocal EnableExtensions
rem Orbis Codex launcher. The first start installs uv (if missing), Python 3.12 and the dependencies automatically.
rem   From a terminal, inside this folder:  .\OrbisCodex.cmd [e7 arguments...]   e.g.  .\OrbisCodex.cmd doctor
rem   Double-click (no arguments): runs "e7 doctor" and waits for a key so the output stays readable.
rem Arguments are passed to e7 exactly as typed (no PowerShell in between). Nothing here touches the game.

set "ORBIS_ROOT=%~dp0."
if not exist "%ORBIS_ROOT%\pyproject.toml" goto not_extracted

call :find_uv
if not defined ORBIS_UV (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ORBIS_ROOT%\scripts\install-uv.ps1"
    call :find_uv
)
if not defined ORBIS_UV goto no_uv

if not exist "%ORBIS_ROOT%\.venv" echo Orbis Codex: first start - installing Python 3.12 and dependencies ^(a few minutes, only once^) ...

if "%~1"=="" goto interactive
"%ORBIS_UV%" run --frozen --no-dev --project "%ORBIS_ROOT%" e7 %*
exit /b %ERRORLEVEL%

:interactive
"%ORBIS_UV%" run --frozen --no-dev --project "%ORBIS_ROOT%" e7 doctor
set "ORBIS_RC=%ERRORLEVEL%"
echo.
pause
exit /b %ORBIS_RC%

:find_uv
rem uv on PATH, then the official installer's locations (UV_INSTALL_DIR, XDG_BIN_HOME, ~\.local\bin, legacy cargo dir).
set "ORBIS_UV="
for /f "delims=" %%U in ('where uv.exe 2^>nul') do if not defined ORBIS_UV set "ORBIS_UV=%%U"
if not defined ORBIS_UV if defined UV_INSTALL_DIR if exist "%UV_INSTALL_DIR%\uv.exe" set "ORBIS_UV=%UV_INSTALL_DIR%\uv.exe"
if not defined ORBIS_UV if defined XDG_BIN_HOME if exist "%XDG_BIN_HOME%\uv.exe" set "ORBIS_UV=%XDG_BIN_HOME%\uv.exe"
if not defined ORBIS_UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "ORBIS_UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined ORBIS_UV if defined CARGO_HOME if exist "%CARGO_HOME%\bin\uv.exe" set "ORBIS_UV=%CARGO_HOME%\bin\uv.exe"
if not defined ORBIS_UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "ORBIS_UV=%USERPROFILE%\.cargo\bin\uv.exe"
exit /b 0

:not_extracted
echo Orbis Codex: the project files were not found next to OrbisCodex.cmd.
echo Extract the whole ZIP first ^(right-click ^> Extract All^), then run OrbisCodex.cmd from the extracted folder.
if "%~1"=="" pause
exit /b 1

:no_uv
echo Orbis Codex: uv could not be installed or found. Check the internet connection and try again,
echo or install uv manually: https://docs.astral.sh/uv/getting-started/installation/
if "%~1"=="" pause
exit /b 1
