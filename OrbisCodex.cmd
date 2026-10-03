@echo off
rem Orbis Codex launcher. First run installs uv, Python 3.12 and the dependencies automatically.
rem Usage: OrbisCodex.cmd [e7 arguments...]   e.g.  OrbisCodex.cmd doctor
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
exit /b %ERRORLEVEL%
