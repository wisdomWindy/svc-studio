# 运行时打包：每个 Release 都要重新出一份
#
# 用法（发版前）：
#   powershell -File scripts/pack-runtime-release.ps1 -Tag v0.1.2
#
# 做的事：
#   1) 用当前安装目录里的 runtime 打 zip（约 3.7GB）
#   2) 切成 <2GB 分卷（GitHub 单附件上限）
#   3) 逐卷 sha256
#   4) 上传到指定 tag 的 Release（不存在则创建 draft）
#   5) 生成并上传 runtime-manifest.json（应用按自己的版本号去取）
param(
  [Parameter(Mandatory = $true)][string]$Tag,
  [string]$DataRoot = "D:\program\svcstudio\app",
  [int]$PartMB = 1800
)

$ErrorActionPreference = "Stop"
$dist = Join-Path $DataRoot "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$log = Join-Path $dist "pack-$Tag.log"
function Log($m) { $line = "$(Get-Date -Format 'HH:mm:ss') $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

$version = $Tag.TrimStart("v")
$zip = Join-Path $dist "runtime-$version.zip"
if (-not (Test-Path $zip)) {
  Log "打包 runtime -> $zip（约 3.7GB）"
  & tar.exe -a -c -f $zip -C (Join-Path $DataRoot "runtime") .
  if ($LASTEXITCODE -ne 0) { throw "tar 打包失败" }
}
Log "zip 完成：$([math]::Round((Get-Item $zip).Length/1MB,1)) MB"

# 分卷
$parts = @()
$fs = [System.IO.File]::OpenRead($zip)
try {
  $partBytes = $PartMB * 1MB
  $index = 1
  $buf = New-Object byte[] (4MB)
  while ($true) {
    $partPath = Join-Path $dist ("runtime-$version-part{0:D2}.bin" -f $index)
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
    $hash = (Get-FileHash -Algorithm SHA256 $partPath).Hash.ToLower()
    $parts += [pscustomobject]@{ Name = (Split-Path $partPath -Leaf); Path = $partPath; Mb = [math]::Round($written/1MB,1); Sha256 = $hash }
    Log ("分卷 {0}：{1} MB  sha256={2}" -f $index, $parts[-1].Mb, $hash)
    $index++
    if ($fs.Position -ge $fs.Length) { break }
  }
} finally { $fs.Close() }

# 鉴权
$in = "protocol=https`nhost=github.com`n`n"
$cred = $in | git credential fill 2>&1
$env:GH_TOKEN = ($cred | Where-Object { $_ -match "^password=" }) -replace "^password=", ""
if (-not $env:HTTPS_PROXY) { $env:HTTPS_PROXY = "http://127.0.0.1:7897" }
if (-not $env:HTTP_PROXY) { $env:HTTP_PROXY = $env:HTTPS_PROXY }

Set-Location (Split-Path -Parent $PSScriptRoot)
gh release view $Tag *> $null
if ($LASTEXITCODE -ne 0) {
  Log "创建 draft Release $Tag（运行时专用）"
  gh release create $Tag --title "SVC Studio 运行时包 $version" --notes "运行时压缩包（分卷）。应用首次启动会自动下载并解压；手动安装：合并分卷后解压到 <安装目录>\runtime。" --draft 2>&1 | ForEach-Object { Log $_ }
}

$manifest = [ordered]@{
  schema    = "svcstudio.runtime-pack/1"
  tag       = $Tag
  version   = $version
  zipSha256 = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
  zipBytes  = (Get-Item $zip).Length
  parts     = $parts | ForEach-Object { [ordered]@{ name = $_.Name; mb = $_.Mb; sha256 = $_.Sha256 } }
}
$manifestPath = Join-Path $dist "runtime-manifest.json"
$manifest | ConvertTo-Json -Depth 4 | Set-Content $manifestPath -Encoding UTF8

foreach ($name in @("runtime-manifest.json") + ($parts | ForEach-Object { $_.Name })) {
  $path = Join-Path $dist $name
  Log "上传 $name"
  gh release upload $Tag $path --clobber 2>&1 | ForEach-Object { Log "  $_" }
  Log "  exit=$LASTEXITCODE"
}
Log "完成：$Tag"
