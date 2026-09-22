@echo off
setlocal EnableExtensions

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "EXPO_DIR=%ROOT%\expo"
set "TRACKER_PID=%EXPO_DIR%\.tracker.pid"
set "BLENDER_PID=%EXPO_DIR%\.blender.pid"

echo ==========================================
echo        LEAP MOTION EXPO SHUTDOWN
echo ==========================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "$files=@('%TRACKER_PID%','%BLENDER_PID%'); $roots=@(); foreach($file in $files){ if(Test-Path -LiteralPath $file){ $roots += [int](Get-Content -Raw $file).Trim() } }; function Get-Tree([int]$id){ $children=@(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$id) -ErrorAction SilentlyContinue); foreach($child in $children){ Get-Tree $child.ProcessId }; return $id }; $targets=@(); foreach($root in $roots){ if(Get-Process -Id $root -ErrorAction SilentlyContinue){ $targets += @(Get-Tree $root) } }; $targets | Sort-Object -Descending -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; foreach($file in $files){ Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }"

echo EXPO STOP COMPLETE
endlocal
exit /b 0
