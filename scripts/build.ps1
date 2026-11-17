# 统一构建入口：每次编译前清理上一次的残留，避免 os error 5（文件被占用/旧资源目录）。
# 用法：
#   powershell -File scripts/build.ps1              # 清理 + 调试构建
#   powershell -File scripts/build.ps1 -Mode release # 清理 + 发行安装包（含运行时资源）
#   powershell -File scripts/build.ps1 -Mode all     # 两者都做
#   powershell -File scripts/build.ps1 -NoClean      # 跳过清理（增量构建）
param(
  [ValidateSet("debug", "release", "all")]
  [string]$Mode = "debug",
  [switch]$NoClean,
  [switch]$WithRuntime
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function Clear-BuildLeftovers([string]$profile) {
  $target = Join-Path $root "src-tauri/target/$profile"
  if (-not (Test-Path $target)) { return }

  # 1) 运行中的 exe 会锁文件：先结束进程（只结束本项目的）
  Get-Process svc-studio -ErrorAction SilentlyContinue | ForEach-Object {
    try {
      $path = $_.Path
      if ($path -and $path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Host "    结束占用进程 pid=$($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
      }
    } catch { }
  }

  # 2) tauri 的 resources 副本与 build script 输出：残留会触发 os error 5
  foreach ($name in @("resources", "bundle")) {
    $path = Join-Path $target $name
    if (Test-Path $path) {
      Write-Host "    清理 $profile/$name"
      Remove-Item -Recurse -Force $path -ErrorAction SilentlyContinue
    }
  }
  $buildDir = Join-Path $target "build"
  if (Test-Path $buildDir) {
    Get-ChildItem $buildDir -Directory -Filter "svc-studio-*" -ErrorAction SilentlyContinue |
      ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }
  }
}

function Invoke-DebugBuild {
  Write-Step "调试构建"
  & pnpm.cmd tauri build --debug --no-bundle
  if ($LASTEXITCODE -ne 0) { throw "调试构建失败（exit $LASTEXITCODE）" }
  Write-Host "    产物：src-tauri/target/debug/svc-studio.exe"
}

function Invoke-ReleaseBuild {
  if ($WithRuntime) {
    Write-Step "发行构建（含运行时资源；>2GB 会被 NSIS 32 位限制拒绝）"
    if (-not (Test-Path "src-tauri/resources/MANIFEST.json")) {
      throw "资源未 staged：先运行 scripts/stage-runtime.ps1"
    }
    & pnpm.cmd tauri build --config src-tauri/tauri.bundle.json
  } else {
    Write-Step "发行构建（瘦安装包，不含运行时；运行时用 install-runtime.ps1 单独安装）"
    & pnpm.cmd tauri build
  }
  if ($LASTEXITCODE -ne 0) { throw "发行构建失败（exit $LASTEXITCODE）" }
  Get-ChildItem "src-tauri/target/release/bundle/nsis" -Filter "*.exe" -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host ("    产物：{0}（{1} MB）" -f $_.Name, [math]::Round($_.Length / 1MB, 1)) }
}

$started = Get-Date
if (-not $NoClean) {
  Write-Step "清理上一次构建残留"
  if ($Mode -in @("debug", "all")) { Clear-BuildLeftovers "debug" }
  if ($Mode -in @("release", "all")) { Clear-BuildLeftovers "release" }
}

if ($Mode -in @("debug", "all")) { Invoke-DebugBuild }
if ($Mode -in @("release", "all")) { Invoke-ReleaseBuild }

Write-Host ("完成：{0}，用时 {1} 秒" -f $Mode, [math]::Round(((Get-Date) - $started).TotalSeconds, 1)) -ForegroundColor Green
