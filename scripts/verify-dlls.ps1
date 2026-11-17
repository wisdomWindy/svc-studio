# DLL 完整性门禁（H3）：比对 resources/MANIFEST.json 与安装目录，并检查关键 DLL。
# 用法：powershell -File scripts/verify-dlls.ps1 [-InstallDir <path>] [-DryRun]

param(
  [string]$InstallDir = "src-tauri/resources",
  # 允许指定清单：H3 自测会用合成清单验证"能通过也能拦住"
  [string]$ManifestPath = "src-tauri/resources/MANIFEST.json",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$manifestPath = $ManifestPath
if (-not (Test-Path $manifestPath)) {
  Write-Host "缺少 staging 清单：$manifestPath（先运行 stage-runtime.ps1）" -ForegroundColor Yellow
  if ($DryRun) { exit 0 } else { exit 2 }
}

$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
Write-Host "清单文件数：$($manifest.count)"

$names = @($manifest.files | ForEach-Object { [System.IO.Path]::GetFileName($_.path) })
function Find-Any($pattern) { @($names | Where-Object { $_ -like $pattern }) }

# CPU 版 torch 与 CUDA 版的要求不同：先判定运行时类型，再决定必须项。
$hasCuda = (Find-Any "torch_cuda.dll").Count -gt 0
$runtimeKind = if ($hasCuda) { "cuda" } else { "cpu" }
Write-Host "运行时类型：$runtimeKind"

$critical = @("c10.dll", "torch_cpu.dll")
if ($hasCuda) { $critical += @("cudnn64_8.dll", "nvrtc64_*.dll") }
$missing = @()
foreach ($pattern in $critical) {
  if ((Find-Any $pattern).Count -eq 0) { $missing += $pattern }
}

if ($missing.Count -gt 0) {
  Write-Host ("缺少关键 DLL：" + ($missing -join "，")) -ForegroundColor Red
  if ($DryRun) { exit 0 } else { exit 1 }
}

# 与安装目录逐文件比对（安装后校验时用 -InstallDir）
if (Test-Path $InstallDir) {
  $bad = @()
  foreach ($file in $manifest.files) {
    if ($file.path -notmatch "\.dll$") { continue }
    $target = Join-Path $InstallDir $file.path
    if (-not (Test-Path $target)) { $bad += "缺少 $($file.path)"; continue }
    $hash = (Get-FileHash -Algorithm SHA256 -Path $target).Hash.ToLower()
    if ($hash -ne $file.sha256) { $bad += "哈希不符 $($file.path)" }
  }
  if ($bad.Count -gt 0) {
    $bad | Select-Object -First 20 | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    if ($DryRun) { exit 0 } else { exit 1 }
  }
}

Write-Host "DLL 完整性检查通过。" -ForegroundColor Green
exit 0
