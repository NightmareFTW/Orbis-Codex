@echo off
rem Orbis Codex - the main window. Double-click it: Arena (predictions), Arena stats (the heroes met most), Settings and
rem the rest, in one place. The first start installs what it needs (a few minutes, once); after that this console opens
rem for a moment and closes by itself: the window runs on its own, with no console.
rem   Orbis.cmd --console   keeps the console open and prints the program's messages there (to find out what went wrong).
rem Read-only towards the game: it only copies screen pixels, never a key or click to the game.
if /i "%~1"=="--console" goto console
call "%~dp0OrbisCodex.cmd" window
set "ORBIS_RC=%ERRORLEVEL%"
if not "%ORBIS_RC%"=="0" (
    echo.
    echo The window could not be started: see the message above.
    pause
)
exit /b %ORBIS_RC%

:console
call "%~dp0OrbisCodex.cmd" app
set "ORBIS_RC=%ERRORLEVEL%"
rem Not "if errorlevel 1": a program that dies on a fatal error ends with a negative code (0xC...), which that test misses.
if not "%ORBIS_RC%"=="0" (
    echo.
    echo The window ended with code %ORBIS_RC%: see the message above. If it closed by itself, the files in
    echo   %LOCALAPPDATA%\OrbisCodex\logs
    echo ^(app.log and crash-*.log^) say why: send them with that message.
    pause
)
exit /b %ORBIS_RC%
