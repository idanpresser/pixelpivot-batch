@echo off
cls
:menu
echo =======================================================================
echo                   PixelPivot Standalone Distribution
echo =======================================================================
echo This directory contains the standalone PixelPivot Batch Engine.
echo Choose one of the options below to run a command:
echo.
echo [1] Run System Doctor (Validate dependencies: FFmpeg, Magick, libvips)
echo [2] Start Terminal UI (TUI Control Plane - monitors/manages the engine)
echo [3] Start REST API Server (FastAPI backend on port 8000)
echo [4] Show CLI Help
echo [5] Exit
echo.
set /p choice="Enter your choice (1-5): "

if "%choice%"=="1" (
    echo.
    echo Running doctor...
    "%~dp0pixelpivot.exe" doctor
    echo.
    pause
    cls
    goto :menu
)
if "%choice%"=="2" (
    echo.
    echo Starting TUI...
    "%~dp0pixelpivot.exe" tui
    cls
    goto :menu
)
if "%choice%"=="3" (
    echo.
    echo Starting API Server...
    "%~dp0pixelpivot.exe" serve
    cls
    goto :menu
)
if "%choice%"=="4" (
    echo.
    echo Showing help...
    "%~dp0pixelpivot.exe" --help
    echo.
    pause
    cls
    goto :menu
)
if "%choice%"=="5" (
    exit /b
)

echo Invalid choice. Please try again.
pause
cls
goto :menu
