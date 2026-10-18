@echo off
rem ---------------------------------------------------------------------------
rem  Instantale mod loader -- distribution builder
rem
rem  Builds three release assets:
rem
rem    InstantaleModLoader-<ver>-full.zip            loader + every mod (for new players)
rem    InstantaleModLoader-<ver>-lite.zip            the same without the large assets
rem    InstantaleModLoader-<ver>-assets-<digest>.zip the large assets only
rem
rem  The large assets are the folders listed in ASSETDIRS below (the item
rem  images and their embeddings of 128_item_image_variety). They make up most
rem  of the full package and change only when images are added, so an update
rem  should not have to download them every time.
rem
rem  runtime\assets.json records a digest of those folders. It ships in the
rem  full and assets packages and is left out of the lite one, so an install
rem  keeps the digest of the assets it actually holds. The digest is also part
rem  of the assets zip name. The manager's update button compares the two:
rem  the same digest downloads the lite package, anything else the full one
rem  (tools\gui.py newer_release). Older managers only look for "-full.zip",
rem  so that name must not change.
rem
rem  The loader and the mods are not packaged separately any more: a fix that
rem  spans both breaks when only one side is updated.
rem
rem  load_order.json ships in the full and lite packages, which hold every mod
rem  it names. _template ships with them too: it is the starting point for
rem  writing a mod, and the loader skips folders starting with "_".
rem
rem  LICENSE and NOTICE are in every package, including the assets one: MIT
rem  requires the copyright notice to travel with every copy, so a package
rem  without them is not a lawful redistribution. NOTICE states what the grant
rem  does and does not cover (game-derived strings, GAME.md, the EULA question).
rem
rem  Never shipped: out\, state\ and settings\ (all three made at
rem  runtime), __pycache__ / *.pyc, tools\tests\test_*.py and the one-off save fixers, and
rem  llm_replacements.txt / npc.json / seeds.json / incidents.json -- those are the
rem  player's own copy of a mod's data file, so only the shipped *.default.*
rem  versions go in. The names are the *.default.* ones with ".default"
rem  dropped (SHIPPED_DEFAULTS and user_name() in tools\gui.py);
rem  tools\tests\test_llm_prompt_replace.py checks the /XF list against them.
rem  338_town_incidents\kinds\ holds incidents the player added, so it is not
rem  shipped either. /XD takes it as a full path (%CD%\...): a relative path there
rem  matched nothing and the folder was copied.
rem
rem    make_dist.bat           version comes from runtime\instantale_modloader
rem    make_dist.bat 1.2.0     override the version string
rem
rem  Staging trees are kept under dist\_stage\ for checking. The zips land in
rem  dist\<ver>\, and they are the only things meant to be uploaded.
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
set "VERFILE=runtime\instantale_modloader\__init__.py"
rem  The large assets (see the note at the top). Space separated, relative to
rem  the package root; none of the paths may contain a space.
set "ASSETDIRS=runtime\mods\128_item_image_variety\images runtime\mods\128_item_image_variety\data"

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
rem  All three unpack to InstantaleModLoader-<ver>\, so the lite and assets
rem  packages can be unpacked on top of an install by hand.
set "STAGE=dist\_stage"
set "FULL=%STAGE%\full\%NAME%-%VER%"
set "LITE=%STAGE%\lite\%NAME%-%VER%"
set "ASSETS=%STAGE%\assets\%NAME%-%VER%"
set "MODS=%FULL%\runtime\mods"

rem  The zips go into a folder per version (dist\<ver>\), next to that
rem  version's release notes, so building one version never touches another.
set "OUTDIR=dist\%VER%"
set "ZIPFULL=%OUTDIR%\%NAME%-%VER%-full.zip"
set "ZIPLITE=%OUTDIR%\%NAME%-%VER%-lite.zip"

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
rem  The old loader-only and mods-only zips are removed too, so a folder built
rem  by an older make_dist does not keep them next to the new ones.
for %%z in ("%ZIPFULL%" "%ZIPLITE%" "%OUTDIR%\%NAME%-%VER%.zip" "%OUTDIR%\InstantaleMods-%VER%.zip") do if exist "%%~z" del /q "%%~z"
if exist "%OUTDIR%\%NAME%-%VER%-assets-*.zip" del /q "%OUTDIR%\%NAME%-%VER%-assets-*.zip"
md "%OUTDIR%" 2>nul
if not exist "%OUTDIR%" (
  echo   ERROR: could not create %OUTDIR%.
  goto :fail
)
md "%FULL%" 2>nul
md "%LITE%" 2>nul
md "%ASSETS%" 2>nul
if not exist "%ASSETS%" (
  echo   ERROR: could not create the staging folders under %STAGE%.
  goto :fail
)

rem ===========================================================================
rem  full: loader
rem ===========================================================================
rem  The launcher is the only thing at the top level: double-clicking it opens
rem  the manager.
echo   [loader] launcher ...
if not exist "%NAME%.bat" (
  echo   ERROR: %NAME%.bat is missing.
  goto :fail
)
copy /y "%NAME%.bat" "%FULL%\" >nul || goto :copyfail

rem  /E keeps the tree, and __pycache__ / *.pyc never ship: they are built for
rem  one Python version and would shadow edited sources on another machine.
echo   [loader] runtime ...
robocopy "runtime\instantale_modloader" "%FULL%\runtime\instantale_modloader" /E /XD "__pycache__" /XF "*.pyc" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed on runtime\instantale_modloader ^(code %ERRORLEVEL%^).
  goto :fail
)
if not exist "runtime\mods\_template\mod.json" (
  echo   ERROR: runtime\mods\_template is missing.
  goto :fail
)
robocopy "runtime\mods\_template" "%FULL%\runtime\mods\_template" /E /XD "__pycache__" /XF "*.pyc" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed on runtime\mods\_template ^(code %ERRORLEVEL%^).
  goto :fail
)

rem  The GUI, the injector, the console watcher, the context probe (the 127_
rem  mod needs it to pick a safe window) and modtool.py -- the shared base
rem  every mod-bundled settings screen imports (TECH.md 3.12). Leave modtool.py
rem  out and the "Settings..." button opens nothing -- in the packaged build
rem  only, so the repo stays green (the packaging job in CI imports every
rem  shipped tool from the unpacked zip to catch it). This list is a whitelist: a new
rem  tools\ file that is not named here is dropped without a word, and so is
rem  a tools\ module one of these imports -- check_mods.py needs mods_meta.py.
rem  tools\tests\test_update.py checks that every import is named here.
rem  tools\tests\ and the one-off save fixers are development harnesses and
rem  stay out.
echo   [loader] tools ...
md "%FULL%\tools" 2>nul
for %%f in (gui.py injector.py watcher.py logrotate.py watch.bat check_mods.py
           mods_meta.py modtool.py llm_ctx_probe.py llm_ctx_probe.bat) do (
  if not exist "tools\%%f" (
    echo   ERROR: tools\%%f is missing.
    goto :fail
  )
  copy /y "tools\%%f" "%FULL%\tools\" >nul || goto :copyfail
)

rem  Every .md under docs\ ships. Naming them one by one is how
rem  VERIFICATION_LOG.md was silently left out of 1.6.0 after the split: the
rem  file was added, README kept listing it as part of the installed tree, and
rem  nothing here or in CI noticed. docs\ holds only documents meant for the
rem  player or the mod author, so the wildcard is the honest rule. The zip check
rem  in .github\workflows\ci.yml asserts that each of them arrived.
echo   [loader] docs ...
md "%FULL%\docs" 2>nul
if not exist "docs\README.md" (
  echo   ERROR: docs\README.md is missing.
  goto :fail
)
copy /y "docs\*.md" "%FULL%\docs\" >nul || goto :copyfail
rem  The verification records are one file per section under docs\verification\
rem  (VERIFICATION.md and VERIFICATION_LOG.md only hold their index).
md "%FULL%\docs\verification" 2>nul
copy /y "docs\verification\*.md" "%FULL%\docs\verification\" >nul || goto :copyfail

rem ===========================================================================
rem  full: mods
rem ===========================================================================
echo   [mods] copying ...
robocopy "runtime\mods" "%MODS%" /E /XD "__pycache__" "_template" "%CD%\runtime\mods\338_town_incidents\kinds" /XF "*.pyc" "llm_replacements.txt" "npc.json" "seeds.json" "incidents.json" "load_order.json" "load_order.local.json" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed on runtime\mods ^(code %ERRORLEVEL%^).
  goto :fail
)

rem  Only ship what load_order.json names. A mod that is still being written
rem  is listed in load_order.local.json instead (git-ignored, see .gitignore),
rem  so it is on disk and the robocopy above already copied it -- this is the
rem  one place that keeps it out of a release. Dropping it here rather than
rem  failing means a release still builds while an unfinished mod sits in the
rem  working tree. _template sits in the same folder now and is never named
rem  there, so folders starting with "_" are left alone (the loader skips them).
for /d %%d in ("%MODS%\*") do (
  set "MODDIR=%%~nxd"
  if not "!MODDIR:~0,1!"=="_" if exist "%%d\mod.json" (
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
for /d %%d in ("%MODS%\*") do (
  set "MODDIR=%%~nxd"
  if not "!MODDIR:~0,1!"=="_" if exist "%%d\mod.json" set /a MODCOUNT+=1
)
if "%MODCOUNT%"=="0" (
  echo   ERROR: no mod folders were staged. Check runtime\mods\.
  goto :fail
)

rem ===========================================================================
rem  full: license and load order
rem ===========================================================================
echo   copying license ...
for %%f in (LICENSE NOTICE) do (
  if not exist "%%f" (
    echo   ERROR: %%f is missing.
    goto :fail
  )
  copy /y "%%f" "%FULL%\" >nul || goto :copyfail
)
if exist "runtime\mods\load_order.json" (
  copy /y "runtime\mods\load_order.json" "%MODS%\" >nul || goto :copyfail
)

rem ===========================================================================
rem  assets digest
rem ===========================================================================
rem  sha256 over "<path><TAB><sha256 of the file><LF>" for every file under
rem  ASSETDIRS, paths with "/" and in ordinal order, so the same files always
rem  give the same digest. The first 12 hex digits are kept: enough to tell
rem  releases apart, short enough for a file name. runtime\assets.json is
rem  written without a BOM (Set-Content -Encoding UTF8 in PowerShell 5.1 adds one).
rem  The hashes come from .NET directly: Get-FileHash is a module cmdlet and was
rem  not found when make_dist ran from a PowerShell 7 console (its module path
rem  leaks into the Windows PowerShell started here).
echo   [assets] digest ...
set "DIGEST="
for /f "usebackq delims=" %%d in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $root=(Resolve-Path -LiteralPath '%FULL%').Path; $dirs='%ASSETDIRS%'.Split(' ',[StringSplitOptions]::RemoveEmptyEntries); $rows=New-Object Collections.Generic.List[string]; $h=[Security.Cryptography.SHA256]::Create(); foreach ($d in $dirs) { $p=Join-Path $root $d; if (-not (Test-Path -LiteralPath $p)) { throw ('missing ' + $d) }; foreach ($f in Get-ChildItem -LiteralPath $p -Recurse -File) { $rel=$f.FullName.Substring($root.Length+1).Replace('\','/'); $rows.Add($rel + [char]9 + (-join ($h.ComputeHash([IO.File]::ReadAllBytes($f.FullName)) | ForEach-Object { $_.ToString('x2') }))) } }; $arr=$rows.ToArray(); [Array]::Sort($arr,[StringComparer]::Ordinal); $text=($arr -join [char]10) + [char]10; $sha=[Security.Cryptography.SHA256]::Create(); $hex=-join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)) | ForEach-Object { $_.ToString('x2') }); $digest=$hex.Substring(0,12); $q=[char]34; $list=($dirs | ForEach-Object { $q + $_.Replace('\','/') + $q }) -join ', '; $json='{' + [char]10 + '  ' + $q + 'digest' + $q + ': ' + $q + $digest + $q + ',' + [char]10 + '  ' + $q + 'files' + $q + ': ' + $arr.Count + ',' + [char]10 + '  ' + $q + 'dirs' + $q + ': [' + $list + ']' + [char]10 + '}' + [char]10; [IO.File]::WriteAllText((Join-Path $root 'runtime\assets.json'), $json, (New-Object Text.UTF8Encoding($false))); Write-Output $digest } catch { Write-Output ('ERROR ' + $_.Exception.Message) }"`) do set "DIGEST=%%d"
if not defined DIGEST (
  echo   ERROR: could not compute the assets digest.
  goto :fail
)
if "%DIGEST:~0,5%"=="ERROR" (
  echo   %DIGEST%
  goto :fail
)
set "ZIPASSETS=%OUTDIR%\%NAME%-%VER%-assets-%DIGEST%.zip"
echo   [assets] digest %DIGEST%

rem ===========================================================================
rem  lite = full without ASSETDIRS and runtime\assets.json
rem ===========================================================================
echo   [lite] copying ...
set "LITEXD="
for %%a in (%ASSETDIRS%) do set LITEXD=!LITEXD! "%CD%\%FULL%\%%a"
robocopy "%FULL%" "%LITE%" /E /XD !LITEXD! /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo   ERROR: robocopy failed while building the lite package ^(code %ERRORLEVEL%^).
  goto :fail
)
if exist "%LITE%\runtime\assets.json" del /q "%LITE%\runtime\assets.json"
for %%a in (%ASSETDIRS%) do if exist "%LITE%\%%a" (
  echo   ERROR: %%a was copied into the lite package.
  goto :fail
)

rem ===========================================================================
rem  assets = ASSETDIRS + runtime\assets.json + license
rem ===========================================================================
echo   [assets] copying ...
for %%a in (%ASSETDIRS%) do (
  robocopy "%FULL%\%%a" "%ASSETS%\%%a" /E /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 (
    echo   ERROR: robocopy failed on %%a.
    goto :fail
  )
)
md "%ASSETS%\runtime" 2>nul
copy /y "%FULL%\runtime\assets.json" "%ASSETS%\runtime\" >nul || goto :copyfail
for %%f in (LICENSE NOTICE) do copy /y "%%f" "%ASSETS%\" >nul || goto :copyfail

rem ===========================================================================
rem  syntax check
rem ===========================================================================
rem  Same interpreter preference as watch.bat. The full package contains every
rem  .py that ships, so checking it covers the lite one (the assets one has none). Skipped when no
rem  Python is installed. The program and its arguments are kept apart, as in
rem  watch.bat: PYEXE is quoted when it runs (a path may hold spaces) and
rem  PYARGS carries the "-3" of the py launcher outside the quotes.
set "PYEXE="
set "PYARGS="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
  set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
) else (
  where py >nul 2>&1 && (
    set "PYEXE=py"
    set "PYARGS=-3"
  )
)
if not defined PYEXE (
  where python >nul 2>&1 && set "PYEXE=python"
)

if defined PYEXE (
  echo   syntax check ...
  "%PYEXE%" %PYARGS% -m compileall -q "%FULL%" >nul
  if errorlevel 1 (
    echo.
    echo   ERROR: a staged .py file does not compile. Nothing was zipped.
    "%PYEXE%" %PYARGS% -m compileall -q "%FULL%"
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
call :zip "%FULL%"   "%ZIPFULL%"   || goto :fail
call :zip "%LITE%"   "%ZIPLITE%"   || goto :fail
call :zip "%ASSETS%" "%ZIPASSETS%" || goto :fail

rem --- report -----------------------------------------------------------------
echo.
echo   done.  %MODCOUNT% mod(s) packaged.
echo.
for %%z in ("%ZIPFULL%" "%ZIPLITE%" "%ZIPASSETS%") do echo     %%~nxz  (%%~zz bytes)
echo.
echo   Upload the three zips in %OUTDIR%\ as separate release assets.
echo     -full    loader + mods. Unzip anywhere, run InstantaleModLoader.bat.
echo     -lite    the same without the large assets. The update button uses it
echo              when the assets have not changed.
echo     -assets  the large assets only, for unpacking on top of a lite install.
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
