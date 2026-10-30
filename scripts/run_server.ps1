# Start the AIRA laptop server from the repository root.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (Test-Path ".venv\Scripts\Activate.ps1") { . .\.venv\Scripts\Activate.ps1 }
chcp 65001 > $null   # UTF-8 console so Tamil text in logs renders
python -m laptop_server
