$ErrorActionPreference = "Stop"
$TaskName = "F.R.I.D.A.Y Mark 6"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "F.R.I.D.A.Y startup disabled."
} else {
    Write-Host "F.R.I.D.A.Y startup was not enabled."
}
