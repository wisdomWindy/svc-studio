# 从 runtime 包安装 / 修复内置运行时
# 用法：powershell -File scripts/install-runtime.ps1 -Zip "D:\path\runtime-0.1.0.zip"
param(
  [Parameter(Mandatory = $true)][string]$Zip,
  [string]$Target = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path $Zip)) { throw "找不到运行时包：$Zip" }
if (-not $Target) {
  $ptr = Join-Path $env:LOCALAPPDATA "svc-studio\data-root.txt"
  $dataRoot = if (Test-Path $ptr) { (Get-Content $ptr -Raw).Trim() } else { Join-Path $env:USERPROFILE "SVCStudio" }
  $Target = Join-Path $dataRoot "runtime"
}

Write-Host "==> 目标目录：$Target"
if (Test-Path $Target) {
  $answer = Read-Host "目录已存在，覆盖？(y/N)"
  if ($answer -ne "y" -and $answer -ne "Y") { throw "已取消" }
  Remove-Item -Recurse -Force $Target
}
New-Item -ItemType Directory -Force -Path $Target | Out-Null

Write-Host "==> 解压（约 3.5GB → 7.6GB，需要几分钟）"
& tar.exe -x -f $Zip -C $Target
if ($LASTEXITCODE -ne 0) { throw "解压失败（tar exit $LASTEXITCODE）" }

$python = Join-Path $Target "python.exe"
if (-not (Test-Path $python)) { throw "解压后找不到 python.exe，包可能损坏" }

Write-Host "==> 自检"
& $python -c "import sys, torch; print('python', sys.version.split()[0], 'torch', torch.__version__, 'cuda', torch.cuda.is_available())"
if ($LASTEXITCODE -ne 0) { throw "运行时自检失败" }

Write-Host "==> 完成：$Target" -ForegroundColor Green
