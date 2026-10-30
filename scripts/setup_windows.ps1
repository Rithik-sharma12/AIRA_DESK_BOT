# AIRA Phase 1 setup for Windows 11. Run from the repository root in PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass; .\scripts\setup_windows.ps1
$ErrorActionPreference = "Stop"

Write-Host "== AIRA setup ==" -ForegroundColor Cyan

# 1. Python (3.12 recommended: widest wheel support for the Phase 3 voice packages)
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { throw "Python launcher 'py' not found. Install Python 3.12 from python.org (tick 'Add to PATH')." }
& py -3.12 --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Python 3.12 not found; falling back to default 'py'. 3.11/3.12 recommended."
    $pyver = @()
} else { $pyver = @("-3.12") }

# 2. Virtual environment
if (-not (Test-Path ".venv")) { & py @pyver -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt

# 3. .env with a fresh API key
& .\.venv\Scripts\python.exe scripts\generate_api_key.py

# 4. Recommended Ollama memory settings (user scope; restart Ollama afterwards)
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q8_0", "User")
Write-Host "Set OLLAMA_FLASH_ATTENTION=1 and OLLAMA_KV_CACHE_TYPE=q8_0 (quit Ollama from the tray and start it again)." -ForegroundColor Yellow

# 5. Ollama present?
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Warning "Ollama not found. Install: winget install Ollama.Ollama   (or download from ollama.com)"
} else {
    ollama --version
    Write-Host "Pull the models to compare:  ollama pull gemma4:e4b-it-qat ; ollama pull gemma4:12b-it-qat"
}

Write-Host "`nNext:" -ForegroundColor Cyan
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python scripts\check_environment.py"
Write-Host "  python -m pytest"
Write-Host "  python scripts\benchmark_llm.py"
Write-Host "  .\scripts\run_server.ps1      (then in a 2nd window: python scripts\chat_cli.py)"
