# 宿主机 → VM 同步仓库工作副本（开发期用；VM 内没有 git 凭据）
# 用法：powershell -ExecutionPolicy Bypass -File deploy\sync-to-vm.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$key = Join-Path $env:USERPROFILE ".ssh\okb_vm"
$target = "okb@127.0.0.1:/home/okb/okb/"
# v2/ 也同步（v2 工作区的卷/计划/断言库是跑批输入；2026-10-10 补）
$dirs = @("engine", "schemas", "tools", "samples", "deploy", "docs", "notes", "v2", "okb")
foreach ($d in $dirs) {
    $p = Join-Path $repo $d
    if (Test-Path -LiteralPath $p) {
        scp -P 2222 -i $key -r $p $target
    } else {
        Write-Host "跳过不存在：$d"
    }
}
# 根文件同步：okb_portable.py 是 test_portable 的必需输入（2026-10-08 真机复验发现漏同步）；
# runs/ 不在此列（体量大、由 VM 本地产出；历史 run 需手工 scp）。
foreach ($f in @("README.md", "HANDOVER.md", "pyproject.toml", "okb_portable.py")) {
    $p = Join-Path $repo $f
    if (Test-Path -LiteralPath $p) {
        scp -P 2222 -i $key $p $target
    } else {
        Write-Host "跳过不存在：$f"
    }
}
Write-Host "同步完成 -> $target"
