# 一键环境准备：clone 之后跑一次，把运行时与权重都装好
# 用法：pnpm run setup         （或 -SkipRuntime / -SkipAssets 跳过某步）
param(
  [switch]$SkipRuntime,
  [switch]$SkipAssets,
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

# ---------- 数据根 ----------
# 数据根：环境变量 > 已有指针 > 仓库根（开发自包含；不写指针，避免影响已安装的正式版）
$pointer = Join-Path $env:LOCALAPPDATA "svc-studio\data-root.txt"
if ($env:SVCSTUDIO_DATA_ROOT) {
  $dataRoot = $env:SVCSTUDIO_DATA_ROOT
} elseif (Test-Path $pointer) {
  $dataRoot = (Get-Content $pointer -Raw).Trim()
} else {
  $dataRoot = $root
}
Write-Step "数据目录：$dataRoot"
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null

# ---------- 1. 内置运行时 ----------
$runtimePython = Join-Path $dataRoot "runtime\python.exe"
if (-not (Test-Path $runtimePython)) { $runtimePython = Join-Path $root "runtime\python.exe" }

if ($SkipRuntime) {
  Write-Host "    跳过运行时（-SkipRuntime）"
} elseif ((Test-Path $runtimePython) -and -not $Force) {
  Write-Host "    运行时已存在：$runtimePython"
} else {
  Write-Host "    构建运行时（下载 Python 3.13 + torch 2.8 cu128 + 依赖，约 4GB）"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\build-runtime.ps1" -OutDir "runtime"
  if ($LASTEXITCODE -ne 0) { throw "运行时构建失败" }
  $runtimePython = Join-Path $root "runtime\python.exe"
}

# ---------- 2. 预训练权重 ----------
$pretrain = Join-Path $dataRoot "pretrain"
New-Item -ItemType Directory -Force -Path $pretrain | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $pretrain "nsf_hifigan") | Out-Null

$assets = @(
  @{ name = "ContentVec"; url = "https://hf-mirror.com/lj1995/VoiceConversionWebUI/resolve/main/hubert_base.pt"; out = (Join-Path $pretrain "checkpoint_best_legacy_500.pt"); mb = 189 },
  @{ name = "RMVPE"; url = "https://hf-mirror.com/datasets/ylzz1997/rmvpe_pretrain_model/resolve/main/rmvpe.pt"; out = (Join-Path $pretrain "rmvpe.pt"); mb = 368 },
  @{ name = "NSF-HiFiGAN model"; url = "https://hf-mirror.com/choihkk/nsf-hifigan/resolve/main/model"; out = (Join-Path $pretrain "nsf_hifigan\model"); mb = 56 },
  @{ name = "NSF-HiFiGAN config"; url = "https://hf-mirror.com/choihkk/nsf-hifigan/resolve/main/config.json"; out = (Join-Path $pretrain "nsf_hifigan\config.json"); mb = 1 }
)

if ($SkipAssets) {
  Write-Host "    跳过权重（-SkipAssets）"
} else {
  foreach ($a in $assets) {
    if ((Test-Path $a.out) -and -not $Force) {
      Write-Host ("    {0} 已存在" -f $a.name)
      continue
    }
    Write-Host ("    下载 {0}（约 {1}MB）" -f $a.name, $a.mb)
    & curl.exe -L --fail --retry 3 --retry-delay 2 -C - -o $a.out $a.url
    if ($LASTEXITCODE -ne 0) { throw ("下载失败：{0}" -f $a.name) }
  }
}

# ---------- 3. 自检 ----------
Write-Step "环境自检"
if (Test-Path $runtimePython) {
  $env:SVCSTUDIO_RUNTIME_KIND = "gpu"
  # 直接落文件再解析：避免中文经管道被控制台编码破坏
  $doctorJson = Join-Path $env:TEMP "svcstudio-doctor.json"
  & $runtimePython -m svc_bridge doctor --json --data-root $dataRoot | Out-File -Encoding utf8 $doctorJson
  python -c "import json,io; d=json.load(io.open(r'$doctorJson', encoding='utf-8-sig')); print('  pass=%d warn=%d fail=%d trainable=%s' % (d['summary']['pass'], d['summary']['warn'], d['summary']['fail'], d['summary']['trainable'])); [print('  [%s] %s: %s' % (i['state'], i['id'], i['message'].encode('ascii','ignore').decode().strip())) for i in d['items'] if i['state'] != 'pass']"
} else {
  Write-Host "    跳过自检：没有内置运行时"
}

Write-Step "GPU 可用性"
if (Test-Path $runtimePython) {
  $gpuJson = & $runtimePython "scripts\check_gpu.py" 2>$null | Select-Object -Last 1
  try {
    $gpu = $gpuJson | ConvertFrom-Json
    if ($gpu.ok) {
      Write-Host ("    可用：{0}（{1}MB，sm {2}），512x512 矩阵乘 {3} ms" -f $gpu.device, $gpu.vram_mb, $gpu.sm, $gpu.matmul_ms) -ForegroundColor Green
      Write-Host "    训练：可用；推理：GPU 加速"
    } elseif ($gpu.cuda -eq $true) {
      Write-Host ("    检测到 CUDA 设备但无法执行 kernel：{0}" -f $gpu.error) -ForegroundColor Yellow
      Write-Host "    训练不可用；推理会回落 CPU"
    } else {
      Write-Host ("    未检测到可用的 CUDA：{0}" -f $gpu.reason) -ForegroundColor Yellow
      Write-Host "    训练不可用；推理走 CPU（较慢）"
    }
  } catch {
    Write-Host "    无法解析 GPU 检查结果" -ForegroundColor Yellow
  }
} else {
  Write-Host "    跳过：没有内置运行时"
}

Write-Host "`n准备完成。" -ForegroundColor Green
Write-Host "  启动开发：pnpm run dev         （浏览器预览，无后端）"
Write-Host "             pnpm run tauri dev   （桌面窗口）"
Write-Host "  质量门：  pnpm run check"
Write-Host "  打包：    pnpm run build:release"
