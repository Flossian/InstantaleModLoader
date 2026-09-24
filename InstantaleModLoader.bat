@echo off
rem ---------------------------------------------------------------------------
rem  Instantale ModLoader -- main entry point
rem
rem  Opens the mod manager window: load order, enable/disable, and a button
rem  that starts the game and injects the loader once the game is ready.
rem
rem  Everything else lives in tools\ :
rem    tools\watch.bat     console watcher (auto-inject, no GUI)
rem    tools\injector.py   the injector itself
rem
rem  Needs 64-bit Python (the game is x64). Prefers 3.13, then the py launcher,
rem  then whatever is on PATH. pythonw.exe is used so no console window opens.
rem
rem  ASCII only on purpose: a .bat is read using the current console code page,
rem  so non-ASCII text here would break parsing on some machines.
rem  Japanese notes live in docs\README.md.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

rem Run Python in UTF-8 mode. Without it, text handling falls back to the
rem machine's code page (cp932 and friends), which breaks once this folder
rem sits under a path containing Japanese or other non-ASCII characters.
set "PYTHONUTF8=1"

rem The program and its arguments are kept apart. PYEXE is always quoted when
rem it runs, so a path with spaces in it (C:\Users\Taro Yamada\...) works;
rem PYARGS holds the "-3" of the py launcher, which must stay outside the quotes.
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
set "PYARGS="
if exist "%PYEXE%" goto :run

where pyw >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=pyw"
  set "PYARGS=-3"
  goto :run
)

where pythonw >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=pythonw"
  goto :run
)

echo.
echo   No 64-bit Python found.
echo   Install one from python.org, or edit the PYEXE= line in this file
echo   to point at your pythonw.exe.
echo.
pause
exit /b 2

:run
start "" "%PYEXE%" %PYARGS% "tools\gui.py"
endlocal
