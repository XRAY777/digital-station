$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
& (Join-Path $PSScriptRoot 'stop-demo.ps1')
$python = Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Offline Python environment missing.' }
if (Test-Path -LiteralPath '.env') {
  Get-Content -LiteralPath '.env' | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$' -and $Matches[2] -notmatch '^\s*#') {
      [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim().Trim('"').Trim("'"), 'Process')
    }
  }
}
if (!$env:DATABASE_URL) { $env:DATABASE_URL = 'sqlite:///./data/station.db' }
if (!$env:DATABASE_URL.StartsWith('sqlite:')) { throw 'Reset only works with the local SQLite demo database.' }
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& $python -m scripts.demo_data reset
if ($LASTEXITCODE -ne 0) { throw 'Demo reset failed.' }
Write-Host 'Demo station reset. Start it with start.bat.'
