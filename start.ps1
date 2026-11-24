param(
    [ValidateRange(1024, 65535)][int]$BackendPort = 8000,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173,
    [string]$PythonPath = "",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
if (!$PythonPath) {
    $PythonPath = Join-Path $project ".venv\Scripts\python.exe"
}
if (!(Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python environment not found. Create .venv and install requirements.txt first."
}
$python = (Resolve-Path -LiteralPath $PythonPath).Path
$node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
$frontendPath = Join-Path $project "frontend"
$vite = Join-Path $frontendPath "node_modules\vite\bin\vite.js"
if (!$node -or !(Test-Path -LiteralPath $vite -PathType Leaf)) {
    throw "Node.js or Vite not found. Install Node.js, then run npm ci in the frontend folder."
}
if ($BackendPort -eq $FrontendPort) {
    throw "Backend and frontend ports must differ."
}

function Assert-PortAvailable([int]$Port) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
    } catch {
        throw "Cannot bind to 127.0.0.1:${Port}. Choose another port."
    } finally {
        $listener.Stop()
    }
}

function Wait-ForHealth($Process, [string]$Url, [string]$ErrorLog) {
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Service exited with code $($Process.ExitCode). See $ErrorLog"
        }
        try {
            Invoke-RestMethod -Uri $Url -TimeoutSec 2 | Out-Null
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Service did not become ready at $Url. See $ErrorLog"
}

Assert-PortAvailable $BackendPort
Assert-PortAvailable $FrontendPort
$logDir = Join-Path $project ("instance\logs\" + [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss-fff"))
New-Item -Path $logDir -ItemType Directory -Force | Out-Null
$backendUrl = "http://127.0.0.1:$BackendPort"
$frontendUrl = "http://127.0.0.1:$FrontendPort"
$priorBackendUrl = $env:BACKEND_URL
$priorPythonUtf8 = $env:PYTHONUTF8
$env:BACKEND_URL = $backendUrl
$env:PYTHONUTF8 = "1"
$backend = $null
$frontend = $null

try {
    $backendError = Join-Path $logDir "backend.error.log"
    $frontendError = Join-Path $logDir "frontend.error.log"
    $backend = Start-Process -FilePath $python -WorkingDirectory $project -WindowStyle Hidden -PassThru `
        -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port $BackendPort --no-access-log" `
        -RedirectStandardOutput (Join-Path $logDir "backend.log") -RedirectStandardError $backendError
    Wait-ForHealth $backend "$backendUrl/health" $backendError
    $frontend = Start-Process -FilePath $node -WorkingDirectory $frontendPath -WindowStyle Hidden -PassThru `
        -ArgumentList "node_modules/vite/bin/vite.js --host 127.0.0.1 --port $FrontendPort --strictPort" `
        -RedirectStandardOutput (Join-Path $logDir "frontend.log") -RedirectStandardError $frontendError
    Wait-ForHealth $frontend "$frontendUrl/api/health" $frontendError
    Write-Host "React      $frontendUrl"
    Write-Host "API docs   $backendUrl/docs"
    Write-Host "Logs       $logDir"
    Write-Host "Press Ctrl+C to stop both services."
    if (!$NoBrowser) { Start-Process $frontendUrl }
    while (!$backend.HasExited -and !$frontend.HasExited) {
        Start-Sleep -Seconds 1
        $backend.Refresh()
        $frontend.Refresh()
    }
    throw "A service stopped. See $logDir"
} finally {
    foreach ($process in @($frontend, $backend)) {
        if ($process -and !$process.HasExited) {
            # A Windows venv launcher can delegate to a child Python process.
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        }
    }
    $env:BACKEND_URL = $priorBackendUrl
    $env:PYTHONUTF8 = $priorPythonUtf8
}
