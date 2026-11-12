@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found. Install Python and try again.
    exit /b 1
)

python -c "import PyInstaller, pygame, vgamepad, PIL" >nul 2>nul
if errorlevel 1 (
    echo ERROR: The build environment is missing a required package.
    echo Install the build dependencies with:
    echo   python -m pip install pyinstaller pygame vgamepad Pillow
    exit /b 1
)

if not exist "controller.png" (
    echo ERROR: controller.png is missing from the project folder.
    exit /b 1
)

if not exist "icon.ico" (
    echo ERROR: icon.ico is missing from the project folder.
    exit /b 1
)

if exist "dist\ControllerMerger\" (
    attrib -R "dist\ControllerMerger" /S /D
    attrib -R "dist\ControllerMerger\*.*" /S /D
)

if not exist "build\" mkdir "build"

python -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --name ControllerMerger ^
    --icon "icon.ico" ^
    --add-data "controller.png;." ^
    --add-data "icon.ico;." ^
    --collect-all pygame ^
    --collect-all vgamepad ^
    controller_merger.py

if errorlevel 1 (
    echo ERROR: Packaging failed.
    exit /b 1
)

echo.
echo Package created in: %~dp0dist\ControllerMerger
echo HidHide is not required.
echo ViGEmBus is still required for the virtual Xbox controller.
exit /b 0
