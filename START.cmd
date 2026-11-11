@echo off
cd /d "%~dp0"
rem Source updates must never launch an old EXE left next to the project.
if exist "start.py" goto source
if exist "Slynor.exe" (
 start "" "%~dp0Slynor.exe"
 exit /b 0
)
echo Slynor files were not found. Extract the complete archive.
pause
exit /b 1
:source
if not exist ".venv\Scripts\python.exe" (
 echo Run INSTALL.ps1 first. See docs/archive/README_STAGE19.md.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" start.py
if errorlevel 1 pause
