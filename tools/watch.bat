@echo off
rem ---------------------------------------------------------------------------
rem  Instantale mod watcher -- launcher
rem
rem  Watches for the game to start and injects the mod loader automatically.
rem  Just leave this window open. Press Ctrl-C to stop.
rem
rem  The injection dies with the game process, so it has to be redone every time
rem  the game starts. Keeping this running removes that chore.
rem
rem  Arguments are passed straight through to watcher.py:
rem    watch.bat            keep watching
rem    watch.bat --once     inject into the running game once, then exit
rem
rem  Needs 64-bit Python (the game is x64). Prefers 3.13, then the py launcher,
rem  then whatever "python" is on PATH.
rem
rem  ASCII only on purpose: a .bat is read using the current console code page,
rem  so non-ASCII text here would break parsing on some machines.
rem  Japanese notes live in docs\README.md / docs\TECH.md.
rem  The GUI (..\InstantaleModLoader.bat) does the same job with a window.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title Instantale mod watcher

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if exist "%PY%" goto :run

where py >nul 2>&1
if not errorlevel 1 (
  set "PY=py -3"
  goto :run
)

where python >nul 2>&1
if not errorlevel 1 (
  set "PY=python"
  goto :run
)

echo.
echo   No 64-bit Python found.
echo   Install one from python.org, or edit the PY= line in this file
echo   to point at your python.exe.
echo.
pause
exit /b 2

:run
echo   python : %PY%
echo   folder : %CD%
echo.
echo   Start the game and it will be injected automatically. Ctrl-C to stop.
echo.
%PY% watcher.py %*
set "RC=%ERRORLEVEL%"

rem Keep the window open on failure so the message is readable when the file
rem was started by double-clicking it.
if not "%RC%"=="0" (
  echo.
  echo   watcher.py exited with code %RC%.
  pause
)
endlocal ^& exit /b %RC%
