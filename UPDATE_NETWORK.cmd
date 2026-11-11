@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Main .venv not found. See docs/archive/README_STAGE18.md.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" -m pip install "httpx[socks]>=0.28.1,<1"
if errorlevel 1 goto failed
set "PROVIDER=.venv\Scripts\python.exe"
if exist ".providers\Scripts\python.exe" set "PROVIDER=.providers\Scripts\python.exe"
"%PROVIDER%" -m pip install "PySocks>=1.7.1,<2"
if errorlevel 1 goto failed
echo Network dependencies updated. Start the application and configure your proxy.
pause
exit /b 0
:failed
echo Update failed. Keep the error text. No virtual environments were deleted.
pause
exit /b 1
