@echo off
setlocal

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python 3 is not installed. Please install Python 3.9 or newer from https://www.python.org/downloads/
        exit /b 1
    )
    set "PYTHON_CMD=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo 环境准备就绪，请运行 python app.py
endlocal
