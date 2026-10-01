<#
.SYNOPSIS
  Installs or updates litrag for this Windows user: the app, uv, the parser's Python
  environment, and Ollama's embedder. No administrator rights needed.

.DESCRIPTION
  Run it from the folder the release zip was extracted to (install.cmd does that). Running it
  again is the update: app\ is replaced by the zip's, venv\ is brought in line with the new
  lock. Libraries ($env:LITRAG_ROOT, else ~\.protracker\library) are never created, moved or
  deleted; the one thing written beside them is <libraries>\models\docling, and only when
  asked.

    %LOCALAPPDATA%\litrag\     (or $env:LITRAG_INSTALL_ROOT)
      app\                     the unpacked app; app\resources\parser is the parser's source
      venv\                    the parser's environment (kept across updates)
      uv\                      uv.exe, the Python it manages, and its cache
      install.log              (the cache goes where UV_CACHE_DIR says, if it says)

.PARAMETER Torch
  auto (the default): the CUDA 13 build of torch when nvidia-smi runs, else the CPU one.
  cpu or cu130 forces one. none installs no torch extra.

.PARAMETER PrefetchModels
  Fetch Docling's layout and table models into <libraries>\models\docling now, rather than
  on the first paper. Once that folder is there the worker reads only it, so every later run
  refreshes it for the Docling installed.

.PARAMETER SkipOllama
  Leave Ollama alone: neither install it nor pull its embedder.

.EXAMPLE
  .\install.cmd -Torch cpu -SkipOllama
#>
[CmdletBinding()]
param(
  [ValidateSet('auto', 'cpu', 'cu130', 'none')]
  [string]$Torch = 'auto',
  [switch]$PrefetchModels,
  [switch]$SkipOllama
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue' # Windows PowerShell's progress bar slows downloads tenfold
$Python = '3.12' # every locked package has a wheel for it, on Windows and on Linux

$here = $PSScriptRoot
$root = if ($env:LITRAG_INSTALL_ROOT) { $env:LITRAG_INSTALL_ROOT } else { Join-Path $env:LOCALAPPDATA 'litrag' }
$log = Join-Path $root 'install.log'

function Write-Log([string]$Text) {
  Write-Host $Text
  if (Test-Path -LiteralPath $root) { Add-Content -LiteralPath $log -Value $Text -Encoding UTF8 }
}

function Step([string]$Text) {
  Write-Log ''
  Write-Log "== $Text"
}

function Fail([string]$Text) {
  Write-Log ''
  Write-Log "install.ps1: $Text"
  Write-Log "Nothing after this step was done. The log: $log"
  exit 1
}

# A program, its output shown and logged line by line; returns its exit code. Windows
# PowerShell turns every stderr line of a native program into an error record, which under
# ErrorActionPreference Stop would end the script at uv's first progress line, so the
# preference is relaxed around the call and the exit code is what decides.
function Invoke-Logged([string]$Exe, [string[]]$Arguments) {
  if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) {
    Write-Log "not found: $Exe"
    return 127
  }
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $Exe @Arguments 2>&1 | ForEach-Object { Write-Log "$_" }
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $saved
  }
}

if (-not (Test-Path -LiteralPath (Join-Path $here 'app\litrag.exe')) -or -not (Test-Path -LiteralPath (Join-Path $here 'app\resources\parser\uv.lock'))) {
  Write-Host 'There is no app\ beside install.ps1. Extract the whole zip (right-click it, Extract All),'
  Write-Host 'then run install.cmd from the folder that made.'
  exit 1
}
if (-not [Environment]::Is64BitOperatingSystem) {
  Write-Host 'This build of litrag is for 64-bit Windows.'
  exit 1
}

try {
  New-Item -ItemType Directory -Force -Path $root | Out-Null
  $root = (Resolve-Path -LiteralPath $root).Path
  $log = Join-Path $root 'install.log'
  Write-Log ''
  Write-Log "litrag install, $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), from $here into $root"

  # ---- 1. the app --------------------------------------------------------------------------
  Step 'The app'
  $app = Join-Path $root 'app'
  $source = (Resolve-Path -LiteralPath (Join-Path $here 'app')).Path
  if ($source -eq $app) {
    Write-Log 'running from the install itself; app\ stays as it is'
  } else {
    # the window, or its parser (whose python.exe runs from uv\python), holds files open
    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $p = $null
        try { $p = $_.Path } catch { }
        $p -and ($p.StartsWith("$root\", [StringComparison]::OrdinalIgnoreCase))
      })
    if ($running.Count -gt 0) { Fail "litrag is running ($($running[0].Path)): close it, then run install.cmd again" }
    $staging = Join-Path $root 'app.new'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    Copy-Item -LiteralPath $source -Destination $staging -Recurse
    # the zip's download mark would have SmartScreen ask about litrag.exe on every first start
    Get-ChildItem -LiteralPath $staging -Recurse -File | Unblock-File
    if (Test-Path -LiteralPath $app) { Remove-Item -LiteralPath $app -Recurse -Force }
    Rename-Item -LiteralPath $staging -NewName 'app'
    # the uninstaller stays with the install, so the download can go
    foreach ($f in 'uninstall.ps1', 'uninstall.cmd') { Copy-Item -LiteralPath (Join-Path $here $f) -Destination $root -Force }
  }
  $pyproject = Get-Content -LiteralPath (Join-Path $app 'resources\parser\pyproject.toml')
  $version = ($pyproject | Where-Object { $_ -match '^version = "(.*)"' } | Select-Object -First 1) -replace '^version = "(.*)"', '$1'
  Write-Log "litrag $version in $app"

  # ---- 2. uv -------------------------------------------------------------------------------
  # astral's installer, into uv\ and nowhere else: no PATH edits, and no update receipt, which
  # would take the place of the receipt of a uv this person installed for themselves. It runs
  # on every install, so an update brings a uv that can read the new lock; if it cannot be
  # reached, the uv already here is used. A child PowerShell, so its exit cannot end this one.
  Step 'uv'
  $uvDir = Join-Path $root 'uv'
  $uv = Join-Path $uvDir 'uv.exe'
  $env:UV_INSTALL_DIR = $uvDir
  $env:UV_NO_MODIFY_PATH = '1'
  $env:UV_DISABLE_UPDATE = '1'
  $code = Invoke-Logged 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; irm https://astral.sh/uv/install.ps1 | iex')
  Remove-Item -Path 'Env:UV_INSTALL_DIR', 'Env:UV_NO_MODIFY_PATH', 'Env:UV_DISABLE_UPDATE'
  if ($code -ne 0) {
    if (Test-Path -LiteralPath $uv) { Write-Log "(could not reach astral.sh; keeping the uv already in $uvDir)" }
    else { Fail 'could not install uv from https://astral.sh/uv/install.ps1 (is this machine online?)' }
  }
  if (-not (Test-Path -LiteralPath $uv)) { Fail "uv is not at $uv after its installer ran" }
  Invoke-Logged $uv @('--version') | Out-Null

  # ---- 3. torch ----------------------------------------------------------------------------
  Step 'torch'
  $gpu = ''
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $gpu = (& nvidia-smi '--query-gpu=name,driver_version' '--format=csv,noheader' 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0) { $gpu = '' }
    $ErrorActionPreference = $saved
  }
  if ($Torch -eq 'auto') {
    $Torch = if ($gpu) { 'cu130' } else { 'cpu' }
    if ($gpu) { Write-Log "auto: nvidia-smi sees $gpu, so cu130" } else { Write-Log 'auto: no nvidia-smi, so cpu' }
  } else {
    Write-Log "$Torch, as asked$(if ($gpu) { " (nvidia-smi sees $gpu)" })"
  }
  if ($Torch -eq 'cu130' -and $gpu -match ',\s*(\d+)\.') {
    if ([int]$Matches[1] -lt 580) {
      Write-Log "warning: CUDA 13 needs an NVIDIA driver of R580 or newer and this one is $($gpu -replace '.*,\s*', '');"
      Write-Log '         torch will not see the GPU until the driver is updated (or use -Torch cpu)'
    }
  }

  # ---- 4. the parser's environment ---------------------------------------------------------
  Step "The parser's Python environment (the first time: a few GB, several minutes)"
  $venv = Join-Path $root 'venv'
  $env:UV_PROJECT_ENVIRONMENT = $venv
  $env:UV_PYTHON_PREFERENCE = 'only-managed'
  $env:UV_PYTHON_INSTALL_DIR = Join-Path $uvDir 'python'
  if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $uvDir 'cache' }
  $sync = @('sync', '--frozen', '--no-dev', '--python', $Python, '--project', (Join-Path $app 'resources\parser'))
  if ($Torch -ne 'none') { $sync += @('--extra', $Torch) }
  $code = Invoke-Logged $uv $sync
  if ($code -ne 0) { Fail "uv sync failed (above). Fix what it names and run install.cmd again; the app is in place already" }
  $py = Join-Path $venv 'Scripts\python.exe'
  if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\litrag-parser.exe'))) { Fail 'the environment has no litrag-parser.exe' }
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  # no double quotes in the Python: Windows PowerShell and PowerShell 7 pass them differently
  $torchSays = (& $py -c 'import torch, docling, litrag_parser.worker; print(torch.__version__, ''cuda'' if torch.cuda.is_available() else ''no cuda'')' 2>&1 | Select-Object -Last 1)
  $imported = $LASTEXITCODE -eq 0
  $pyVersion = (& $py -c 'import platform; print(platform.python_version())' 2>$null)
  $ErrorActionPreference = $saved
  if (-not $imported) { Fail "the environment does not import: $torchSays" }
  Write-Log "torch $torchSays"

  # ---- 5. Docling's models -----------------------------------------------------------------
  Step "Docling's models"
  $libroot = if ($env:LITRAG_ROOT) { $env:LITRAG_ROOT } elseif ($env:PROTRACKER_LIBRARY) { $env:PROTRACKER_LIBRARY } else { Join-Path $HOME '.protracker\library' }
  $models = Join-Path $libroot 'models\docling'
  $prefetched = ''
  if ($PrefetchModels -or ((Test-Path -LiteralPath $models) -and @(Get-ChildItem -LiteralPath $models -Force).Count -gt 0)) {
    # the layout model and TableFormer are all the worker's pipeline uses (no OCR, no enrichment);
    # docling-tools lays them out as the worker's artifacts_path reads them (~0.7 GB)
    Write-Log "fetching the layout and table models into $models"
    $code = Invoke-Logged (Join-Path $venv 'Scripts\docling-tools.exe') @('models', 'download', 'layout', 'tableformer', '--quiet', '-o', $models)
    if ($code -ne 0) { Fail "could not fetch Docling's models into $models" }
    $prefetched = $models
  } else {
    Write-Log 'not prefetched: the first paper fetches them (~0.5 GB, once). -PrefetchModels does it now.'
  }

  # ---- 6. Ollama ---------------------------------------------------------------------------
  Step 'Ollama'
  $ollamaSays = 'skipped'
  if ($SkipOllama) {
    Write-Log 'skipped (-SkipOllama)'
  } else {
    $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    $ollamaHome = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
    if (-not $ollama -and (Test-Path -LiteralPath (Join-Path $ollamaHome 'ollama.exe'))) { $ollama = Join-Path $ollamaHome 'ollama.exe' }
    if (-not $ollama) {
      if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Log 'Ollama is not installed; installing it with winget'
        $code = Invoke-Logged 'winget' @('install', '--id', 'Ollama.Ollama', '-e', '--accept-source-agreements', '--accept-package-agreements')
        # this window's PATH is from before the install
        if (Test-Path -LiteralPath (Join-Path $ollamaHome 'ollama.exe')) { $ollama = Join-Path $ollamaHome 'ollama.exe' }
        elseif ($code -ne 0) { Write-Log "winget could not install Ollama (exit $code)" }
      } else {
        Write-Log 'Ollama is not installed, and there is no winget to install it with.'
      }
    }
    if (-not $ollama) {
      $ollamaSays = 'not installed'
      Write-Log 'Papers are read without it; Query, and naming headings by meaning, need it:'
      Write-Log '  https://ollama.com/download/windows   then   ollama pull nomic-embed-text'
    } else {
      # the Ollama app starts its server; give it a moment after a fresh install
      $up = $false
      for ($i = 0; $i -lt 30 -and -not $up; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri 'http://127.0.0.1:11434/api/version' | Out-Null; $up = $true }
        catch {
          if ($i -eq 0 -and (Test-Path -LiteralPath (Join-Path $ollamaHome 'ollama app.exe'))) { Start-Process -FilePath (Join-Path $ollamaHome 'ollama app.exe') }
          Start-Sleep -Seconds 2
        }
      }
      if (-not $up) {
        $ollamaSays = 'installed, but not running'
        Write-Log 'Ollama is installed but its server is not answering. Start Ollama, then: ollama pull nomic-embed-text'
      } elseif ((Invoke-Logged $ollama @('pull', 'nomic-embed-text')) -eq 0) {
        $ollamaSays = 'ready, with nomic-embed-text'
      } else {
        $ollamaSays = 'installed, but nomic-embed-text was not pulled'
        Write-Log 'could not pull nomic-embed-text; later: ollama pull nomic-embed-text'
      }
    }
  }

  # ---- 7. the Start Menu shortcut ----------------------------------------------------------
  Step 'The Start Menu shortcut'
  $lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'litrag.lnk'
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($lnk)
  $shortcut.TargetPath = Join-Path $app 'litrag.exe'
  $shortcut.WorkingDirectory = $app
  $shortcut.Description = 'One library of papers per project, read into trees you can see'
  $shortcut.Save()
  Write-Log $lnk

  # ---- 8. what was done --------------------------------------------------------------------
  Step 'Done'
  Write-Log "  litrag $version   $app"
  Write-Log "  parser         $venv ($Torch; torch $torchSays, Python $pyVersion)"
  Write-Log "  Ollama         $ollamaSays"
  Write-Log "  models         $(if ($prefetched) { $prefetched } else { 'fetched on the first paper' })"
  Write-Log "  libraries      $libroot (untouched)"
  Write-Log "  log            $log"
  Write-Log ''
  Write-Log 'Start it from the Start Menu (litrag). Run install.cmd again to update;'
  Write-Log "$root\uninstall.cmd removes it (never the libraries)."
  if ($root -ne (Join-Path $env:LOCALAPPDATA 'litrag')) {
    Write-Log "This is not the default place, so set LITRAG_VENV=$venv for the app to find its parser."
  }
} catch {
  Fail "$($_.Exception.Message) (at line $($_.InvocationInfo.ScriptLineNumber))"
}
