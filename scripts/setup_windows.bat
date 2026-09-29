@echo off
setlocal enabledelayedexpansion

echo ==================================================================
echo    Vision AI Studio - Automated Windows Environment Setup
echo ==================================================================
echo.

:: 1. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in system PATH.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo and ensure "Add Python to PATH" is checked during installation.
    pause
    exit /b 1
)

echo [Step 1/3] Verifying and installing required AI dependencies...
python scripts\bootstrap_env.py
if %errorlevel% neq 0 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo [Step 2/3] Installing Node.js frontend dependencies...
call npm install
if %errorlevel% neq 0 (
    echo [ERROR] npm install failed.
    pause
    exit /b 1
)

echo.
echo [Step 3/3] Compiling desktop application...
call npm run build
if %errorlevel% neq 0 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo ==================================================================
echo    [SUCCESS] Setup Completed! You can now run or package:
echo      - To start development: npm start
echo      - To package installer: npm run package:win
echo ==================================================================
pause
