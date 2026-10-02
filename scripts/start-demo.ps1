$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
$node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
$vite = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logs, (Join-Path $root 'data') | Out-Null
if (!(Test-Path -LiteralPath $python)) { throw 'Offline Python environment missing. Prepare it before disconnecting from the internet.' }
if (!$node -or !(Test-Path -LiteralPath $vite)) { throw 'Local Node/Vite dependencies missing. Prepare them before disconnecting from the internet.' }

if (Test-Path -LiteralPath '.env') {
  Get-Content -LiteralPath '.env' | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$' -and $Matches[2] -notmatch '^\s*#') {
      [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim().Trim('"').Trim("'"), 'Process')
    }
  }
}
if (!$env:DATABASE_URL) { $env:DATABASE_URL = 'sqlite:///./data/station.db' }
if (!$env:STATION_HOST) { $env:STATION_HOST = '127.0.0.1' }
if (!$env:STATION_BACKEND_URL) { $env:STATION_BACKEND_URL = 'http://127.0.0.1:8000' }

& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& $python -m scripts.demo_data seed
if ($LASTEXITCODE -ne 0) { throw 'Demo database seed failed.' }
& $python -m scripts.demo_accounts
if ($LASTEXITCODE -ne 0) { throw 'Demo account setup failed.' }

$backendPidFile = Join-Path $logs 'backend.pid'
$frontendPidFile = Join-Path $logs 'frontend.pid'
if (Test-Path $backendPidFile) { & (Join-Path $PSScriptRoot 'stop-demo.ps1') }
$backendOut = Join-Path $logs 'backend.out.log'
$backendErr = Join-Path $logs 'backend.err.log'
$api = Start-Process -FilePath $python -ArgumentList @('-m','backend.app.run') -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
Set-Content -LiteralPath $backendPidFile -Value $api.Id

$frontendHost = if ($env:STATION_LAN -eq '1') { '0.0.0.0' } else { '127.0.0.1' }
$frontendOut = Join-Path $logs 'frontend.out.log'
$frontendErr = Join-Path $logs 'frontend.err.log'
$web = Start-Process -FilePath $node -ArgumentList @(('"' + $vite + '"'),'--host',$frontendHost,'--port','5173','--strictPort') -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
Set-Content -LiteralPath $frontendPidFile -Value $web.Id

$healthy = $false
for ($i = 0; $i -lt 40; $i++) {
  try { $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8000/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { $healthy = $true; break } } catch { Start-Sleep -Milliseconds 500 }
}
if (!$healthy) { & (Join-Path $PSScriptRoot 'stop-demo.ps1'); throw "Backend health check failed. See $backendErr" }
$webReady = $false
for ($i = 0; $i -lt 40; $i++) {
  try { $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:5173/' -TimeoutSec 2; if ($r.StatusCode -eq 200) { $webReady = $true; break } } catch { Start-Sleep -Milliseconds 500 }
}
if (!$webReady) { & (Join-Path $PSScriptRoot 'stop-demo.ps1'); throw "Frontend did not start. See $frontendErr" }
if ($env:STATION_NO_BROWSER -ne '1') { Start-Process 'http://127.0.0.1:5173/' | Out-Null }
Write-Host 'Digital Station is ready at http://127.0.0.1:5173/'
if ($env:STATION_LAN -eq '1') { Write-Host 'LAN access enabled. Open the server PC LAN address on port 5173; configure Windows Firewall for trusted private networks only.' }
