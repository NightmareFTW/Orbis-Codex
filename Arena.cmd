@echo off
rem Orbis Codex - Arena. Double-click and play: each time the Arena VS screen appears, both teams are read
rem (unknown heroes are asked once, then learned) and the chance to win is shown. Close the window to stop.
rem Read-only: it only copies screen pixels, never a key or click to the game.
call "%~dp0OrbisCodex.cmd" arena watch %*
echo.
pause
