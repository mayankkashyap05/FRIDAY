$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $ProjectRoot "Start-Friday.ps1"
$TaskName = "Friday"
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe"))) {
    throw "Virtual environment missing. Run .\Setup-Friday.ps1 first."
}

$Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`""
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description "Starts the Friday desktop assistant when the user signs in." -Force | Out-Null
Write-Host "Startup enabled. Friday will start when $CurrentUser signs in."
