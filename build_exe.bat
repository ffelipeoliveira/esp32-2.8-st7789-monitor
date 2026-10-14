@echo off
REM Builds monitor.py into a single standalone monitor.exe.
REM Run this once yourself (needs Python) -- the resulting .exe needs no
REM Python install on the machine you copy it to.

python -m pip install --upgrade pyinstaller requests pyserial pystray pillow
python -m PyInstaller --onefile --windowed --name monitor monitor.py

echo.
echo Done. Find it at dist\monitor.exe
pause
