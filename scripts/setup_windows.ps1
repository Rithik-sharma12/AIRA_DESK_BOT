# AIRA setup for Windows 11. Run from the repository root:
#   PowerShell:      Set-ExecutionPolicy -Scope Process Bypass; .\scripts\setup_windows.ps1
#   Command Prompt:  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "== AIRA setup ==" -ForegroundColor Cyan

# 1. Find a usable Python (3.10+; 3.12 recommended). Tries the 'py' launcher first, then
#    python / python3 on PATH. Skips the Microsoft Store placeholder that only opens the Store.
function Test-Python([string]$exe, [string[]]$pre) {
    try {
        $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10) { return $v }
    } catch { }
    return $null
}
$candidates = @(
    @{ exe = "py"; pre = @("-3.12") }, @{ exe = "py"; pre = @("-3.11") }, @{ exe = "py"; pre = @("-3") },
    @{ exe = "python"; pre = @() }, @{ exe = "python3"; pre = @() }
)
$py = $null
foreach ($c in $candidates) {
    if (-not (Get-Command $c.exe -ErrorAction SilentlyContinue)) { continue }
    $ver = Test-Python $c.exe $c.pre
    if ($ver) { $py = $c; Write-Host "Using Python $ver ($($c.exe) $($c.pre -join ' '))"; break }
}
if (-not $py) {
    Write-Host ""
    Write-Host "No usable Python 3.10+ found." -ForegroundColor Red
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/windows/"
    Write-Host "  - tick 'Add python.exe to PATH' on the first installer screen"
    Write-Host "  - then close this window, open a NEW Command Prompt/PowerShell, and run setup again."
    Write-Host "Or:  winget install Python.Python.3.12"
    exit 1
}

# 2. Virtual environment
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $py.exe @($py.pre) -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv" }
}
$venvPy = ".\.venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed (check the internet connection)" }

# 3. .env with a fresh API key
& $venvPy scripts\generate_api_key.py

# 4. Recommended Ollama memory settings (user scope; restart Ollama afterwards)
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q8_0", "User")
Write-Host "Set OLLAMA_FLASH_ATTENTION=1 and OLLAMA_KV_CACHE_TYPE=q8_0 (quit Ollama from the tray and start it again)." -ForegroundColor Yellow

# 5. Ollama present?
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Warning "Ollama not found. Install: winget install Ollama.Ollama   (or download from ollama.com)"
} else {
    ollama --version
    Write-Host "Pull the model:  ollama pull gemma4:e4b-it-qat"
}

Write-Host "`nSetup done. Next:" -ForegroundColor Cyan
Write-Host "  PowerShell:      .\.venv\Scripts\Activate.ps1   then   .\scripts\run_server.ps1"
Write-Host "  Command Prompt:  .venv\Scripts\activate.bat     then   chcp 65001 & python -m laptop_server"
Write-Host "  Then open http://localhost:8765 and enter the pairing code shown in the server window."
