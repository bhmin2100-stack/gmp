@echo off
setlocal EnableExtensions

cd /d "%~dp0"
title GMP Company EXE Builder
set "VENV_DIR=.venv-company-py312"

set "NO_PAUSE=0"
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"

echo ========================================
echo GMP Company EXE Builder
echo ========================================
echo.
echo Close GMP-Scheduler.exe before continuing.
echo This window will prepare everything and build the company EXE.
echo.

if not exist "requirements.txt" (
    echo [ERROR] requirements.txt was not found.
    goto :Failed
)

if not exist "build-company-release.ps1" (
    echo [ERROR] build-company-release.ps1 was not found.
    goto :Failed
)

if not exist "requirements-company-build.txt" (
    echo [ERROR] requirements-company-build.txt was not found.
    goto :Failed
)

if not exist "download-company-build.ps1" (
    echo [ERROR] download-company-build.ps1 was not found.
    goto :Failed
)

echo [1/4] Checking Python...
call :FindPython
if not defined PYTHON_EXE (
    echo Python 3.11/3.12 was not found.
    echo Downloading the matching company EXE built by GitHub Actions instead...
    call :DownloadPrebuilt
    if not errorlevel 1 goto :BuildComplete
    goto :PrebuiltDownloadFailed
)

if not defined PYTHON_EXE goto :PythonInstallFailed
echo Python: %PYTHON_EXE% %PYTHON_ARGS%
"%PYTHON_EXE%" %PYTHON_ARGS% --version
if errorlevel 1 goto :PythonInstallFailed
echo.

echo [2/4] Preparing the private Python environment...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    "%PYTHON_EXE%" %PYTHON_ARGS% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Could not create %VENV_DIR%.
        goto :Failed
    )
) else (
    echo Existing %VENV_DIR% will be reused.
)
echo.

echo [3/4] Checking build packages...
"%VENV_DIR%\Scripts\python.exe" -c "import PySide6, openpyxl, holidays, PyInstaller; assert PyInstaller.__version__ == '6.8.0'" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages. This can take several minutes...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-company-build.txt
    if errorlevel 1 (
        echo Package installation failed. Trying the matching GitHub Actions build...
        call :DownloadPrebuilt
        if not errorlevel 1 goto :BuildComplete
        echo [ERROR] The package installation and prebuilt download both failed.
        echo Check the company network, proxy, or package-download policy.
        goto :Failed
    )
) else (
    echo Required packages and the transition-compatible builder are already installed.
)
echo.

echo [4/4] Building GMP-Scheduler.exe for the company channel...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\build-company-release.ps1" -Clean
if errorlevel 1 (
    echo [ERROR] The company EXE build failed.
    goto :Failed
)

:BuildComplete
set "RESULT_EXE="
for /r "%CD%\dist" %%F in (GMP-Scheduler.exe) do if exist "%%~fF" set "RESULT_EXE=%%~fF"

if not defined RESULT_EXE (
    echo [ERROR] The build finished, but GMP-Scheduler.exe was not found.
    goto :Failed
)

echo.
echo ========================================
echo SUCCESS
echo %RESULT_EXE%
echo ========================================
echo.

if "%NO_PAUSE%"=="0" start "" explorer.exe /select,"%RESULT_EXE%"
if "%NO_PAUSE%"=="0" pause
exit /b 0

:DownloadPrebuilt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\download-company-build.ps1"
exit /b %ERRORLEVEL%

:FindPython
set "PYTHON_EXE="
set "PYTHON_ARGS="

where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3.12"
        exit /b 0
    )
    py -3.11 --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3.11"
        exit /b 0
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; assert (3, 11) ^<= sys.version_info[:2] ^< (3, 13)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=python"
        exit /b 0
    )
)

for /f "delims=" %%P in ('dir /b /s "%LocalAppData%\Programs\Python\Python3*\python.exe" 2^>nul') do (
    "%%P" -c "import sys; assert (3, 11) ^<= sys.version_info[:2] ^< (3, 13)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=%%P"
        exit /b 0
    )
)

exit /b 0

:PythonInstallFailed
echo.
echo [ERROR] Python 3.11 or 3.12 could not be used for a local build.
echo Run this BAT on a PC with Python, or use the prebuilt download on a PC without Python.
goto :Failed

:PrebuiltDownloadFailed
echo.
echo [ERROR] The matching prebuilt company EXE could not be downloaded.
echo Pull the latest main branch, wait for GitHub Actions to finish, and run this BAT again.
echo Python is not required for the prebuilt download.
goto :Failed

:Failed
echo.
echo Build did not complete.
if "%NO_PAUSE%"=="0" pause
exit /b 1
