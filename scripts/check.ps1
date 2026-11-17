# 质量门：一次"可提交"必须全绿。任何一步非零退出即整体失败。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/check.ps1 [-SkipRust]

param(
  [switch]$SkipRust
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$failed = @()

# rustup 装的 cargo 不一定在 PATH 上（本机实测），显式解析。
$cargo = (Get-Command cargo -ErrorAction SilentlyContinue).Source
if (-not $cargo) { $cargo = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe" }
if (-not (Test-Path $cargo)) { Write-Host "cargo 未找到：$cargo" -ForegroundColor Red; exit 1 }

function Step($name, [scriptblock]$body) {
  Write-Host "==> $name" -ForegroundColor Cyan
  try {
    & $body
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { throw "exit $LASTEXITCODE" }
  } catch {
    Write-Host "FAILED: $name -> $_" -ForegroundColor Red
    $script:failed += $name
  }
}

Step "tsc --noEmit" { & pnpm.cmd typecheck }
Step "vitest" { & pnpm.cmd test -- --reporter=dot }
Step "pytest" { & python -m pytest worker/tests -q }
Step "state artifacts" { & python docs/requests/intent_770b99fe990a4a0d/tools/check_state.py }
Step "verification ledger" { & python docs/requests/intent_770b99fe990a4a0d/tools/check_verification.py }
Step "error cases" { & python docs/requests/intent_770b99fe990a4a0d/tools/check_error_cases.py }
Step "error codes" { & python docs/requests/intent_770b99fe990a4a0d/tools/check_error_codes.py }
Step "dll gate selftest" { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-verify-dlls.ps1 }
Step "bundle budget" { & node scripts/assert-budget.mjs }

if (-not $SkipRust) {
  Step "cargo fmt" { & $cargo fmt --manifest-path src-tauri/Cargo.toml --check }
  Step "cargo clippy" {
    & $cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
  }
  Step "cargo test" { & $cargo test --manifest-path src-tauri/Cargo.toml --quiet }
}

if ($failed.Count -gt 0) {
  Write-Host ("`nRESULT FAIL: " + ($failed -join ", ")) -ForegroundColor Red
  exit 1
}
Write-Host "`nRESULT PASS" -ForegroundColor Green
exit 0
