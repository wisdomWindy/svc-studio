# 把已构建好的 runtime/ 打成发布资产：zip → <2GB 分卷 → manifest
# 用法：powershell -File scripts/make-runtime-assets.ps1 -Version 0.1.3 -OutDir dist
param(
  [Parameter(Mandatory = $true)][string]$Version,
  [string]$RuntimeDir = "runtime",
  [string]$OutDir = "dist",
  [int]$PartMB = 1800
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not (Test-Path (Join-Path $RuntimeDir "python.exe"))) {
  throw "找不到 $RuntimeDir\python.exe，先运行 build-runtime.ps1"
}

$zip = Join-Path $OutDir "runtime-$Version.zip"
Write-Host "==> 打包 $RuntimeDir -> $zip"
& tar.exe -a -c -f $zip -C $RuntimeDir .
if ($LASTEXITCODE -ne 0) { throw "tar 打包失败" }
$zipBytes = (Get-Item $zip).Length
Write-Host "    zip $([math]::Round($zipBytes/1MB,1)) MB"

Write-Host "==> 切分卷（每卷 < $PartMB MB）"
$parts = @()
$fs = [System.IO.File]::OpenRead($zip)
try {
  $partBytes = $PartMB * 1MB
  $index = 1
  $buf = New-Object byte[] (4MB)
  while ($true) {
    $partPath = Join-Path $OutDir ("runtime-$Version-part{0:D2}.bin" -f $index)
    $out = [System.IO.File]::Create($partPath)
    $written = 0L
    try {
      while ($written -lt $partBytes) {
        $want = [Math]::Min($buf.Length, $partBytes - $written)
        $read = $fs.Read($buf, 0, $want)
        if ($read -le 0) { break }
        $out.Write($buf, 0, $read)
        $written += $read
      }
    } finally { $out.Close() }
    if ($written -eq 0) { Remove-Item $partPath -Force; break }
    $sha = (Get-FileHash -Algorithm SHA256 $partPath).Hash.ToLower()
    $parts += [ordered]@{ name = (Split-Path $partPath -Leaf); mb = [math]::Round($written/1MB,1); sha256 = $sha }
    Write-Host ("    分卷 {0}: {1} MB" -f $index, $parts[-1].mb)
    $index++
    if ($fs.Position -ge $fs.Length) { break }
  }
} finally { $fs.Close() }

$manifest = [ordered]@{
  schema    = "svcstudio.runtime-pack/1"
  version   = $Version
  zipSha256 = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
  zipBytes  = $zipBytes
  parts     = $parts
}
$manifestPath = Join-Path $OutDir "runtime-manifest.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content $manifestPath -Encoding UTF8

Write-Host "==> 清单：$manifestPath"
Get-ChildItem $OutDir -File | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
