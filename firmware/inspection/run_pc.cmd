@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup_pc.ps1 first.
    exit /b 1
)
set "BASE_PYTHON="
for /f "delims=" %%I in ('.venv\Scripts\python.exe -c "import sys; print(sys._base_executable)"') do set "BASE_PYTHON=%%I"
if not defined BASE_PYTHON (
    echo Could not resolve the base Python interpreter. Run setup_pc.ps1 again.
    exit /b 1
)
if not exist "%BASE_PYTHON%" (
    echo The base Python interpreter is missing. Run setup_pc.ps1 again.
    exit /b 1
)
echo PC_PYTHON=%BASE_PYTHON%
"%BASE_PYTHON%" "%~dp0run_pc.py" %*
exit /b %errorlevel%
