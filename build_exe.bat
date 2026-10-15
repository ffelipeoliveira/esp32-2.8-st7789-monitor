@echo off
setlocal
cd /d "%~dp0" || goto :error

if not defined LOCALAPPDATA (
    echo ERROR: LOCALAPPDATA is not set.
    goto :error
)

python -m pip install --upgrade pyinstaller requests pyserial pystray pillow
if errorlevel 1 goto :error

python -m PyInstaller --onefile --windowed --name monitor monitor.py
if errorlevel 1 goto :error
if not exist "dist\monitor.exe" (
    echo ERROR: PyInstaller did not create dist\monitor.exe.
    goto :error
)

REM Copy exe to a stable location
set "DEST=%LOCALAPPDATA%\MonitorApp"
if not exist "%DEST%" mkdir "%DEST%"
if errorlevel 1 goto :error
copy /Y "dist\monitor.exe" "%DEST%\monitor.exe" >nul
if errorlevel 1 goto :error
if not exist "%DEST%\monitor.exe" (
    echo ERROR: The executable was not copied to "%DEST%".
    goto :error
)

echo Build complete. monitor.exe is installed at "%DEST%\monitor.exe".
pause
exit /b 0

:error
echo Build or installation failed.
pause
exit /b 1