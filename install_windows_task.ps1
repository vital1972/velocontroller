param(
    [string]$ControllerPath = (Join-Path $PSScriptRoot "pedal_controller_windows.exe"),
    [string]$TaskName = "VeloController"
)

$ErrorActionPreference = "Stop"
$ControllerPath = [System.IO.Path]::GetFullPath($ControllerPath)

if (-not (Test-Path -LiteralPath $ControllerPath -PathType Leaf)) {
    throw "Файл контроллера не найден: $ControllerPath"
}

$extension = [System.IO.Path]::GetExtension($ControllerPath).ToLowerInvariant()
$workingDirectory = Split-Path -Parent $ControllerPath

if ($extension -eq ".py") {
    $pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $pythonw) {
        throw "pythonw.exe не найден в PATH. Установите Python или укажите путь к собранному EXE."
    }
    $action = New-ScheduledTaskAction `
        -Execute $pythonw.Source `
        -Argument ('"{0}"' -f $ControllerPath) `
        -WorkingDirectory $workingDirectory
}
elseif ($extension -eq ".exe") {
    $action = New-ScheduledTaskAction `
        -Execute $ControllerPath `
        -WorkingDirectory $workingDirectory
}
else {
    throw "Поддерживаются только файлы .exe и .py"
}

$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$everyTenMinutes = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 10)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$userId = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
$principal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($atLogon, $everyTenMinutes) `
    -Settings $settings `
    -Principal $principal `
    -Description "Запуск вело-контроллера при входе и проверка запуска каждые 10 минут"

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Задача '$TaskName' установлена и запущена."
Write-Host "Повторный запуск каждые 10 минут безопасен: Планировщик и mutex не допускают второй экземпляр."
