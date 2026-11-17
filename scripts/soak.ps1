# G5 浸泡：长跑应用 + 定时采样内存 + 周期性轻任务
# 用法：powershell -File scripts/soak.ps1 [-Hours 24] [-Minutes 6]
param(
  [int]$Hours = 24,
  [int]$Minutes = 0,
  [int]$SampleSeconds = 300
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$totalSeconds = if ($Minutes -gt 0) { $Minutes * 60 } else { $Hours * 3600 }
$outDir = Join-Path $root "docs/requests/intent_770b99fe990a4a0d/evidence/G5"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
$csv = Join-Path $outDir "soak-$stamp.csv"

$python = Join-Path $root "runtime_gpu/python.exe"
if (-not (Test-Path $python)) { $python = Join-Path $root "runtime/python.exe" }
if (-not (Test-Path $python)) { throw "找不到内置运行时：不允许使用系统 Python" }

Write-Host "==> 启动应用"
$app = Join-Path $root "src-tauri/target/debug/svc-studio.exe"
$proc = Start-Process -FilePath $app -PassThru
Start-Sleep -Seconds 5

"ts,seconds,workingSetMB,privateMB,handleCount,appAlive" | Set-Content $csv -Encoding ASCII
$started = Get-Date
$nextSample = 0
$failures = 0

while ($true) {
  $elapsed = ((Get-Date) - $started).TotalSeconds
  if ($elapsed -ge $totalSeconds) { break }

  $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
  if (-not $alive) {
    $failures++
    "app died at $([math]::Round($elapsed,0))s" | Add-Content $csv -Encoding ASCII
    break
  }

  if ($elapsed -ge $nextSample) {
    $ws = [math]::Round($alive.WorkingSet64 / 1MB, 1)
    $priv = [math]::Round($alive.PrivateMemorySize64 / 1MB, 1)
    "$(Get-Date -Format o),$([math]::Round($elapsed,0)),$ws,$priv,$($alive.HandleCount),1" |
      Add-Content $csv -Encoding ASCII
    Write-Host ("  t={0,6}s  WS={1,7}MB  Private={2,7}MB  handles={3}" -f [math]::Round($elapsed,0), $ws, $priv, $alive.HandleCount)
    $nextSample = $elapsed + $SampleSeconds
  }

  # 每 30 分钟跑一次轻量任务（doctor），验证子进程链路没被长期运行拖坏
  if ($elapsed -ge 1800 -and [math]::Floor($elapsed / 1800) -gt [math]::Floor(($elapsed - 60) / 1800)) {
    try {
      & $python -m svc_bridge doctor --json --data-root "D:\program\svcstudio" | Out-Null
    } catch {
      $failures++
      "worker failure at $([math]::Round($elapsed,0))s" | Add-Content $csv -Encoding ASCII
    }
  }

  Start-Sleep -Seconds 30
}

$alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
$endWs = if ($alive) { [math]::Round($alive.WorkingSet64 / 1MB, 1) } else { 0 }
$firstWs = (Get-Content $csv | Select-Object -Skip 1 -First 1) -split "," | Select-Object -Index 2
$growthPct = if ($firstWs -and [double]$firstWs -gt 0) { [math]::Round((([double]$endWs - [double]$firstWs) / [double]$firstWs) * 100, 1) } else { 0 }

$summary = [ordered]@{
  schema = "svcstudio.soak/1"
  startedAt = $started.ToString("o")
  finishedAt = (Get-Date).ToString("o")
  durationSeconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 0)
  plannedSeconds = $totalSeconds
  samples = (Get-Content $csv | Measure-Object -Line).Lines - 1
  firstWorkingSetMB = $firstWs
  lastWorkingSetMB = $endWs
  growthPercent = $growthPct
  appAliveAtEnd = [bool]$alive
  failures = $failures
  csv = (Split-Path $csv -Leaf)
  verdict = if ($failures -eq 0 -and $growthPct -lt 10) { "pass" } else { "fail" }
}
$summary | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $outDir "soak-$stamp.json") -Encoding UTF8
if ($alive) { Stop-Process -Id $proc.Id -Force }
Write-Host ("==> 完成：{0} 秒，{1} 个样本，内存增长 {2}%，失败 {3} 次 → {4}" -f $summary.durationSeconds, $summary.samples, $growthPct, $failures, $summary.verdict)
