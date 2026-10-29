# Kiro-harness cleanup + project tidy-up. Takes a zip backup first, then deletes.
# Run: right-click > Run with PowerShell, or:
#   powershell -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\Projects\mcp-for-aztelekom\cleanup_kiro_harness.ps1"
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$H    = $env:USERPROFILE
$Proj = Join-Path $H 'Projects\mcp-for-aztelekom'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$Bk   = Join-Path $H "Desktop\kiro-cleanup-backup-$Stamp"
New-Item -ItemType Directory -Path $Bk -Force | Out-Null

Write-Host "1) Backup -> $Bk" -ForegroundColor Cyan
$toZip = @($Proj, "$H\.kiro-harness", "$H\.kiro-teams", "$H\.kiro-wiki", "$H\.kiro-backups")
foreach ($d in $toZip) {
  if (Test-Path $d) {
    $zip = Join-Path $Bk ((Split-Path $d -Leaf).TrimStart('.') + '.zip')
    try { [IO.Compression.ZipFile]::CreateFromDirectory($d, $zip); Write-Host "   ok  $zip" }
    catch { Write-Host "   BACKUP FAILED for $d : $_" -ForegroundColor Red; Write-Host 'Stopping, nothing deleted.'; pause; exit 1 }
  }
}
$baks = @("$H\.kiro\settings\mcp.json.bak", "$H\.kiro\settings\mcp.json.bak_20261027", "$H\.kiro\settings\permissions.yaml.bak_20261027")
foreach ($f in $baks) { if (Test-Path $f) { Copy-Item $f $Bk -Force } }

Write-Host "2) Remove wiki junctions first (so the delete never follows a link)" -ForegroundColor Cyan
Get-ChildItem "$H\.kiro-teams" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  $w = Join-Path $_.FullName 'wiki'
  if (Test-Path $w) { cmd /c rmdir "$w" | Out-Null; Write-Host "   junction removed: $w" }
}

Write-Host "3) Delete harness folders" -ForegroundColor Cyan
foreach ($d in @("$H\.kiro-harness", "$H\.kiro-teams", "$H\.kiro-wiki", "$H\.kiro-backups")) {
  if (Test-Path $d) {
    Get-ChildItem $d -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Attributes = 'Normal' }
    Remove-Item $d -Recurse -Force -ErrorAction Continue
    if (Test-Path $d) { Write-Host "   STILL EXISTS (file in use? close Kiro and rerun): $d" -ForegroundColor Yellow } else { Write-Host "   deleted $d" }
  }
}

Write-Host "4) Delete old .bak files in .kiro\settings" -ForegroundColor Cyan
foreach ($f in $baks) { if (Test-Path $f) { Remove-Item $f -Force; Write-Host "   deleted $f" } }

Write-Host "5) Delete unneeded project files" -ForegroundColor Cyan
foreach ($p in @("$Proj\shots_list.txt", "$Proj\.agents", "$Proj\dist\aztelekom-tfs-1.0.0.mcpb", "$Proj\src\__pycache__")) {
  if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Host "   deleted $p" }
}

Write-Host "6) User PATH: remove .kiro-harness entries" -ForegroundColor Cyan
$up = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($up) {
  $parts = $up -split ';' | Where-Object { $_ -ne '' }
  $keep  = $parts | Where-Object { $_ -notmatch '\\\.kiro-harness' }
  if ($keep.Count -ne $parts.Count) {
    [Environment]::SetEnvironmentVariable('Path', ($keep -join ';'), 'User')
    Write-Host "   removed: $(($parts | Where-Object { $_ -match '\\\.kiro-harness' }) -join ', ')"
  } else { Write-Host '   nothing to remove' }
}

Write-Host "7) Harness user env vars (KH_*)" -ForegroundColor Cyan
$envs = [Environment]::GetEnvironmentVariables('User')
$kh = @($envs.Keys | Where-Object { $_ -like 'KH_*' })
foreach ($k in $kh) { [Environment]::SetEnvironmentVariable($k, $null, 'User'); Write-Host "   removed $k" }
if (-not $kh) { Write-Host '   none' }
$other = @($envs.Keys | Where-Object { $_ -match 'MCP_API_KEY$' -or $_ -eq 'NO_PROXY' })
if ($other) { Write-Host "   NOT touched (check yourself if still needed): $($other -join ', ')" -ForegroundColor Yellow }

Write-Host ''
Write-Host "Done. Backup: $Bk" -ForegroundColor Green
Write-Host 'Untouched: .kiro (clean Kiro), .kiro-shared, artifacts\shots.'
Write-Host 'Open a new terminal so the PATH change takes effect.'
pause
