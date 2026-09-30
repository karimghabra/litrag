<#
.SYNOPSIS
  Removes what install.ps1 put in place: app\, venv\, uv\ (with the Python and the cache uv
  kept there), the log, the uninstall scripts it left, and the Start Menu shortcut.

.DESCRIPTION
  Never the libraries, never Docling's models under them, never Ollama or its models.
  $env:LITRAG_INSTALL_ROOT names the install, as for install.ps1. It asks first unless -Yes.
#>
[CmdletBinding()]
param([switch]$Yes)

$ErrorActionPreference = 'Stop'
$root = if ($env:LITRAG_INSTALL_ROOT) { $env:LITRAG_INSTALL_ROOT } else { Join-Path $env:LOCALAPPDATA 'litrag' }
$libroot = if ($env:LITRAG_ROOT) { $env:LITRAG_ROOT } elseif ($env:PROTRACKER_LIBRARY) { $env:PROTRACKER_LIBRARY } else { Join-Path $HOME '.protracker\library' }
$lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'litrag.lnk'

# each thing only if it is what the installer made there, whatever the root was set to
$gone = @()
if (Test-Path -LiteralPath (Join-Path $root 'app\resources\app.asar')) { $gone += Join-Path $root 'app' }
if (Test-Path -LiteralPath (Join-Path $root 'app.new')) { $gone += Join-Path $root 'app.new' }
if (Test-Path -LiteralPath (Join-Path $root 'venv\pyvenv.cfg')) { $gone += Join-Path $root 'venv' }
if (Test-Path -LiteralPath (Join-Path $root 'uv\uv.exe')) { $gone += Join-Path $root 'uv' }
foreach ($f in 'install.log', 'uninstall.ps1', 'uninstall.cmd') {
  if (Test-Path -LiteralPath (Join-Path $root $f)) { $gone += Join-Path $root $f }
}
if (Test-Path -LiteralPath $lnk) { $gone += $lnk }
if ($gone.Count -eq 0) {
  Write-Host "Nothing of litrag's is installed at $root."
  exit 0
}

Write-Host 'This removes:'
$gone | ForEach-Object { Write-Host "  $_" }
Write-Host "and keeps the libraries at $libroot, and Ollama."
if (-not $Yes) {
  $answer = Read-Host 'Remove litrag? [y/N]'
  if ($answer -notmatch '^(y|yes)$') {
    Write-Host 'Nothing removed.'
    exit 0
  }
}

$running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $p = $null
    try { $p = $_.Path } catch { }
    $p -and ($p.StartsWith("$root\", [StringComparison]::OrdinalIgnoreCase))
  })
if ($running.Count -gt 0) {
  Write-Host "litrag is running ($($running[0].Path)): close it, then run uninstall.cmd again."
  exit 1
}

Set-Location -LiteralPath $env:TEMP # a folder a process stands in cannot be removed
try {
  foreach ($g in $gone) { Remove-Item -LiteralPath $g -Recurse -Force }
} catch {
  Write-Host "Could not remove everything: $($_.Exception.Message)"
  exit 1
}
# the folder itself when nothing else is in it (and no window stands in it)
try { if (@(Get-ChildItem -LiteralPath $root -Force).Count -eq 0) { Remove-Item -LiteralPath $root } } catch { }
Write-Host "litrag is removed. The libraries at $libroot are as they were."
