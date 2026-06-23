@echo off
setlocal
cd /d "%~dp0"

:: Set up paths to binaries and node
set "PATH=%~dp0bin\ffmpeg;%~dp0bin\magick;%~dp0bin\vips\bin;%~dp0vendor\node;%PATH%"

:: Set up database path
set "PIXELPIVOT_DB_PATH=%~dp0data\pixelpivot.db"

:: Create data directory if it doesn't exist
if not exist "%~dp0data" mkdir "%~dp0data"

:: Determine which python to use (prefer virtual environment)
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
) else if exist "%~dp0python-3.14.5-embed-amd64\python.exe" (
    set "PYTHON_EXE=%~dp0python-3.14.5-embed-amd64\python.exe"
    set "PYTHONPATH=%~dp0vendor\site-packages"
) else (
    set "PYTHON_EXE=python"
)

echo Starting PixelPivot Terminal UI...
"%PYTHON_EXE%" -m app.cli tui

endlocal
