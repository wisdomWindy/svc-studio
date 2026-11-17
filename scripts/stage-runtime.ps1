# 把运行时、上游源码与前端产物 staged 到 src-tauri/resources，并生成 DLL/文件清单。
# tauri.conf.json 的 bundle.resources 指向这里；安装后 ~ 随包分发。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/stage-runtime.ps1 [-DryRun]

param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$resources = "src-tauri/resources"
$required = @("runtime/python.exe", "runtime/worker/svc_bridge/__init__.py", "vendor/so-vits-svc/train.py")

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

$missing = @()
foreach ($item in $required) { if (-not (Test-Path $item)) { $missing += $item } }

Write-Step "检查待 staged 的目录"
Write-Host ("- runtime/: " + (Test-Path "runtime"))
Write-Host ("- vendor/so-vits-svc/: " + (Test-Path "vendor/so-vits-svc"))
Write-Host ("- assets/manifest.json: " + (Test-Path "assets/manifest.json"))

if ($missing.Count -gt 0) {
  Write-Host ("缺少：" + ($missing -join "，") + "。先运行 scripts/build-runtime.ps1，或仅做开发构建。") -ForegroundColor Yellow
  if (-not $DryRun) {
    Write-Warning "缺少 runtime：跳过运行时 staging（瘦安装包不需要）；仍会 stage vendor / assets / ffmpeg"
  }
}

Write-Step "计划"
Write-Host "1) 清空并重建 $resources"
Write-Host "2) 复制 runtime/ → resources/runtime，vendor/so-vits-svc → resources/upstream"
Write-Host "3) 复制 assets/manifest.json → resources/assets"
Write-Host "4) 生成 resources/MANIFEST.json（相对路径 + 大小 + sha256，排除 *.pyc/__pycache__）"
Write-Host "5) 打印体积汇总（预算：安装包 ≤2.2GB）"

if ($DryRun) {
  Write-Host "`nDryRun 完成：未写入资源目录。" -ForegroundColor Green
  exit 0
}

if (Test-Path $resources) { Remove-Item -Recurse -Force $resources }
New-Item -ItemType Directory -Force -Path $resources | Out-Null

if (Test-Path "runtime") {
  Copy-Item -Recurse -Force "runtime" (Join-Path $resources "runtime")
  # runtime/worker 可能是构建时的旧副本：用源码覆盖，避免"装的是旧版 worker"
  $stagedWorker = Join-Path $resources "runtime/worker"
  if (Test-Path $stagedWorker) { Remove-Item -Recurse -Force $stagedWorker }
  New-Item -ItemType Directory -Force -Path $stagedWorker | Out-Null
} else {
  Write-Host "    跳过 runtime（瘦安装包：运行时单独安装）"
  $stagedWorker = $null
}
Copy-Item -Recurse -Force "worker\*" $stagedWorker
if (Test-Path "vendor/so-vits-svc") {
  Copy-Item -Recurse -Force "vendor/so-vits-svc" (Join-Path $resources "upstream")
}
New-Item -ItemType Directory -Force -Path (Join-Path $resources "assets") | Out-Null
Copy-Item -Force "assets/manifest.json" (Join-Path $resources "assets/manifest.json")

Write-Step "生成 MANIFEST.json"
$entries = Get-ChildItem -Recurse -File $resources |
  Where-Object { $_.FullName -notmatch "__pycache__|\.pyc$" }
$manifest = foreach ($file in $entries) {
  $rel = $file.FullName.Substring((Resolve-Path $resources).Path.Length + 1).Replace("\", "/")
  [ordered]@{
    path  = $rel
    bytes = $file.Length
    sha256 = (Get-FileHash -Algorithm SHA256 -Path $file.FullName).Hash.ToLower()
  }
}
$payload = [ordered]@{ generatedAt = (Get-Date).ToString("o"); count = @($manifest).Count; files = @($manifest) }
$payload | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $resources "MANIFEST.json") -Encoding UTF8

$totalGb = [math]::Round(((Get-ChildItem -Recurse -File $resources | Measure-Object Length -Sum).Sum / 1GB), 2)
Write-Host "`nstaging 完成：$resources（$($payload.count) 个文件，$totalGb GB）" -ForegroundColor Green

# 内置 ffmpeg（转码需要；没有就现取）
$ffmpegSrc = Join-Path $root "assets/bin/ffmpeg.exe"
if (-not (Test-Path $ffmpegSrc)) {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts/fetch-ffmpeg.ps1")
}
if (Test-Path $ffmpegSrc) {
  $binDir = Join-Path $root "src-tauri/resources/bin"
  New-Item -ItemType Directory -Force -Path $binDir | Out-Null
  Copy-Item $ffmpegSrc (Join-Path $binDir "ffmpeg.exe") -Force
  Write-Host "    已内置 ffmpeg（$([math]::Round((Get-Item $ffmpegSrc).Length/1MB,1)) MB）"
} else {
  Write-Warning "没有找到 ffmpeg，安装包将不含内置 ffmpeg"
}
