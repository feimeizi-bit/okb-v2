# 宿主侧 VBox 快照（跑批前 take / 灾后 restore / 列表）
# 用法：
#   powershell -ExecutionPolicy Bypass -File deploy\snapshot-vm.ps1 take pre-run_20260923_001
#   powershell -ExecutionPolicy Bypass -File deploy\snapshot-vm.ps1 restore pre-run_20260923_001
#   powershell -ExecutionPolicy Bypass -File deploy\snapshot-vm.ps1 list
# 注意：take 可在运行中执行（在线快照）；restore 需先关闭 VM。
param(
    [Parameter(Mandatory = $true)][ValidateSet("take", "restore", "list")][string]$Action,
    [string]$Name = "",
    [string]$VmName = "openKylin-V3.0",
    [string]$VBoxManage = "D:\tools\VirtualBox\VBoxManage.exe"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $VBoxManage)) {
    throw "VBoxManage 不存在：$VBoxManage（用 -VBoxManage 指定）"
}
switch ($Action) {
    "take" {
        if (-not $Name) { throw "take 需要快照名（如 pre-run_xxx）" }
        & $VBoxManage snapshot $VmName take $Name
        Write-Host "快照已创建：$VmName / $Name"
    }
    "restore" {
        if (-not $Name) { throw "restore 需要快照名" }
        & $VBoxManage snapshot $VmName restore $Name
        Write-Host "已回滚到快照：$VmName / $Name"
    }
    "list" {
        & $VBoxManage snapshot $VmName list
    }
}
