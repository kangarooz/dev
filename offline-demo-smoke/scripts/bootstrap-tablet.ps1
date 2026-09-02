<#
.SYNOPSIS
  One-shot bootstrap for a Windows machine ("the tablet") - run in an elevated
  (Administrator) PowerShell. Installs prerequisites, clones the kit branch, runs
  the kit setup (venv, deps, torch, Chatterbox weights), proves the pipeline on the
  bundled mock app, lists LM Studio models, and prints the next steps.

.DESCRIPTION
  Idempotent: safe to re-run. Uses winget for Git, Python 3.11, Google Chrome and
  Node LTS; npm for OpenCode and Claude Code. Nothing here needs Tailscale or a
  second machine. LM Studio itself is not installed by this script (install it
  from lmstudio.ai if it is not already on the machine).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\bootstrap-tablet.ps1
  powershell -ExecutionPolicy Bypass -File .\bootstrap-tablet.ps1 -SkipTts -SkipMockRun
#>
[CmdletBinding()]
param(
  [string]$Branch = 'claude/voice-cloning-smoke-test-prompt-p5kiey',
  [string]$Repo = 'https://github.com/kangarooz/dev.git',
  [string]$Root = "$env:USERPROFILE\src\kangarooz-dev",
  [string]$LmStudioUrl = 'http://127.0.0.1:1234/v1',
  [switch]$SkipTts,        # skip torch + chatterbox install and weight prefetch (fast path, --tts tone only)
  [switch]$SkipMockRun,    # skip the proof run against the bundled mock app
  [switch]$SkipClaude      # do not install Claude Code (only needed for Remote Control)
)

# 'Continue', not 'Stop': in Windows PowerShell 5.1 native commands (git, py, npm, winget)
# that write to stderr would otherwise abort the script. Exit codes are checked explicitly.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

function Say($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!! $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "XX $m" -ForegroundColor Red; exit 1 }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path', 'User')
}

# ---------------------------------------------------------------- preflight
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Fail 'Run this in an elevated PowerShell (right-click > Run as administrator).' }
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
  Fail 'winget is missing. Install "App Installer" from the Microsoft Store, then re-run.'
}
# Best effort only: this script already runs under -ExecutionPolicy Bypass. A machine-wide
# policy can override the CurrentUser scope, which throws; that is harmless here.
try { Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force -ErrorAction Stop }
catch { Warn 'could not set CurrentUser execution policy (overridden by a wider scope); continuing under Bypass' }
# Smart App Control (Windows 11) blocks unsigned/unknown binaries with no allowlist, so the
# unsigned Python extension modules the kit installs (torch, pandas, librosa .pyd files) fail to
# import. Read-only check of the same registry value `python -m demo_smoke doctor` reports
# (1 = on, 2 = evaluation, 0 = off, missing = not applicable): warn and continue, never change it.
$sacState = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' `
              -Name VerifiedAndReputablePolicyState -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
if ($sacState -eq 1) {
  Warn 'Smart App Control is ON and will block unsigned Python extension modules (torch, pandas, librosa). Turn it off: Windows Security > App & browser control > Smart App Control settings > Off (one-way), or run the kit in WSL2.'
  Warn 'Continuing anyway: the venv and deps install, but --tts turbo/nano will fail to import torch until it is off.'
}
Say "machine: $env:COMPUTERNAME  user: $env:USERNAME  PowerShell $($PSVersionTable.PSVersion)"

# ---------------------------------------------------------------- winget installs
function Ensure-App([string]$Id, [scriptblock]$Present) {
  if (& $Present) { Write-Host "   $Id already present"; return }
  Write-Host "   installing $Id ..."
  winget install --id $Id -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
  Refresh-Path
  if (-not (& $Present)) { Warn "$Id installed but not detected on PATH yet; open a new terminal and re-run if later steps fail." }
}

Say 'prerequisites (winget)'
Ensure-App 'Git.Git'            { [bool](Get-Command git -ErrorAction SilentlyContinue) }
function Test-Py311 {
  if (-not (Get-Command py -ErrorAction SilentlyContinue)) { return $false }
  try { $out = & cmd /c 'py -3.11 -c "print(1)" 2>nul'; return ("$out".Trim() -eq '1') } catch { return $false }
}
Ensure-App 'Python.Python.3.11' { Test-Py311 }
Ensure-App 'Google.Chrome'      { (Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe") -or
                                  (Test-Path "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe") -or
                                  (Test-Path "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") }
Ensure-App 'OpenJS.NodeJS.LTS'  { [bool](Get-Command node -ErrorAction SilentlyContinue) }
Refresh-Path

# ---------------------------------------------------------------- npm installs
Say 'OpenCode (npm)'
if (Get-Command opencode -ErrorAction SilentlyContinue) { Write-Host "   opencode $(opencode --version) already present" }
else {
  npm install -g opencode-ai | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "npm install -g opencode-ai failed (exit $LASTEXITCODE)" }
  Refresh-Path; Write-Host "   opencode $(opencode --version)"
}

if (-not $SkipClaude) {
  Say 'Claude Code (npm) - for Remote Control from the phone/web'
  if (Get-Command claude -ErrorAction SilentlyContinue) { Write-Host '   claude already present' }
  else { npm install -g @anthropic-ai/claude-code | Out-Null; Refresh-Path; Write-Host '   claude installed' }
}

# ---------------------------------------------------------------- repo
Say "repo -> $Root  (branch $Branch)"
if (Test-Path (Join-Path $Root '.git')) {
  git -C $Root fetch origin $Branch
  git -C $Root checkout $Branch
  git -C $Root pull --ff-only origin $Branch
  if ($LASTEXITCODE -ne 0) { Fail "git update failed in $Root (exit $LASTEXITCODE)" }
} else {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Root) | Out-Null
  git clone --branch $Branch $Repo $Root
  if ($LASTEXITCODE -ne 0) { Fail "git clone failed (exit $LASTEXITCODE)" }
}
$Kit = Join-Path $Root 'offline-demo-smoke'
if (-not (Test-Path (Join-Path $Kit 'scripts\setup.ps1'))) { Fail "kit not found at $Kit" }
Set-Location $Kit

# ---------------------------------------------------------------- kit setup
$setupNote = if ($SkipTts) { 'kit setup (venv, deps)' } else { 'kit setup (venv, deps, torch, chatterbox, weight prefetch - this is the slow part)' }
Say $setupNote
$setupArgs = @('-ExecutionPolicy', 'Bypass', '-File', 'scripts\setup.ps1', '-BaseUrl', $LmStudioUrl, '-NoDoctor')
if (-not $SkipTts) { $setupArgs += @('-Tts', '-Torch', 'auto', '-Prefetch', 'auto') }
& powershell @setupArgs
if ($LASTEXITCODE -ne 0) { Fail "setup.ps1 failed (exit $LASTEXITCODE). Fix the message above and re-run this script." }
$VPy = Join-Path $Kit '.venv\Scripts\python.exe'

# ---------------------------------------------------------------- doctor
Say 'doctor'
& $VPy -m demo_smoke doctor --out demo-output\doctor
if ($LASTEXITCODE -ne 0) { Warn 'doctor reported problems (see above). The mock run below may still work with --tts tone.' }

# ---------------------------------------------------------------- LM Studio
Say "LM Studio models at $LmStudioUrl"
try {
  $ids = (Invoke-RestMethod "$LmStudioUrl/models" -TimeoutSec 5).data.id
  if ($ids) { $ids | ForEach-Object { Write-Host "   $_" } } else { Warn 'server is up but no model is loaded' }
  Write-Host '   put the id you want into offline-demo-smoke\opencode.json -> provider.lmstudio.models (replace "local"),'
  Write-Host '   or pass it per run:  opencode run --model lmstudio/<id> ...'
} catch {
  Warn 'LM Studio server not reachable. In LM Studio: Developer tab > Start Server (port 1234), load a model, context >= 32k. Then re-run: (Invoke-RestMethod http://127.0.0.1:1234/v1/models).data.id'
}

# ---------------------------------------------------------------- proof run on the mock app
if (-not $SkipMockRun) {
  Say 'proof run: bundled mock app, headless Chrome, synthetic voice (no LLM, no Chatterbox needed)'
  $appDir = Join-Path $Kit 'tests\fixtures\app'
  $srv = Start-Process -FilePath $VPy -ArgumentList @('-m', 'http.server', '8765', '--bind', '127.0.0.1') `
           -WorkingDirectory $appDir -PassThru -WindowStyle Hidden
  try {
    Start-Sleep -Seconds 2
    & $VPy -m demo_smoke run tests\fixtures\scenarios\fixture-pass.json --out demo-output\fixture --tts tone --headless
    $rc = $LASTEXITCODE
  } finally {
    if ($srv -and -not $srv.HasExited) { Stop-Process -Id $srv.Id -Force }
  }
  if ($rc -eq 0) { Write-Host '   PASS -> demo-output\fixture\final\fixture-pass.mp4  (open it, you should hear the beep narration)' -ForegroundColor Green }
  else { Warn "mock run exited $rc - read demo-output\fixture\report.md" }
}

# ---------------------------------------------------------------- next steps
Say 'next steps'
@"
 1. Voice clip:   put a 30-90 s clean recording of yourself at voice\ref.wav
                  (or, in the OpenCode TUI, /clone-voice once that command lands on this branch).
 2. LM Studio:    Developer tab > Start Server, load your model, context length >= 32k.
                  Check tool calling:  $VPy -m demo_smoke check-model --base-url $LmStudioUrl --model <id>
 3. OpenCode TUI: cd $Kit ; `$env:OPENCODE_DISABLE_MODELS_FETCH=1 ; opencode
                  /models -> pick a hosted model first to validate, then lmstudio/<id>.
                  /smoke scenarios\example-chat-with-manuals.json demo-output\chat-with-manuals
 4. Real app:     export creds before starting opencode:  `$env:DEMO_USER='...'; `$env:DEMO_PASS='...'
                  and edit scenarios\example-chat-with-manuals.json (app_url, steps) - see README "Writing a scenario".
 5. Remote Control (lets the cloud session drive this machine):  cd $Kit ; claude   then type /remote-control
"@ | Write-Host
