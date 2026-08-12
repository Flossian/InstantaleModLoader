@echo off
rem ---------------------------------------------------------------------------
rem  Instantale local-LLM context probe -- launcher
rem
rem  Measures how wide a context window this machine can actually run, then
rem  prints the settings to put into the 904_llm_context_size mod.
rem
rem  Close the game first. The probe starts llama-server itself and needs the
rem  VRAM and the port to be free.
rem
rem  It loads the model once per candidate size, so it takes several minutes.
rem  Ctrl-C is safe: any llama-server it started is killed on the way out.
rem
rem  Arguments are passed straight through to llm_ctx_probe.py:
rem    llm_ctx_probe.bat                       measure with 1 slot (default)
rem    llm_ctx_probe.bat --parallel 0          measure with the shared KV cache
rem    llm_ctx_probe.bat --ctx-list 8192,16384 use your own candidates
rem    llm_ctx_probe.bat --reserve 400         reserve more for the game itself
rem
rem  Needs 64-bit Python (the game is x64). Prefers 3.13, then the py launcher,
rem  then whatever "python" is on PATH.
rem
rem  ASCII only on purpose: a .bat is read using the current console code page,
rem  so non-ASCII text here would break parsing on some machines.
rem  Japanese notes live in runtime\mods\904_llm_context_size\DOC.md.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title Instantale local-LLM context probe

rem Run Python in UTF-8 mode. Without it, text handling falls back to the
rem machine's code page (cp932 and friends), which breaks once this folder
rem sits under a path containing Japanese or other non-ASCII characters.
set "PYTHONUTF8=1"

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
%PY% llm_ctx_probe.py %*
set "RC=%ERRORLEVEL%"

rem Keep the window open so the result is readable when the file was started
rem by double-clicking it.
echo.
pause
endlocal ^& exit /b %RC%
