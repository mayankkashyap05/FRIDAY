$ErrorActionPreference = "Stop"
$Removed = $false
foreach ($TaskName in @("Friday", "F.R.I.D.A.Y", "F.R.I.D.A.Y Mark 6")) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($Task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        $Removed = $true
    }
}
if ($Removed) {
    Write-Host "Friday startup disabled."
} else {
    Write-Host "Friday startup was not enabled."
}
