# 迁移数据根（项目/模型/权重/数据库）到新磁盘。
# 用法：powershell -File scripts/migrate-data.ps1 -To D:\program\svcstudio [-DryRun]
#
# 做的事：
#   1) 停应用（如果还在跑）
#   2) robocopy /MOVE 搬移旧数据根
#   3) 写指针文件 %LOCALAPPDATA%\svc-studio\data-root.txt，应用启动时读它
#   4) 校验数据库与权重是否到位

param(
  [Parameter(Mandatory = $true)][string]$To,
  [string]$From = (Join-Path $env:USERPROFILE "SVCStudio"),
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

$target = [System.IO.Path]::GetFullPath($To)
$source = [System.IO.Path]::GetFullPath($From)
if ($target -eq $source) { throw "目标与源相同：$target" }

Write-Step "迁移计划"
Write-Host "从：$source"
Write-Host "到：$target"

if (-not (Test-Path $source)) {
  Write-Host "源目录不存在（可能已迁移过）。只写指针文件即可。" -ForegroundColor Yellow
}

Write-Step "停止应用"
Get-Process svc-studio -ErrorAction SilentlyContinue | ForEach-Object {
  Write-Host "结束进程 $($_.Id)"
  if (-not $DryRun) { Stop-Process -Id $_.Id -Force }
}

if ($DryRun) {
  Write-Host "`nDryRun：未搬移任何文件。" -ForegroundColor Green
  exit 0
}

New-Item -ItemType Directory -Force -Path $target | Out-Null

if (Test-Path $source) {
  Write-Step "搬移数据（robocopy /MOVE）"
  & robocopy $source $target /E /MOVE /NFL /NDL /NJH /NJS /NP | Out-Null
  # robocopy 0-7 都是成功
  if ($LASTEXITCODE -gt 7) { throw "robocopy 失败：exit $LASTEXITCODE" }
  if ((Test-Path $source) -and -not (Get-ChildItem $source -Force -ErrorAction SilentlyContinue)) {
    Remove-Item $source -Force -ErrorAction SilentlyContinue
  }
}

Write-Step "写指针文件"
$pointerDir = Join-Path $env:LOCALAPPDATA "svc-studio"
New-Item -ItemType Directory -Force -Path $pointerDir | Out-Null
$pointer = Join-Path $pointerDir "data-root.txt"
Set-Content -Path $pointer -Value $target -Encoding ASCII
Write-Host $pointer

Write-Step "校验"
$checks = [ordered]@{
  "studio.db"                                  = Test-Path (Join-Path $target "studio.db")
  "pretrain/checkpoint_best_legacy_500.pt"     = Test-Path (Join-Path $target "pretrain/checkpoint_best_legacy_500.pt")
  "pretrain/rmvpe.pt"                          = Test-Path (Join-Path $target "pretrain/rmvpe.pt")
  "pretrain/nsf_hifigan/model"                 = Test-Path (Join-Path $target "pretrain/nsf_hifigan/model")
}
$checks.GetEnumerator() | ForEach-Object { Write-Host ("- {0}: {1}" -f $_.Key, $_.Value) }
$missing = @($checks.GetEnumerator() | Where-Object { -not $_.Value })
if ($missing.Count -gt 0) {
  Write-Host "有缺失项，请确认源目录是否完整。" -ForegroundColor Yellow
}

$sizeGb = [math]::Round(((Get-ChildItem -Recurse -File $target | Measure-Object Length -Sum).Sum / 1GB), 2)
Write-Host "`n迁移完成：$target（$sizeGb GB）" -ForegroundColor Green
