# 下载并缓存 ffmpeg（内置到安装包）
# 用法：powershell -File scripts/fetch-ffmpeg.ps1
param(
  [string]$CacheDir = "assets/bin",
  [string]$Url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
$target = Join-Path $CacheDir "ffmpeg.exe"

if (Test-Path $target) {
  Write-Host "已存在：$target（$([math]::Round((Get-Item $target).Length / 1MB, 1)) MB）"
  exit 0
}

# 本机 PATH 上已有就直接复用，省一次 80MB 下载
$onPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($onPath) {
  Copy-Item $onPath.Source $target -Force
  Write-Host "复用本机 ffmpeg：$($onPath.Source)"
  exit 0
}

$zip = Join-Path $env:TEMP "ffmpeg-essentials.zip"
Write-Host "下载 ffmpeg（约 80MB）"
& curl.exe -L --fail --retry 3 --retry-delay 2 -C - -o $zip $Url
if ($LASTEXITCODE -ne 0) { throw "ffmpeg 下载失败" }

$extract = Join-Path $env:TEMP "ffmpeg-extract"
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -Path $zip -DestinationPath $extract -Force
$exe = Get-ChildItem -Path $extract -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
if (-not $exe) { throw "压缩包里没有 ffmpeg.exe" }
Copy-Item $exe.FullName $target -Force
Remove-Item -Recurse -Force $extract
Remove-Item $zip -Force
Write-Host "完成：$target（$([math]::Round((Get-Item $target).Length / 1MB, 1)) MB）"
