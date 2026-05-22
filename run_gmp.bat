@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo ========================================
echo GMP 근무표 자동 생성기 실행파일
echo ========================================
echo.

echo 이 실행기는 Python이 없으면 자동 설치를 시도합니다.
echo 인터넷 연결이 필요할 수 있습니다.
echo.

call :FindPython
if not "%PYTHON_CMD%"=="" goto PythonReady

echo [준비] Python을 찾을 수 없습니다. 자동 설치를 시도합니다.
echo.

call :InstallPython
if errorlevel 1 (
    echo.
    echo [오류] Python 자동 설치에 실패했습니다.
    echo 아래 주소에서 Python 3.11 이상을 직접 설치한 뒤 다시 실행하세요.
    echo https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

call :FindPython
if "%PYTHON_CMD%"=="" (
    echo.
    echo [오류] Python 설치 후에도 실행 파일을 찾지 못했습니다.
    echo PC를 재부팅한 뒤 run_gmp.bat을 다시 실행해보세요.
    pause
    exit /b 1
)

:PythonReady
echo [확인] Python 사용: %PYTHON_CMD%
%PYTHON_CMD% --version
if errorlevel 1 (
    echo [오류] Python 실행 확인 실패
    pause
    exit /b 1
)
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 가상환경을 생성합니다...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [오류] 가상환경 생성 실패
        pause
        exit /b 1
    )

    echo [2/3] 필요한 패키지를 설치합니다...
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [오류] 패키지 설치 실패
        echo 인터넷 연결 또는 백신/방화벽 설정을 확인하세요.
        pause
        exit /b 1
    )
) else (
    echo [1/3] 기존 가상환경을 사용합니다.
    call .venv\Scripts\activate.bat
)

echo [3/3] 프로그램을 실행합니다...
echo.
python main.py

if errorlevel 1 (
    echo.
    echo [오류] 프로그램 실행 중 문제가 발생했습니다.
    pause
    exit /b 1
)

endlocal
exit /b 0

:FindPython
set "PYTHON_CMD="

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 --version >nul 2>nul
    if !errorlevel!==0 (
        set "PYTHON_CMD=py -3"
        exit /b 0
    )
)

where python >nul 2>nul
if %errorlevel%==0 (
    python --version >nul 2>nul
    if !errorlevel!==0 (
        set "PYTHON_CMD=python"
        exit /b 0
    )
)

for %%P in (
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%~P (
        set "PYTHON_CMD=%%~P"
        exit /b 0
    )
)

exit /b 0

:InstallPython
where winget >nul 2>nul
if %errorlevel%==0 (
    echo [설치] winget으로 Python 3.11 설치를 시도합니다...
    winget install --id Python.Python.3.11 --exact --source winget --accept-package-agreements --accept-source-agreements --silent
    if not errorlevel 1 exit /b 0
    echo [안내] winget 설치 실패. python.org 설치 파일 방식으로 재시도합니다.
)

set "PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe"
set "PY_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"

echo [설치] Python 설치 파일 다운로드 중...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_INSTALLER%' -UseBasicParsing } catch { exit 1 }"
if errorlevel 1 exit /b 1

if not exist "%PY_INSTALLER%" exit /b 1

echo [설치] Python을 현재 사용자용으로 조용히 설치합니다...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0 SimpleInstall=1
if errorlevel 1 exit /b 1

echo [설치] 설치 반영 대기 중...
timeout /t 5 /nobreak >nul
exit /b 0
