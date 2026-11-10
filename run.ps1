# One-command local start (Windows PowerShell). Builds the UI if needed, then serves API + UI on http://localhost:8765
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8765
$url = "http://localhost:$port"

# Port already in use? Either the app is already running, or something else holds the port.
function Get-PortOwner {
    $line = netstat -ano | Select-String ":$port\s+\S+\s+LISTENING" | Select-Object -First 1
    if ($line) { return ($line.ToString().Trim() -split "\s+")[-1] }
    return $null
}
$alreadyUp = $false
try { $alreadyUp = [bool](Invoke-RestMethod "$url/api/health" -TimeoutSec 3).ok } catch { }
if ($alreadyUp) {
    $procId = Get-PortOwner
    Write-Host "AP Autopilot is already running at $url$(if ($procId) { " (PID $procId)" }). Opening browser." -ForegroundColor Green
    if ($procId) { Write-Host "To restart it:  Stop-Process -Id $procId ; .\run.ps1" -ForegroundColor Yellow }
    Start-Process $url
    exit 0
}
$busy = $false
try {
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
    $probe.Start(); $probe.Stop()
} catch { $busy = $true }
if ($busy) {
    $procId = Get-PortOwner
    $proc = if ($procId) { Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue }
    Write-Host "Port $port is used by another program$(if ($procId) { ": PID $procId $($proc.Name)" })." -ForegroundColor Red
    if ($proc.CommandLine) { Write-Host "  $($proc.CommandLine)" }
    if ($procId) { Write-Host "Stop it with:  Stop-Process -Id $procId" -ForegroundColor Yellow }
    else { Write-Host "Find it with:  netstat -ano | findstr :$port" -ForegroundColor Yellow }
    exit 1
}

if (-not (Test-Path "$root\backend\.venv")) {
    python -m venv "$root\backend\.venv"
    & "$root\backend\.venv\Scripts\python.exe" -m pip install -r "$root\backend\requirements.txt"
}
if (-not (Test-Path "$root\frontend\dist")) {
    Push-Location "$root\frontend"; npm install; npm run build; Pop-Location
}
Write-Host "Starting AP Autopilot at $url  (Ctrl+C to stop)" -ForegroundColor Green
Push-Location "$root\backend"
try {
    & ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port
} finally {
    Pop-Location
}
