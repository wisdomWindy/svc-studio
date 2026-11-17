# 桌面开发：同时拉起 Vite 与 Tauri（Tauri 会等待 devUrl 就绪）。
# 用法：pwsh scripts/dev.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "node_modules")) {
  Write-Host "首次运行，安装依赖…" -ForegroundColor Cyan
  & pnpm.cmd install
}

& pnpm.cmd tauri dev
