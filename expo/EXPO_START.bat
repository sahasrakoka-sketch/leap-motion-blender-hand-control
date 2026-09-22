@echo off
setlocal

echo ==========================================
echo       LEAP MOTION EXPO LAUNCHER
echo ==========================================
echo.

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "TRACKER_BAT=%ROOT%\leap1\run_tracker.bat"
set "BLENDER=C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
set "BLEND=%ROOT%\leap_hand_control_EXPO.blend"
set "RECEIVER=%ROOT%\blender\receiver.py"
set "CONTROLS=%ROOT%\blender\expo_controls.py"

echo [1/3] Checking files...

if not exist "%TRACKER_BAT%" (
    echo ERROR: Tracker BAT not found
    echo %TRACKER_BAT%
    pause
    exit /b 1
)

if not exist "%BLENDER%" (
    echo ERROR: Blender not found
    echo %BLENDER%
    pause
    exit /b 1
)

if not exist "%BLEND%" (
    echo ERROR: EXPO blend file not found
    echo %BLEND%
    pause
    exit /b 1
)

if not exist "%RECEIVER%" (
    echo ERROR: receiver.py not found
    echo %RECEIVER%
    pause
    exit /b 1
)

if not exist "%CONTROLS%" (
    echo ERROR: expo_controls.py not found
    echo %CONTROLS%
    pause
    exit /b 1
)

echo [2/3] Starting Leap Motion tracker...

start "" /D "%ROOT%\leap1" cmd /c call "%TRACKER_BAT%"

timeout /t 3 /nobreak >nul

echo [3/3] Starting Blender EXPO...

start "" "%BLENDER%" "%BLEND%" --python "%RECEIVER%" --python "%CONTROLS%"

echo.
echo ==========================================
echo          EXPO STARTED
echo ==========================================
echo.
echo Scene: %BLEND%
echo.
echo Use EXPO_STOP.bat to stop the EXPO session.

endlocal
exit /b 0