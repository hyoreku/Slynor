@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
if exist "Slynor.exe" (
 start "Slynor runtime check" /wait "Slynor.exe" --check-runtime
 set "CHECK_RESULT=!ERRORLEVEL!"
 goto report
)
if not exist ".venv\Scripts\python.exe" (
 echo Run INSTALL.ps1 first.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" start.py --check-runtime
set "CHECK_RESULT=!ERRORLEVEL!"
:report
if exist "%LOCALAPPDATA%\Slynor\logs\runtime-check.json" type "%LOCALAPPDATA%\Slynor\logs\runtime-check.json"
echo.
echo See %%LOCALAPPDATA%%\Slynor\logs for reports and startup.log.
pause
exit /b %CHECK_RESULT%
