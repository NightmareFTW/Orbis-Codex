@echo off
rem Orbis Codex - the main window. Double-click it: Arena (predictions), Arena stats (the heroes met most) and the
rem rest, in one place. The first start installs what it needs (a few minutes, once).
rem Read-only towards the game: it only copies screen pixels, never a key or click to the game.
call "%~dp0OrbisCodex.cmd" app %*
if errorlevel 1 (
    echo.
    echo The window did not start: see the message above.
    pause
)
