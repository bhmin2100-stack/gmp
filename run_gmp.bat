@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ========================================
echo GMP 근무표 자동 생성기 임시 실행파일
echo ========================================
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    )
)

if "%PYTHON_CMD%"=="" (
    echo [오류] Python을 찾을 수 없습니다.
    echo Python 3.10 이상을 설치한 뒤 다시 실행하세요.
    echo https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

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
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [오류] 패키지 설치 실패
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
