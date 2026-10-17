@echo off
rem Orbis Codex - the main window. Double-click it: Arena (predictions), Arena stats (the heroes met most) and the
rem rest, in one place. The first start installs what it needs (a few minutes, once).
rem Read-only towards the game: it only copies screen pixels, never a key or click to the game.
call "%~dp0OrbisCodex.cmd" app %*
rem Not "if errorlevel 1": a program that dies on a fatal error ends with a negative code (0xC...), which that test misses.
if not "%ERRORLEVEL%"=="0" (
    echo.
    echo The window ended with code %ERRORLEVEL%: see the message above. If it closed by itself, the files in
    echo   %LOCALAPPDATA%\OrbisCodex\logs
    echo ^(app.log and crash-*.log^) say why: send them with that message.
    pause
)
