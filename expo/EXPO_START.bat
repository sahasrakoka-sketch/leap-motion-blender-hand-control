@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "EXPO_DIR=%ROOT%\expo"
set "TRACKER_BAT=%ROOT%\leap1\run_tracker.bat"
set "RECEIVER=%ROOT%\blender\receiver.py"
set "CONTROLS=%ROOT%\blender\expo_controls.py"
set "BLEND=%ROOT%\leap_hand_control_EXPO.blend"
set "TRACKER_PID=%EXPO_DIR%\.tracker.pid"
set "BLENDER_PID=%EXPO_DIR%\.blender.pid"

 echo ==========================================
 echo        LEAP MOTION EXPO LAUNCHER
 echo ==========================================
 echo.
 echo [1/4] Checking project...

for %%F in ("%TRACKER_BAT%" "%RECEIVER%" "%CONTROLS%" "%BLEND%") do (
    if not exist "%%~F" (
        echo ERROR: Missing required file:
        echo %%~F
        pause
        exit /b 1
    )
)

if exist "%TRACKER_PID%" if exist "%BLENDER_PID%" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ids=@((Get-Content -Raw '%TRACKER_PID%'),(Get-Content -Raw '%BLENDER_PID%')) | %% { [int]$_.Trim() }; if (@($ids | ? { Get-Process -Id $_ -ErrorAction SilentlyContinue }).Count -gt 0) { exit 0 } else { exit 1 }"
    if not errorlevel 1 (
        echo EXPO is already running.
        pause
        exit /b 0
    )
    del /q "%TRACKER_PID%" "%BLENDER_PID%" 2>nul
)

set "BLENDER=C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
if not exist "%BLENDER%" set "BLENDER="
if not defined BLENDER where blender.exe >nul 2>&1
if not defined BLENDER if not errorlevel 1 (
    for /f "delims=" %%B in ('where blender.exe') do if not defined BLENDER set "BLENDER=%%B"
)
if not defined BLENDER (
    for /d %%D in ("C:\Program Files\Blender Foundation\Blender *") do if exist "%%~fD\blender.exe" if not defined BLENDER set "BLENDER=%%~fD\blender.exe"
)
if not defined BLENDER (
    echo ERROR: Blender was not found in PATH or C:\Program Files\Blender Foundation\.
    pause
    exit /b 1
)

echo [2/4] Starting Leap Motion tracker...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process -FilePath 'cmd.exe' -ArgumentList @('/d','/c','%TRACKER_BAT%') -WorkingDirectory '%ROOT%\leap1' -PassThru; Set-Content -LiteralPath '%TRACKER_PID%' -Value $p.Id"
if errorlevel 1 (
    echo ERROR: Could not start the tracker.
    pause
    exit /b 1
)

timeout /t 3 /nobreak >nul

echo [3/4] Starting Blender...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process -FilePath '%BLENDER%' -ArgumentList @('"%BLEND%"','--python','"%RECEIVER%"','--python','"%CONTROLS%"') -WorkingDirectory '%ROOT%\blender' -PassThru; Set-Content -LiteralPath '%BLENDER_PID%' -Value $p.Id"
if errorlevel 1 (
    echo ERROR: Could not start Blender.
    call "%EXPO_DIR%\EXPO_STOP.bat"
    pause
    exit /b 1
)

echo [4/4] EXPO READY
echo Blender: %BLEND%
echo Use EXPO_STOP.bat to stop only this EXPO session.
endlocal
exit /b 0
