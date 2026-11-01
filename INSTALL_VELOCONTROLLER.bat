@echo off
setlocal EnableExtensions
title VeloController Installer

set "TASK_NAME=VeloController"
set "SOURCE_EXE=%~dp0VeloController.exe"
set "INSTALL_DIR=%LOCALAPPDATA%\VeloController"
set "DEST_EXE=%INSTALL_DIR%\VeloController.exe"
set "VC_INSTALLED_EXE=%DEST_EXE%"

echo ================================================
echo VeloController installation
echo ================================================
echo.

if not exist "%SOURCE_EXE%" goto :source_missing

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if errorlevel 1 goto :install_error

echo Stopping an older installed copy if necessary...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-Process -Name 'VeloController' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $env:VC_INSTALLED_EXE } | Stop-Process -Force -ErrorAction SilentlyContinue"

copy /Y "%SOURCE_EXE%" "%DEST_EXE%" >nul
if errorlevel 1 goto :install_error

echo Installed EXE:
echo %DEST_EXE%
echo.
echo Creating Windows scheduled task...

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $exe=$env:VC_INSTALLED_EXE; $dir=Split-Path -Parent $exe; $userId=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name; $action=New-ScheduledTaskAction -Execute $exe -WorkingDirectory $dir; $atLogon=New-ScheduledTaskTrigger -AtLogOn -User $userId; $watchdog=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10); $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; $principal=New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited; $task=New-ScheduledTask -Action $action -Trigger @($atLogon,$watchdog) -Settings $settings -Principal $principal -Description 'VeloController startup and 10 minute watchdog'; Register-ScheduledTask -TaskName $env:TASK_NAME -InputObject $task -Force | Out-Null; Start-ScheduledTask -TaskName $env:TASK_NAME"
if errorlevel 1 goto :task_error

timeout /t 4 /nobreak >nul

echo.
echo ================================================
echo Installation completed successfully
echo ================================================
echo.
echo Scheduled task status:
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$task=Get-ScheduledTask -TaskName $env:TASK_NAME; $info=Get-ScheduledTaskInfo -TaskName $env:TASK_NAME; Write-Host ('State: ' + $task.State); Write-Host ('Last run: ' + $info.LastRunTime); Write-Host ('Next run: ' + $info.NextRunTime); Write-Host ('Last result: ' + $info.LastTaskResult)"

echo.
echo VeloController has been started by Task Scheduler.
echo The task uses IgnoreNew and the EXE also uses a Windows mutex.
echo To test the mutex, run this file twice:
echo %DEST_EXE%
echo Only one application window must remain open.
echo.
pause
exit /b 0

:source_missing
echo ERROR: VeloController.exe was not found next to this BAT file.
echo Put these two files in the same folder:
echo   INSTALL_VELOCONTROLLER.bat
echo   VeloController.exe
echo.
pause
exit /b 1

:task_error
echo.
echo ERROR: Windows Task Scheduler configuration failed.
echo Try running this BAT file as Administrator.
echo.
pause
exit /b 2

:install_error
echo.
echo ERROR: Could not copy the application to:
echo %INSTALL_DIR%
echo.
pause
exit /b 3
