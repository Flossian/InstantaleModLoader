@echo off
rem ---------------------------------------------------------------------------
rem  Instantale mod loader -- distribution builder
rem
rem  Builds three release assets, so the loader and the mods can be attached to
rem  a GitHub release as separate downloads:
rem
rem    InstantaleModLoader-<ver>-full.zip   loader + every mod (for new players)
rem    InstantaleModLoader-<ver>.zip        loader only
rem    InstantaleMods-<ver>.zip             every mod, no loader
rem
rem  The mods package is shaped for the manager: "MOD wo tsuika..." reads a zip,
rem  finds every folder holding a mod.json whatever the depth, and copies it
rem  into runtime\mods\. So the whole pack installs in one step, and so does a
rem  single mod folder pulled out of it.
rem
rem  What goes where:
rem    loader   InstantaleModLoader.bat, LICENSE, NOTICE, tools\, docs\,
rem             runtime\instantale_modloader\, runtime\mods\_template\
rem    mods     runtime\mods\* except _template, plus LICENSE and NOTICE
rem    full     the two merged, plus load_order.json
rem
rem  _template ships with the loader, not with the mods: it is the starting
rem  point for writing a mod, and the loader skips folders starting with "_".
rem
rem  load_order.json ships in the full package only. Elsewhere it would name
rem  mods that are not installed, and the loader reports that as a problem.
rem  Leaving it out costs nothing: with no order file the loader sorts by folder
rem  name -- which the number prefixes already make correct -- and then applies
rem  the after/before rules from each mod.json.
rem
rem  LICENSE and NOTICE are in every package, including the mods-only one: MIT
rem  requires the copyright notice to travel with every copy, so a package
rem  without them is not a lawful redistribution. NOTICE states what the grant
rem  does and does not cover (game-derived strings, GAME.md, the EULA question).
rem
rem  Never shipped: out\ and settings\ (both made at runtime), __pycache__ /
rem  *.pyc, tools\test_*.py and the one-off save fixers, and
rem  llm_replacements.txt -- that last one is the player's own copy of a mod's
rem  data file, so only the shipped llm_replacements.default.txt goes in.
rem
rem    make_dist.bat           version comes from runtime\instantale_modloader
rem    make_dist.bat 1.2.0     override the version string
rem
rem  Staging trees are kept under dist\_stage\ for checking. The zips land in
rem  dist\ directly, and they are the only things meant to be uploaded.
rem
rem  Python is optional here: if one is found the staged .py files are
rem  byte-compiled as a syntax check, then the caches are removed again.
rem
rem  ASCII only on purpose: a .bat is read using the current console code page,
rem  so non-ASCII text here would break parsing on some machines.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Instantale mod loader -- make dist

set "NAME=InstantaleModLoader"
set "MODSNAME=InstantaleMods"
set "VERFILE=runtime\instantale_modloader\__init__.py"

rem --- version -------------------------------------------------------------
set "VER=%~1"
if not "%VER%"=="" goto :haveversion

if not exist "%VERFILE%" (
  echo   ERROR: %VERFILE% not found. Run this from the project folder.
  goto :fail
)
for /f "tokens=3" %%a in ('findstr /b /c:"__version__" "%VERFILE%"') do set "VER=%%a"
set "VER=%VER:"=%"
set "VER=%VER:'=%"
if "%VER%"=="" (
  echo   ERROR: could not read __version__ from %VERFILE%.
  echo   Pass one instead:  make_dist.bat 1.2.0
  goto :fail
)

:haveversion
rem  Each package is staged inside its own wrapper folder, so the folder the
rem  player ends up with keeps the name we want no matter what the zip is
rem  called -- the full package still unpacks to InstantaleModLoader-<ver>\,
rem  the same as every release so far.
set "STAGE=dist\_stage"
set "LOADER=%STAGE%\loader\%NAME%-%VER%"
set "MODS=%STAGE%\mods\%MODSNAME%-%VER%"
set "FULL=%STAGE%\full\%NAME%-%VER%"

set "ZIPLOADER=dist\%NAME%-%VER%.zip"
set "ZIPMODS=dist\%MODSNAME%-%VER%.zip"
set "ZIPFULL=dist\%NAME%-%VER%-full.zip"

echo.
echo   name    : %NAME%
echo   version : %VER%
echo   staging : %CD%\%STAGE%
echo.

rem --- clean ---------------------------------------------------------------
if exist "%STAGE%" rd /s /q "%STAGE%"
if exist "%STAGE%" (
  echo   ERROR: could not remove the old staging folder.
  echo   Close anything that has it open and try again.
  goto :fail
)
for %%z in ("%ZIPLOADER%" "%ZIPMODS%" "%ZIPFULL%") do if exist "%%~z" del /q "%%~z"
md "%LOADER%" 2>nul
md "%MODS%" 2>nul
md "%FULL%" 2>nul
if not exist "%FULL%" (
  echo   ERROR: could not create the staging folders under %STAGE%.
  goto :fail
)

rem ===========================================================================
rem  loader
rem ===========================================================================
rem  The launcher is the only thing at the top level: double-clicking it opens
rem  the manager.
echo   [loader] launcher ...
if not exist "%NAME%.bat" (
  echo   ERROR: %NAME%.bat is missing.
  goto :fail
)
copy /y "%NAME%.bat" "%LOADER%\" >nul || goto :copyfail

rem  /E keeps the tree, and __pycache__ / *.pyc never ship: they are built for
rem  one Python version and would shadow edited sources on another machine.
echo   [loader] runtime ...
robocopy "runtime\instantale_modloader" "%LOADER%\runtime\instantale_modloader" /E /XD "__pycache__" /XF "*.pyc" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed on runtime\instantale_modloader ^(code %ERRORLEVEL%^).
  goto :fail
)
if not exist "runtime\mods\_template\mod.json" (
  echo   ERROR: runtime\mods\_template is missing.
  goto :fail
)
robocopy "runtime\mods\_template" "%LOADER%\runtime\mods\_template" /E /XD "__pycache__" /XF "*.pyc" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed on runtime\mods\_template ^(code %ERRORLEVEL%^).
  goto :fail
)

rem  The GUI, the injector and the console watcher. test_*.py and the one-off
rem  save fixers are development harnesses and stay out.
echo   [loader] tools ...
md "%LOADER%\tools" 2>nul
for %%f in (gui.py injector.py watcher.py logrotate.py watch.bat check_mods.py) do (
  if not exist "tools\%%f" (
    echo   ERROR: tools\%%f is missing.
    goto :fail
  )
  copy /y "tools\%%f" "%LOADER%\tools\" >nul || goto :copyfail
)

echo   [loader] docs ...
md "%LOADER%\docs" 2>nul
for %%f in (README.md TECH.md GAME.md VERIFICATION.md) do (
  if exist "docs\%%f" copy /y "docs\%%f" "%LOADER%\docs\" >nul || goto :copyfail
)

rem ===========================================================================
rem  mods
rem ===========================================================================
echo   [mods] copying ...
robocopy "runtime\mods" "%MODS%" /E /XD "__pycache__" "_template" /XF "*.pyc" "llm_replacements.txt" "load_order.json" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed on runtime\mods ^(code %ERRORLEVEL%^).
  goto :fail
)

rem  A pack with no mod.json in it would install as nothing. Catch that here
rem  rather than after it is uploaded.
set "MODCOUNT=0"
for /d %%d in ("%MODS%\*") do if exist "%%d\mod.json" set /a MODCOUNT+=1
if "%MODCOUNT%"=="0" (
  echo   ERROR: no mod folders were staged. Check runtime\mods\.
  goto :fail
)

rem ===========================================================================
rem  license -- into every package, see the note at the top
rem ===========================================================================
echo   copying license ...
for %%f in (LICENSE NOTICE) do (
  if not exist "%%f" (
    echo   ERROR: %%f is missing.
    goto :fail
  )
  copy /y "%%f" "%LOADER%\" >nul || goto :copyfail
  copy /y "%%f" "%MODS%\" >nul || goto :copyfail
)

rem ===========================================================================
rem  full = loader + mods + load_order.json
rem ===========================================================================
echo   [full] merging ...
robocopy "%LOADER%" "%FULL%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed while building the full package ^(code %ERRORLEVEL%^).
  goto :fail
)
robocopy "%MODS%" "%FULL%\runtime\mods" /E /XF "LICENSE" "NOTICE" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed while adding mods to the full package ^(code %ERRORLEVEL%^).
  goto :fail
)
if exist "runtime\mods\load_order.json" (
  copy /y "runtime\mods\load_order.json" "%FULL%\runtime\mods\" >nul || goto :copyfail
)

rem ===========================================================================
rem  syntax check
rem ===========================================================================
rem  Same interpreter preference as watch.bat. The full package contains every
rem  .py that ships, so checking it covers the other two. Skipped when no
rem  Python is installed.
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
  set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
) else (
  where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)

if defined PY (
  echo   syntax check ...
  %PY% -m compileall -q "%FULL%" >nul
  if errorlevel 1 (
    echo.
    echo   ERROR: a staged .py file does not compile. Nothing was zipped.
    %PY% -m compileall -q "%FULL%"
    goto :fail
  )
  rem compileall just created the caches we refuse to ship -- drop them again.
  for /d /r "%STAGE%" %%d in ("__pycache__") do if exist "%%d" rd /s /q "%%d"
) else (
  echo   syntax check : skipped ^(no Python found^)
)

rem ===========================================================================
rem  zip
rem ===========================================================================
echo   zipping ...
call :zip "%LOADER%" "%ZIPLOADER%" || goto :fail
call :zip "%MODS%"   "%ZIPMODS%"   || goto :fail
call :zip "%FULL%"   "%ZIPFULL%"   || goto :fail

rem --- report -----------------------------------------------------------------
echo.
echo   done.  %MODCOUNT% mod(s) packaged.
echo.
for %%z in ("%ZIPFULL%" "%ZIPLOADER%" "%ZIPMODS%") do echo     %%~nxz  (%%~zz bytes)
echo.
echo   Upload the three zips in dist\ as separate release assets.
echo     -full   loader + mods. Unzip anywhere, run InstantaleModLoader.bat.
echo     loader  loader only.
echo     mods    add the zip itself with "MOD wo tsuika..." in the manager,
echo             or copy the mod folders into runtime\mods\ by hand.
echo.
endlocal & exit /b 0

rem ---------------------------------------------------------------------------
rem  :zip <staging folder> <zip path>
rem  Packs the folder itself, so the zip carries that folder as its top entry.
rem ---------------------------------------------------------------------------
:zip
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%~1' -DestinationPath '%~2' -Force"
if errorlevel 1 (
  echo   ERROR: Compress-Archive failed for %~2.
  exit /b 1
)
if not exist "%~2" (
  echo   ERROR: %~2 was not created.
  exit /b 1
)
exit /b 0

:copyfail
echo   ERROR: copy failed.

:fail
echo.
pause
endlocal & exit /b 1
