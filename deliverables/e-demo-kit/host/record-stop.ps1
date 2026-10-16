# Stop recording and show the produced file.
# Usage: powershell -ExecutionPolicy Bypass -File record-stop.ps1
$ErrorActionPreference = "Stop"
$vbm = "D:\tools\VirtualBox\VBoxManage.exe"
$vm  = "openKylin-V3.0"
& $vbm controlvm $vm recording stop

$deliverables = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$latest = Get-ChildItem $deliverables -Filter "recording-*.webm" |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) {
    Write-Host ""
    Write-Host ("Recording STOPPED. File: {0} ({1:N1} MB)" -f $latest.FullName, ($latest.Length / 1MB)) -ForegroundColor Green
    Write-Host "Rename to recording-<date>.webm before submitting if you like. WebM plays in most players/browsers."
} else {
    Write-Host "No recording-*.webm found" -ForegroundColor Yellow
}
