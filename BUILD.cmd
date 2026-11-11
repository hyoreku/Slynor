@echo off
setlocal
cd /d "%~dp0"
echo Slynor: self-contained Windows build. Developer CPython 3.13 x64 and Inno Setup 6 required.
echo See docs\BUILD_PORTABLE_STAGE23.md for NativeBundle and embedded Python inputs.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\BUILD_WINDOWS.ps1" %*
if errorlevel 1 goto failed
echo EXE: dist\Slynor\Slynor.exe - keep the entire Slynor folder
echo Installer: dist\installer\Slynor-Setup.exe
echo Portable: dist\Slynor-Portable.zip
pause
exit /b 0
:failed
echo BUILD FAILED. Read the error above and build-logs if available.
pause
exit /b 1
