@echo off
setlocal

if not defined LOCALAPPDATA (
	echo ERROR: LOCALAPPDATA is not set.
	goto :error
)

set "EXE=%LOCALAPPDATA%\MonitorApp\monitor.exe"
if not exist "%EXE%" (
	echo ERROR: "%EXE%" was not found. Run build_exe.bat first.
	goto :error
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" ^
	/v MonitorApp /t REG_SZ /d "\"%EXE%\"" /f
if errorlevel 1 goto :error

echo Done. monitor.exe will start automatically at login.
pause
exit /b 0

:error
echo Could not configure monitor.exe to start automatically.
pause
exit /b 1
