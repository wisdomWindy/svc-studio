# 打包运行时并上传到 GitHub Release（分卷 <2GB）
param(
  [string]$Tag = "v0.1.0",
  [int]$PartMB = 1800
)

$ErrorActionPreference = "Stop"
$dataRoot = "D:\program\svcstudio"
$dist = Join-Path $dataRoot "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$log = Join-Path $dist "pack.log"
function Log($m) { $line = "$(Get-Date -Format 'HH:mm:ss') $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

$zip = Join-Path $dist "runtime-0.1.0.zip"
if (-not (Test-Path $zip)) {
  Log "打包 runtime -> zip（约 3.5GB）"
  & tar.exe -a -c -f $zip -C (Join-Path $dataRoot "runtime") .
  if ($LASTEXITCODE -ne 0) { throw "tar 打包失败" }
}
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Log "zip 完成：$zipMb MB"

# 分卷
$parts = @()
$fs = [System.IO.File]::OpenRead($zip)
try {
  $partBytes = $PartMB * 1MB
  $index = 1
  $buf = New-Object byte[] (4MB)
  while ($true) {
    $partPath = Join-Path $dist ("runtime-0.1.0-part{0:D2}.bin" -f $index)
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
    $sizeMb = [math]::Round($written / 1MB, 1)
    $hash = (Get-FileHash -Algorithm SHA256 $partPath).Hash.ToLower()
    $parts += [pscustomobject]@{ Name = (Split-Path $partPath -Leaf); Path = $partPath; Mb = $sizeMb; Sha256 = $hash }
    Log ("分卷 {0}：{1} MB  sha256={2}" -f $index, $sizeMb, $hash)
    $index++
    if ($fs.Position -ge $fs.Length) { break }
  }
} finally { $fs.Close() }

# 上传
$in = "protocol=https`nhost=github.com`n`n"
$cred = $in | git credential fill 2>&1
$token = ($cred | Where-Object { $_ -match "^password=" }) -replace "^password=", ""
$env:GH_TOKEN = $token
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
$env:HTTP_PROXY = "http://127.0.0.1:7897"
foreach ($part in $parts) {
  Log "上传 $($part.Name)（$($part.Mb) MB）"
  gh release upload $Tag $part.Path --clobber 2>&1 | ForEach-Object { Log "  $_" }
  if ($LASTEXITCODE -ne 0) { Log "  上传失败，稍后重试" }
}

# 写出清单，供应用读取
$manifest = [ordered]@{
  schema = "svcstudio.runtime-pack/1"
  tag = $Tag
  zipSha256 = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
  zipBytes = (Get-Item $zip).Length
  parts = $parts | ForEach-Object { [ordered]@{ name = $_.Name; mb = $_.Mb; sha256 = $_.Sha256 } }
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $dist "runtime-manifest.json") -Encoding UTF8
Log "完成，清单：$dist\runtime-manifest.json"
