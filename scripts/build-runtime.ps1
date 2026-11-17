# 构建 GPU 运行时（Python 3.13 + torch 2.8 cu128 + fairseq-fixed）
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$root = "D:\study\engineer_knowledge_system\project\real-project\voice-generator"
Set-Location $root

$out = "runtime"
$pyVer = "3.13.7"
$embedUrl = "https://mirrors.huaweicloud.com/python/$pyVer/python-$pyVer-embed-amd64.zip"
$pipIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
$wheelDir = Join-Path $env:TEMP "svcstudio-wheels"
New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null
$torchWheelUrl = "https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/torch-2.8.0%2Bcu128-cp313-cp313-win_amd64.whl"
$audioWheelUrl = "https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/torchaudio-2.8.0%2Bcu128-cp313-cp313-win_amd64.whl"

if ($DryRun) {
  Write-Host "计划：下载 Python 3.13.7 embeddable + torch 2.8.0+cu128（3.3GB）+ 依赖 + fairseq-fixed" -ForegroundColor Cyan
  Write-Host "     输出目录：$out；预计 7.6GB；耗时 5–10 分钟"
  Write-Host "DryRun 完成：未下载、未写入。" -ForegroundColor Green
  exit 0
}

Write-Host "==> 准备目录"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force -Path $out | Out-Null

Write-Host "==> 下载 Python $pyVer embeddable"
$zip = Join-Path $out "python-embed.zip"
& curl.exe -L --fail --silent --show-error -o $zip $embedUrl
if ($LASTEXITCODE -ne 0) { throw "下载 Python 失败" }
Expand-Archive -Path $zip -DestinationPath $out -Force
Remove-Item $zip

$pth = Get-ChildItem $out -Filter "python*._pth" | Select-Object -First 1
if ($pth) {
  $content = Get-Content $pth.FullName
  $content = $content -replace "^#\s*import site", "import site"
  if ($content -notcontains "Lib\site-packages") { $content += "Lib\site-packages" }
  Set-Content -Path $pth.FullName -Value $content -Encoding ASCII
}

$python = Join-Path $out "python.exe"
Write-Host "==> 安装 pip"
$getPip = Join-Path $out "get-pip.py"
& curl.exe -L --fail --silent --show-error -o $getPip "https://bootstrap.pypa.io/get-pip.py"
& $python $getPip --no-warn-script-location -i $pipIndex
if ($LASTEXITCODE -ne 0) { throw "pip 安装失败" }

Write-Host "==> 安装训练/推理依赖（先装，避免它们把 torch 降级）"
& $python -m pip install --no-warn-script-location -i $pipIndex --trusted-host pypi.tuna.tsinghua.edu.cn `
  "numpy" "scipy" "librosa" "scikit-learn" "soundfile" "tensorboard" "faiss-cpu" `
  "fairseq-fixed==0.12.3.1" "omegaconf" "hydra-core" "tensorboardX" "demucs==4.1.0"
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

Write-Host "==> 准备 cu128 轮子（缓存缺失时从镜像下载）"
$torchWheel = Join-Path $wheelDir "torch-2.8.0+cu128-cp313-cp313-win_amd64.whl"
$audioWheel = Join-Path $wheelDir "torchaudio-2.8.0+cu128-cp313-cp313-win_amd64.whl"
if (-not (Test-Path $torchWheel)) {
  Write-Host "    下载 torch（约 3.3GB，断点续传）"
  & curl.exe -L --fail --retry 3 --retry-delay 2 -C - -o $torchWheel $torchWheelUrl
  if ($LASTEXITCODE -ne 0) { throw "torch 轮子下载失败" }
}
if (-not (Test-Path $audioWheel)) {
  & curl.exe -L --fail --retry 3 --retry-delay 2 -C - -o $audioWheel $audioWheelUrl
  if ($LASTEXITCODE -ne 0) { throw "torchaudio 轮子下载失败" }
}

Write-Host "==> 强制安装 cu128 版 torch/torchaudio（覆盖依赖带来的 CPU 版）"
& $python -m pip install --no-warn-script-location --no-deps --force-reinstall --find-links $wheelDir "torch==2.8.0+cu128" "torchaudio==2.8.0+cu128"
if ($LASTEXITCODE -ne 0) { throw "cu128 torch 安装失败" }

Write-Host "==> 补丁：fairseq 的 torch.load 需要 weights_only=False"
$ckpt = Join-Path $out "Lib\site-packages\fairseq\checkpoint_utils.py"
if (Test-Path $ckpt) {
  $text = Get-Content $ckpt -Raw -Encoding UTF8
  $patched = $text -replace 'torch\.load\(f, map_location=torch\.device\("cpu"\)\)', 'torch.load(f, map_location=torch.device("cpu"), weights_only=False)'
  if ($patched -ne $text) {
    [System.IO.File]::WriteAllText($ckpt, $patched, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "    checkpoint_utils.py 已打补丁"
  } else {
    Write-Host "    WARN: 未匹配到 torch.load 调用，需人工确认"
  }
} else {
  Write-Host "    WARN: 未找到 fairseq/checkpoint_utils.py"
}

# 让 svc_bridge 可导入
$pthFile = Join-Path $out "Lib\site-packages\zz_svcstudio_gpu.pth"
Set-Content -Path $pthFile -Value "..\..\worker" -Encoding ASCII

Write-Host "==> 写入 sitecustomize（修复 embeddable 隔离模式）"
$sitecustomize = @"
import os
import sys

_up = os.environ.get("SVCSTUDIO_UPSTREAM_DIR")
if _up and os.path.isdir(_up) and _up not in sys.path:
    sys.path.insert(0, _up)
"@
[System.IO.File]::WriteAllText((Join-Path $out "Lib\site-packages\sitecustomize.py"), $sitecustomize, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "==> 预置 Demucs 人声分离权重（80MB，离线可用）"
$demucsDir = Join-Path $out "models\torch\hub\checkpoints"
New-Item -ItemType Directory -Force -Path $demucsDir | Out-Null
$demucsWeight = Join-Path $demucsDir "955717e8-8726e21a.th"
if (-not (Test-Path $demucsWeight)) {
  & curl.exe -L --fail --retry 3 --retry-delay 2 -C - -o $demucsWeight "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th"
  if ($LASTEXITCODE -ne 0) { throw "Demucs 权重下载失败" }
}

Write-Host "==> 复制 worker"
$dstWorker = Join-Path $out "worker"
New-Item -ItemType Directory -Force -Path $dstWorker | Out-Null
Copy-Item -Recurse -Force "worker\*" $dstWorker

Write-Host "==> 自检"
& $python -c "import torch, sys; print('python', sys.version.split()[0], 'torch', torch.__version__, 'cuda', torch.cuda.is_available()); from fairseq import checkpoint_utils; print('fairseq ok')"
if ($LASTEXITCODE -ne 0) { throw "GPU 运行时自检失败" }

$sizeGb = [math]::Round((Get-ChildItem -Recurse -File $out | Measure-Object Length -Sum).Sum / 1GB, 2)
Write-Host "==> 完成：$out（$sizeGb GB）"
