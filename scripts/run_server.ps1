# Start the AIRA laptop server from the repository root.
# Starts Ollama first if it is not running (it is easy to quit it from the tray and forget).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (Test-Path ".venv\Scripts\Activate.ps1") { . .\.venv\Scripts\Activate.ps1 }
chcp 65001 > $null   # UTF-8 console so Tamil text in logs renders

function Test-Ollama {
    try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2 -UseBasicParsing | Out-Null; return $true }
    catch { return $false }
}

if (-not (Test-Ollama)) {
    Write-Host "Ollama is not running - starting it..."
    $app = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
    if (Test-Path $app) { Start-Process -FilePath $app }
    elseif (Get-Command ollama -ErrorAction SilentlyContinue) { Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden }
    else { Write-Host "Ollama is not installed (https://ollama.com/download)." -ForegroundColor Red; exit 1 }
    $ok = $false
    for ($i = 0; $i -lt 30 -and -not $ok; $i++) { Start-Sleep -Milliseconds 500; $ok = Test-Ollama }
    if ($ok) { Write-Host "Ollama is running." -ForegroundColor Green }
    else { Write-Host "Ollama did not start within 15 s. Start it from the Start menu, then run this again." -ForegroundColor Yellow; exit 1 }
}

python -m laptop_server
