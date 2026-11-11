@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo Slynor - setup for the EXISTING Python project
 echo ============================================================
echo Requires Windows 10/11 x64, Internet and winget (App Installer).
echo This will install/check Python 3.13, VLC, WebView2 and FFmpeg
echo and download Python packages into .venv, .providers and .search.
echo It accepts winget package/source agreements and updates pip.
echo System installers may request administrator approval.
echo This does NOT install Rust or create a Rust version of the app.
echo Deno is optional for certain YouTube providers and is not installed.
echo.
if exist "runtime_paths.json" (
 echo STOP: runtime_paths.json already exists in this folder.
 echo The existing INSTALL.ps1 would overwrite it. Preserve the file and
 echo use a fresh extracted project folder for setup.
 pause
 exit /b 1
)
if not exist "INSTALL.ps1" (
 echo ERROR: INSTALL.ps1 is missing. Extract the complete archive.
 pause
 exit /b 1
)
choice /C YN /N /M "Continue with dependency installation? [Y/N]: "
if errorlevel 2 exit /b 0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" (
 echo Setup failed with exit code %RESULT%. Read the error above.
 echo After fixing it, run this file again. No library was deleted.
) else (
 echo Setup completed. Start the existing app with START.cmd.
)
pause
exit /b %RESULT%
