# 桌面开发：同时拉起 Vite 与 Tauri（Tauri 会等待 devUrl 就绪）。
# 用法：pwsh scripts/dev.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "node_modules")) {
  Write-Host "首次运行，安装依赖…" -ForegroundColor Cyan
  & pnpm.cmd install
}

# 开发版把数据放仓库内（gitignore），避免跟着 target/debug 到处跑
$env:SVCSTUDIO_DATA_ROOT = Join-Path $root ".devdata"
New-Item -ItemType Directory -Force -Path $env:SVCSTUDIO_DATA_ROOT | Out-Null
Write-Host "数据目录：$env:SVCSTUDIO_DATA_ROOT" -ForegroundColor DarkGray

& pnpm.cmd tauri dev
