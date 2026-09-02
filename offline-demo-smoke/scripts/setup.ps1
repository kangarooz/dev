<#
.SYNOPSIS
  Offline Demo Smoke Kit - setup for Windows (PowerShell 5.1 or 7).

.DESCRIPTION
  Creates .venv, installs requirements.txt, optionally torch + requirements-tts.txt
  (voice cloning) followed by `python -m demo_smoke prefetch --tts <-Prefetch>` (default
  auto = what `run --tts auto` picks here), optionally `ollama pull NAME`, then runs
  `python -m demo_smoke doctor`. Idempotent (safe to re-run), no admin rights needed.

  If Windows refuses to run it:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

.EXAMPLE
  .\scripts\setup.ps1
  .\scripts\setup.ps1 -Tts -Torch auto -Prefetch auto -Model qwen3-coder:30b
#>
[CmdletBinding()]
param(
  [switch]$Tts,
  [ValidateSet('auto', 'cuda', 'rocm', 'cpu')][string]$Torch = 'auto',
  [string]$TorchIndex = '',
  [string]$TorchVersion = $(if ($env:DEMO_SMOKE_TORCH_VERSION) { $env:DEMO_SMOKE_TORCH_VERSION } else { '2.6.0' }),
  [ValidateSet('auto', 'turbo', 'nano', 'classic', 'none')][string]$Prefetch = 'auto',
  [string]$Model = '',
  [string]$Python = '',
  [string]$BaseUrl = $(if ($env:DEMO_SMOKE_BASE_URL) { $env:DEMO_SMOKE_BASE_URL } else { 'http://localhost:11434/v1' }),
  [switch]$NoDoctor
)

$ErrorActionPreference = 'Stop'
$KitDir = Split-Path -Parent $PSScriptRoot
Set-Location $KitDir

function Fail($msg) { Write-Host "setup.ps1: $msg" -ForegroundColor Red; exit 3 }

function Invoke-Py([string[]]$cmd, [string[]]$extra) {
  # $cmd is e.g. @('py','-3.11') or @('python'); PowerShell's 1..0 range is descending, so guard it.
  $lead = @()
  if ($cmd.Length -gt 1) { $lead = $cmd[1..($cmd.Length - 1)] }
  & $cmd[0] @lead @extra
}

function Test-PythonCmd([string[]]$cmd) {
  try {
    Invoke-Py $cmd @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)') 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

# ---------------------------------------------------------------- python
$PyCmd = $null
if ($Python) {
  if (-not (Test-PythonCmd @($Python))) { Fail "$Python is not a Python >= 3.10" }
  $PyCmd = @($Python)
} else {
  $candidates = @(
    @('py', '-3.11'), @('py', '-3.12'), @('py', '-3.10'), @('py', '-3.13'), @('py', '-3'),
    @('python3.11'), @('python'), @('python3')
  )
  foreach ($c in $candidates) {
    if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
      if (Test-PythonCmd $c) { $PyCmd = $c; break }
    }
  }
}
if (-not $PyCmd) { Fail 'no Python >= 3.10 found; install Python 3.11 from python.org (tick "Add to PATH") or pass -Python C:\path\python.exe' }
$pyVersion = Invoke-Py $PyCmd @('-c', 'import platform; print(platform.python_version())')
Write-Host "== python: $($PyCmd -join ' ') ($pyVersion)"
if ($Tts) {
  # torch 2.6.0 (pinned by chatterbox-tts) ships wheels for CPython 3.9-3.13 only.
  Invoke-Py $PyCmd @('-c', 'import sys; sys.exit(0 if sys.version_info < (3, 14) else 1)') 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "-Tts needs Python 3.10-3.13 (torch $TorchVersion has no wheels for $pyVersion); pass -Python C:\path\to\python3.11\python.exe" }
}

# ---------------------------------------------------------------- venv
$VPy = Join-Path $KitDir '.venv\Scripts\python.exe'
if (-not (Test-Path $VPy)) {
  Write-Host '== creating .venv'
  Invoke-Py $PyCmd @('-m', 'venv', (Join-Path $KitDir '.venv'))
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VPy)) { Fail 'could not create .venv' }
} else {
  Write-Host '== .venv exists, reusing'
}

Write-Host '== installing requirements.txt'
& $VPy -m pip install --disable-pip-version-check --quiet --upgrade pip
& $VPy -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail 'pip install -r requirements.txt failed' }

# ---------------------------------------------------------------- tts (optional)
if ($Tts) {
  if ($Torch -eq 'auto') {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $Torch = 'cuda' } else { $Torch = 'cpu' }
    Write-Host "== torch accelerator (auto): $Torch"
  }
  if ($Torch -eq 'rocm' -and -not $TorchIndex) {
    Write-Host 'setup.ps1: warning: PyTorch publishes no ROCm wheels for Windows; installing the CPU build (use -TorchIndex to override)' -ForegroundColor Yellow
    $Torch = 'cpu'
  }
  if (-not $TorchIndex) {
    switch ($Torch) {
      'cuda' { $TorchIndex = 'https://download.pytorch.org/whl/cu126' }
      'cpu'  { $TorchIndex = 'https://download.pytorch.org/whl/cpu' }
    }
  }
  Write-Host "== installing torch==$TorchVersion torchaudio==$TorchVersion from $TorchIndex"
  & $VPy -m pip install --disable-pip-version-check "torch==$TorchVersion" "torchaudio==$TorchVersion" --index-url $TorchIndex
  if ($LASTEXITCODE -ne 0) { Fail 'torch install failed (check the index URL / Python version; torch 2.6.0 supports Python 3.9-3.13)' }
  Write-Host '== installing requirements-tts.txt (chatterbox-tts)'
  & $VPy -m pip install --disable-pip-version-check -r requirements-tts.txt
  if ($LASTEXITCODE -ne 0) { Fail 'pip install -r requirements-tts.txt failed' }
  if ($Prefetch -ne 'none') {
    Write-Host "== prefetch --tts $Prefetch (Chatterbox weights into the Hugging Face cache)"
    & $VPy -m demo_smoke prefetch --tts $Prefetch
    if ($LASTEXITCODE -ne 0) { Fail "prefetch failed; re-run: python -m demo_smoke prefetch --tts $Prefetch (while online)" }
  } else {
    Write-Host '== remember: python -m demo_smoke prefetch --tts auto   while still online (caches what --tts auto uses here)'
  }
}

# ---------------------------------------------------------------- ollama model (optional)
if ($Model) {
  if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "== ollama pull $Model"
    & ollama pull $Model
    if ($LASTEXITCODE -ne 0) { Write-Host "setup.ps1: warning: ollama pull failed (is Ollama running?)" -ForegroundColor Yellow }
  } else {
    Write-Host "setup.ps1: warning: ollama not on PATH; install it from https://ollama.com and run: ollama pull $Model" -ForegroundColor Yellow
  }
}

# ---------------------------------------------------------------- doctor
$rc = 0
if (-not $NoDoctor) {
  Write-Host '== doctor'
  if ($Model) { & $VPy -m demo_smoke doctor --base-url $BaseUrl --model $Model } else { & $VPy -m demo_smoke doctor }
  $rc = $LASTEXITCODE
}

Write-Host @"
== done. Next (the example scenario needs your app at http://localhost:3000 with /login and /manuals,
   and its login reads DEMO_USER / DEMO_PASS; copy your reference clip into the kit, e.g. voice\ref.wav):
   .venv\Scripts\activate      (if scripts are disabled: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned, or use .venv\Scripts\python.exe)
   python -m demo_smoke voice-check --ref voice\ref.wav
   `$env:DEMO_USER='...'; `$env:DEMO_PASS='...'
   python -m demo_smoke run scenarios\example-chat-with-manuals.json --out demo-output\chat-with-manuals --narration template --ref voice\ref.wav
   The agent path needs OpenCode installed while online (https://opencode.ai; opencode --version), then, offline:
   `$env:OPENCODE_DISABLE_MODELS_FETCH=1; `$env:OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS=1200000
   opencode run --agent demo-smoke --auto --command smoke "scenarios/example-chat-with-manuals.json demo-output/chat-with-manuals"
   No app yet? Try the bundled mock app first: see README "Try it on the bundled mock app".
"@
exit $rc
