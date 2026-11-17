# 12 类错误的可复现用例（G2）。
# 能在本机自动触发的就跑；依赖真机/GPU/磁盘构造的明确标注 SKIP，不假装通过。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/error-cases.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = "worker"

$rows = New-Object System.Collections.ArrayList
function Add-Row($id, $name, $status, $evidence) {
  [void]$rows.Add([pscustomobject]@{ Id = $id; Name = $name; Status = $status; Evidence = "$evidence" })
}

$cargo = "C:\Users\yangjianlin\.cargo\bin\cargo.exe"

# 1 / 6 / 12：doctor 真实输出（缺权重、ffmpeg、版本错配）
$doctorRaw = python -m svc_bridge doctor --json --data-root "C:\Users\yangjianlin\SVCStudio" 2>&1 | Out-String
$doctor = $null
try { $doctor = $doctorRaw | ConvertFrom-Json } catch { $doctor = $null }
if ($doctor) {
  $items = @($doctor.items)
  $weightMissing = @($items | Where-Object { $_.id -in @("encoder", "f0", "vocoder") -and $_.state -ne "pass" }).Count
  Add-Row "G2-1" "缺权重" $(if ($weightMissing -gt 0) { "PASS" } else { "FAIL" }) "doctor 权重类不通过=$weightMissing，trainable=$($doctor.summary.trainable)"
  $ff = @($items | Where-Object { $_.id -eq "ffmpeg" })[0]
  Add-Row "G2-6" "ffmpeg 缺失" $(if ($ff.state -eq "pass") { "SKIP" } elseif ($ff.fix) { "PASS" } else { "FAIL" }) "ffmpeg 状态=$($ff.state)（本机已安装，缺失态无法构造）"
  $py = @($items | Where-Object { $_.id -eq "python" })[0]
  Add-Row "G2-12" "版本错配" $(if ($py.state -ne "pass" -and $py.fix) { "PASS" } else { "FAIL" }) "python 状态=$($py.state) cause='$($py.cause)'"
} else {
  Add-Row "G2-1" "缺权重" "SKIP" "doctor 未返回 JSON"
  Add-Row "G2-6" "ffmpeg 缺失" "SKIP" "doctor 未返回 JSON"
  Add-Row "G2-12" "版本错配" "SKIP" "doctor 未返回 JSON"
}

# 2：显存不足
& $cargo test --manifest-path src-tauri/Cargo.toml --quiet recommend 2>&1 | Out-Null
Add-Row "G2-2" "显存不足" $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) "cargo test recommend exit=$LASTEXITCODE（含 6GB 上限与不可训练档）"

# 3：配置非法
$p = python -m pytest worker/tests -q -k "config" 2>&1 | Out-String
Add-Row "G2-3" "配置非法" $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) (($p -split "`n" | Select-Object -Last 1))

# 4：路径含中文 / 越界
& $cargo test --manifest-path src-tauri/Cargo.toml --quiet safe_join 2>&1 | Out-Null
Add-Row "G2-4" "路径非法" $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) "cargo test safe_join exit=$LASTEXITCODE"

# 5 / 10：需要构造环境
Add-Row "G2-5" "磁盘不足" "SKIP" "需可控磁盘配额；doctor 只做余量告警"
Add-Row "G2-10" "权限不足" "SKIP" "需 ACL 构造；代码路径返回 SVC-7003 并保留原始错误"

# 7：进程崩溃
& $cargo test --manifest-path src-tauri/Cargo.toml --quiet crash 2>&1 | Out-Null
Add-Row "G2-7" "进程崩溃" $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) "cargo test crash exit=$LASTEXITCODE"

# 8：音频损坏
$p = python -m pytest worker/tests -q -k "corrupt or short or silent or broken" 2>&1 | Out-String
Add-Row "G2-8" "音频损坏" $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) (($p -split "`n" | Select-Object -Last 1))

# 9：模型不匹配
$p = python -m pytest worker/tests -q -k "compat" 2>&1 | Out-String
Add-Row "G2-9" "模型不匹配" $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) (($p -split "`n" | Select-Object -Last 1))

# 11：端口占用
& $cargo test --manifest-path src-tauri/Cargo.toml --quiet avoids_occupied_port 2>&1 | Out-Null
Add-Row "G2-11" "端口占用" $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) "cargo test avoids_occupied_port exit=$LASTEXITCODE"

$rows | Format-Table -AutoSize | Out-String -Width 220 | Write-Host
$pass = @($rows | Where-Object { $_.Status -eq "PASS" }).Count
$fail = @($rows | Where-Object { $_.Status -eq "FAIL" }).Count
$skip = @($rows | Where-Object { $_.Status -eq "SKIP" }).Count
Write-Host "PASS=$pass FAIL=$fail SKIP=$skip"

$dir = "docs/requests/intent_770b99fe990a4a0d/evidence/G2"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
# 显式 UTF-8 无 BOM：Set-Content -Encoding UTF8 会写 BOM；默认编码会把中文写成 GBK 字节。
$json = @($rows) | ConvertTo-Json -Depth 3
[System.IO.File]::WriteAllText((Join-Path $dir "error-cases.json"), $json, (New-Object System.Text.UTF8Encoding($false)))
if ($fail -gt 0) { exit 1 }
