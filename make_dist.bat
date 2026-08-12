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
rem  Never shipped: out\, state\ and settings\ (all three made at
rem  runtime), __pycache__ / *.pyc, tools\test_*.py and the one-off save fixers, and
rem  llm_replacements.txt / npc.json -- those last two are the player's own copy
rem  of a mod's data file, so only the shipped *.default.* versions go in.
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

rem  Every .md under docs\ ships. Naming them one by one is how
rem  VERIFICATION_LOG.md was silently left out of 1.6.0 after the split: the
rem  file was added, README kept listing it as part of the installed tree, and
rem  nothing here or in CI noticed. docs\ holds only documents meant for the
rem  player or the mod author, so the wildcard is the honest rule. The zip check
rem  in .github\workflows\ci.yml asserts that each of them arrived.
echo   [loader] docs ...
md "%LOADER%\docs" 2>nul
if not exist "docs\README.md" (
  echo   ERROR: docs\README.md is missing.
  goto :fail
)
copy /y "docs\*.md" "%LOADER%\docs\" >nul || goto :copyfail

rem ===========================================================================
rem  mods
rem ===========================================================================
echo   [mods] copying ...
robocopy "runtime\mods" "%MODS%" /E /XD "__pycache__" "_template" /XF "*.pyc" "llm_replacements.txt" "npc.json" "load_order.json" "load_order.local.json" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed on runtime\mods ^(code %ERRORLEVEL%^).
  goto :fail
)

rem  Only ship what load_order.json names. A mod that is still being written
rem  is listed in load_order.local.json instead (git-ignored, see .gitignore),
rem  so it is on disk and the robocopy above already copied it -- this is the
rem  one place that keeps it out of a release. Dropping it here rather than
rem  failing means a release still builds while an unfinished mod sits in the
rem  working tree.
for /d %%d in ("%MODS%\*") do (
  if exist "%%d\mod.json" (
    findstr /c:"\"%%~nxd\"" "runtime\mods\load_order.json" >nul || (
      echo   [mods] skipping %%~nxd ^(not listed in load_order.json^)
      rd /s /q "%%d"
    )
  )
)

rem  A pack with no mod.json in it would install as nothing. Catch that here
rem  rather than after it is uploaded. Counted after the skip above, so a
rem  load_order.json that names nothing installed is caught too.
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
rem
rem  The entries are written one by one instead of with Compress-Archive.
rem  Windows PowerShell 5.1 ships a Compress-Archive that emits a bare
rem  directory entry for any folder holding no files directly -- runtime\ is
rem  exactly that, holding only instantale_modloader\ and mods\ -- and it
rem  writes that entry with no attributes set. Extractors that read the
rem  attributes rather than the trailing slash then create a 0-byte *file*
rem  named runtime, and every runtime\... file after it has nowhere to go.
rem  Explorer copes, several other archivers do not.
rem
rem  ZipFile::CreateFromDirectory is not the fix: on .NET Framework, which is
rem  what 5.1 runs on, it separates entry names with "\" instead of "/", which
rem  breaks a different set of extractors.
rem
rem  So: open the archive and add each file under a forward-slash relative
rem  name. Empty folders are dropped, which costs nothing here -- the loader
rem  reads files, and out\, state\ and settings\ are made at runtime anyway.
rem ---------------------------------------------------------------------------
:zip
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { Add-Type -AssemblyName System.IO.Compression.FileSystem; $src=(Resolve-Path -LiteralPath '%~1').Path; $dst=[IO.Path]::GetFullPath([IO.Path]::Combine((Get-Location).Path,'%~2')); if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }; $base=Split-Path -Parent $src; $zip=[IO.Compression.ZipFile]::Open($dst,'Create'); try { Get-ChildItem -LiteralPath $src -Recurse -File | ForEach-Object { $rel=$_.FullName.Substring($base.Length+1).Replace('\','/'); [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,$_.FullName,$rel,'Optimal') | Out-Null } } finally { $zip.Dispose() } } catch { Write-Host $_.Exception.Message; exit 1 }"
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
