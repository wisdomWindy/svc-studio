# H3 门禁自测：合成一份 MANIFEST + 目录，验证 verify-dlls.ps1
#   1) 齐全 → exit 0
#   2) 篡改一个 DLL 1 字节 → exit 1（哈希不符）
#   3) 删除关键 DLL → exit 1（缺失）
# 用法：powershell -File scripts/test-verify-dlls.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$work = Join-Path (Get-Location) "tmp/h3-selftest"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force -Path (Join-Path $work "runtime") | Out-Null

$critical = @("c10.dll", "torch_cpu.dll", "torch_cuda.dll", "cudnn64_8.dll", "nvrtc64_120_0.dll")
foreach ($name in $critical) {
  $bytes = New-Object byte[] 4096
  (New-Object System.Random -ArgumentList 42).NextBytes($bytes)
  $dest = Join-Path (Join-Path $work "runtime") $name
  [System.IO.File]::WriteAllBytes($dest, $bytes)
}

function New-Manifest($dir) {
  $files = Get-ChildItem -Recurse -File $dir | ForEach-Object {
    [ordered]@{
      path   = $_.FullName.Substring((Resolve-Path $dir).Path.Length + 1).Replace("\", "/")
      bytes  = $_.Length
      sha256 = (Get-FileHash -Algorithm SHA256 -Path $_.FullName).Hash.ToLower()
    }
  }
  [ordered]@{ generatedAt = (Get-Date).ToString("o"); count = @($files).Count; files = @($files) } |
    ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $work "MANIFEST.json") -Encoding UTF8
}

function Invoke-Check {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/verify-dlls.ps1 `
    -ManifestPath (Join-Path $work "MANIFEST.json") -InstallDir (Join-Path $work "runtime") *> $null
  return $LASTEXITCODE
}

New-Manifest (Join-Path $work "runtime")
$results = @()

$code = Invoke-Check
$results += [ordered]@{ case = "intact"; exit = $code; expect = 0; pass = ($code -eq 0) }

# 篡改 1 字节
$target = Join-Path $work "runtime/c10.dll"
$bytes = [System.IO.File]::ReadAllBytes($target)
$bytes[0] = [byte](($bytes[0] + 1) % 256)
[System.IO.File]::WriteAllBytes($target, $bytes)
$code = Invoke-Check
$results += [ordered]@{ case = "tampered"; exit = $code; expect = 1; pass = ($code -eq 1) }

# 删除关键 DLL
Remove-Item -Force (Join-Path $work "runtime/torch_cuda.dll")
$code = Invoke-Check
$results += [ordered]@{ case = "missing-critical"; exit = $code; expect = 1; pass = ($code -eq 1) }

$out = "docs/requests/intent_770b99fe990a4a0d/evidence/H3/verify-dlls-selftest.json"
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
[ordered]@{
  generatedAt = (Get-Date).ToString("o")
  cases       = $results
  passed      = (@($results | Where-Object { $_.pass }).Count)
  total       = $results.Count
} | ConvertTo-Json -Depth 4 | Set-Content -Path $out -Encoding UTF8

$failed = @($results | Where-Object { -not $_.pass })
if ($failed.Count -gt 0) {
  Write-Host ("H3 自测失败：" + ($failed.case -join ", ")) -ForegroundColor Red
  exit 1
}
Write-Host "H3 自测通过：齐全放行 / 篡改拦截 / 缺失拦截" -ForegroundColor Green
exit 0
