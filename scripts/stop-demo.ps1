$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$vite = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'
$logs = Join-Path $root 'logs'
foreach ($name in @('backend','frontend')) {
  $pidFile = Join-Path $logs "$name.pid"
  if (!(Test-Path -LiteralPath $pidFile)) { continue }
  $processId = 0
  [void][int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$processId)
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
  if ($process) {
    $line = [string]$process.CommandLine
    $isOurs = if ($name -eq 'backend') { $line.Contains('backend.app.run') -or $line.Contains('uvicorn backend.app.main:app') } else { $line.Contains($vite) }
    if ($isOurs -and $process.ExecutablePath -and $process.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
      # Windows venv launchers spawn the actual interpreter as a child process.
      Get-CimInstance Win32_Process -Filter "ParentProcessId = $processId" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*backend.app.run*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
      Stop-Process -Id $processId -ErrorAction SilentlyContinue
    } elseif ($isOurs -and $name -eq 'frontend') {
      Stop-Process -Id $processId -ErrorAction SilentlyContinue
    } else {
      Write-Warning "PID $processId no longer identifies this project's $name process; leaving it untouched."
    }
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
Write-Host 'Digital Station processes stopped.'
