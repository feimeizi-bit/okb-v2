# Start recording the openKylin VM desktop (VirtualBox built-in recorder -> WebM)
# Usage: powershell -ExecutionPolicy Bypass -File record-start.ps1
$ErrorActionPreference = "Stop"
$vbm = "D:\tools\VirtualBox\VBoxManage.exe"
$vm  = "openKylin-V3.0"
$deliverables = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$file = Join-Path $deliverables "recording-$stamp.webm"

& $vbm controlvm $vm recording filename "$file"
& $vbm controlvm $vm recording videofps 25
& $vbm controlvm $vm recording videores=1280x800
& $vbm controlvm $vm recording videorate 4096
& $vbm controlvm $vm recording start

Write-Host ""
Write-Host "Recording STARTED (VM screen only)." -ForegroundColor Green
Write-Host "  Output: recording-<stamp>-screen0.webm in deliverables/  (VirtualBox inserts -screen0 before .webm)"
Write-Host "  Stop with: record-stop.ps1"
