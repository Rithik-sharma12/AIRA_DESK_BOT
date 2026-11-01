# Connect the Raspberry Pi (AIRA's screen) to this laptop: check every piece, fix what can be fixed,
# then (optionally) copy the Pi files and pair, all from Windows.
#
#   .\scripts\connect_pi.ps1                  check only; says exactly what to fix
#   .\scripts\connect_pi.ps1 -Fix             also fix .env / certificate; as Administrator also the
#                                             firewall rule and the hotspot's network type (Private)
#   .\scripts\connect_pi.ps1 -Deploy          copy the raspberry_pi folder to the Pi (asks the Pi password)
#   .\scripts\connect_pi.ps1 -Pair            pair over SSH (needs the server running in another window)
#   -PiHost 192.168.43.57 -PiUser aira       if AIRA.local does not resolve
#
# Never prints keys or tokens. Works in Windows PowerShell 5.1 and PowerShell 7.
param(
    [switch]$Fix,
    [switch]$Deploy,
    [switch]$Pair,
    [string]$PiHost = "AIRA.local",
    [string]$PiUser = "aira"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$script:Problems = 0

function Ok([string]$m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Bad([string]$m)  { Write-Host "  [FIX ] $m" -ForegroundColor Yellow; $script:Problems++ }
function Info([string]$m) { Write-Host "         $m" }
function Head([string]$m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

$IsAdmin = $false
try {
    $IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { }
$Python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

# ------------------------------------------------------------------ 1. .env
Head "1/6 Server settings (.env)"
$EnvPath = Join-Path $Root ".env"
if (-not (Test-Path $EnvPath)) {
    Bad ".env not found. Copy .env.example to .env first (HOW-TO-RUN.md, section 2)."
    exit 1
}
$Lines = [System.Collections.Generic.List[string]]([IO.File]::ReadAllLines($EnvPath))
function Get-EnvValue([string]$name) {
    $value = $null                                       # last one wins, as in the server
    foreach ($l in $Lines) {
        if ($l -match "^\s*$name\s*=\s*(.*?)\s*$") { $value = ($Matches[1] -replace "\s+#.*$", "").Trim('"', "'") }
    }
    return $value
}
$Want = [ordered]@{
    "AIRA_HOST"         = "0.0.0.0"
    "AIRA_ALLOW_LAN"    = "true"
    "AIRA_SSL_CERTFILE" = "certs/aira-dev.crt"
    "AIRA_SSL_KEYFILE"  = "certs/aira-dev.key"
}
$EnvChanged = $false
foreach ($k in $Want.Keys) {
    $v = Get-EnvValue $k
    $good = $false
    if ($k -eq "AIRA_ALLOW_LAN") { $good = ($v -and $v.ToLower() -in @("true", "1", "yes")) }
    elseif ($k -like "AIRA_SSL_*") { $good = [bool]$v }
    else { $good = ($v -eq $Want[$k]) }
    if ($good) { Ok "$k=$v"; continue }
    if ($Fix) {
        $idx = -1                                         # every active KEY= line, else uncomment "# KEY=..."
        for ($i = 0; $i -lt $Lines.Count; $i++) {
            if ($Lines[$i] -match "^\s*$k\s*=") { $Lines[$i] = "$k=$($Want[$k])"; $idx = $i }
        }
        if ($idx -lt 0) {
            for ($i = 0; $i -lt $Lines.Count; $i++) {
                if ($Lines[$i] -match "^\s*#\s*$k\s*=") { $Lines[$i] = "$k=$($Want[$k])"; $idx = $i; break }
            }
        }
        if ($idx -lt 0) { $Lines.Add("$k=$($Want[$k])") }
        $EnvChanged = $true
        Ok "$k=$($Want[$k])  (set now)"
    } else {
        Bad "$k should be $($Want[$k])  (now: $(if ($v) { $v } else { 'not set' }))"
    }
}
if ($EnvChanged) {   # UTF-8 without BOM, so the first line still parses
    [IO.File]::WriteAllLines($EnvPath, $Lines, (New-Object Text.UTF8Encoding $false))
    Info "Saved .env. Restart the server so it uses the new settings."
}
$Port = Get-EnvValue "AIRA_PORT"; if (-not $Port) { $Port = "8765" }
if ((Get-EnvValue "AIRA_DEVICE_DISCOVERY") -eq "false") {
    Info "AIRA_DEVICE_DISCOVERY=false: the Pi cannot find the laptop by itself; pairing below uses the address."
}

# ------------------------------------------------------------------ 2. certificate
Head "2/6 HTTPS certificate"
$CertRel = Get-EnvValue "AIRA_SSL_CERTFILE"; if (-not $CertRel) { $CertRel = "certs/aira-dev.crt" }
$CertPath = Join-Path $Root $CertRel
$KeyRel = Get-EnvValue "AIRA_SSL_KEYFILE"; if (-not $KeyRel) { $KeyRel = "certs/aira-dev.key" }
if (-not ((Test-Path $CertPath) -and (Test-Path (Join-Path $Root $KeyRel)))) {
    if ($Fix) {
        & $Python scripts\make_dev_cert.py | Out-Host
        if ($LASTEXITCODE -ne 0) { Bad "make_dev_cert.py failed (is the venv set up? pip install -r requirements.txt)" }
    } else {
        Bad "No certificate yet. Run:  python scripts\make_dev_cert.py   (or this script with -Fix)"
    }
}
$Fingerprint = $null
$FpShown = $null
if (Test-Path $CertPath) {
    try {
        $pem = [IO.File]::ReadAllText($CertPath)
        $b64 = ($pem -replace "-----(BEGIN|END) CERTIFICATE-----", "" -replace "\s", "")
        $der = [Convert]::FromBase64String($b64)
        $sha = [Security.Cryptography.SHA256]::Create()
        $Fingerprint = -join ($sha.ComputeHash($der) | ForEach-Object { $_.ToString("x2") })
        $FpShown = (0..3 | ForEach-Object { $Fingerprint.Substring($_ * 4, 4) }) -join ":"
        $FpShown = $FpShown.ToUpper()
        Ok "$CertRel  fingerprint $FpShown  (the Pi shows the same when pairing)"
    } catch { Bad "Could not read $CertRel ($($_.Exception.Message)). Re-run python scripts\make_dev_cert.py" }
}

# ------------------------------------------------------------------ 3. network
Head "3/6 Network (phone hotspot)"
$Profiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue |
    Where-Object { $_.IPv4Connectivity -ne "Disconnected" })
$LaptopIps = @()
if (-not $Profiles) { Bad "This laptop is not connected to any network. Join the phone hotspot." }
foreach ($p in $Profiles) {
    $ips = @(Get-NetIPAddress -InterfaceIndex $p.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -ne "127.0.0.1" } |
        ForEach-Object { $_.IPAddress })
    $LaptopIps += $ips
    $label = "$($p.Name) on $($p.InterfaceAlias): $($ips -join ', ')"
    if ($p.NetworkCategory -eq "Public") {
        if ($Fix -and $IsAdmin) {
            try {
                Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
                Ok "$label  (switched Public -> Private)"
            } catch { Bad "$label is Public and could not be switched: $($_.Exception.Message)" }
        } else {
            Bad "$label is a Public network: Windows blocks the Pi. Make it Private:"
            Info "Settings > Network & internet > Wi-Fi > $($p.Name) > Private network"
            Info "(or run this script as Administrator with -Fix)"
        }
    } else { Ok "$label ($($p.NetworkCategory))" }
}

# The Pi: resolve AIRA.local (Windows understands .local names) and ping it
$PiIp = $null
try {
    $PiIp = ([Net.Dns]::GetHostAddresses($PiHost) | Where-Object { $_.AddressFamily -eq "InterNetwork" } |
        Select-Object -First 1).IPAddressToString
} catch { }
if ($PiIp) {
    $alive = Test-Connection -ComputerName $PiIp -Count 1 -Quiet -ErrorAction SilentlyContinue
    if ($alive) { Ok "Raspberry Pi $PiHost is at $PiIp and answers" }
    else { Bad "$PiHost is $PiIp but does not answer. Is the Pi on and on the same hotspot?" }
} else {
    Bad "Cannot find $PiHost. Is the Pi on the hotspot? Find its address in the phone's hotspot list (or: hostname -I on AIRA), then use -PiHost <address>"
}
# Pick the laptop address on the Pi's network
$LaptopIp = $LaptopIps | Select-Object -First 1
if ($PiIp) {
    $prefix = ($PiIp -split "\.")[0..2] -join "."
    $same = $LaptopIps | Where-Object { $_ -like "$prefix.*" } | Select-Object -First 1
    if ($same) { $LaptopIp = $same }
    elseif ($LaptopIps) { Bad "The laptop ($($LaptopIps -join ', ')) and the Pi ($PiIp) look like different networks." }
}

# ------------------------------------------------------------------ 4. firewall
Head "4/6 Windows firewall (TCP $Port in)"
$Rule = Get-NetFirewallRule -DisplayName "AIRA web $Port" -ErrorAction SilentlyContinue
if ($Rule -and $Rule.Enabled -eq "True") { Ok "Rule 'AIRA web $Port' allows the Pi in (Private networks)" }
elseif ($Fix -and $IsAdmin) {
    if ($Rule) { Enable-NetFirewallRule -DisplayName "AIRA web $Port" }
    else {
        New-NetFirewallRule -DisplayName "AIRA web $Port" -Direction Inbound -Protocol TCP -LocalPort $Port `
            -Action Allow -Profile Private | Out-Null
    }
    Ok "Rule 'AIRA web $Port' added (Private networks only)"
} else {
    Bad "No firewall rule for port $Port. Run this script as Administrator with -Fix, or once as Administrator:"
    Info "New-NetFirewallRule -DisplayName `"AIRA web $Port`" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private"
}
# Windows adds a *block* rule for python.exe if its "allow access?" popup was cancelled; block beats allow
Write-Host "         (checking for blocked python.exe rules, a few seconds...)"
$Blocks = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue |
    Where-Object { $_.Program -like "*python*.exe" } | Get-NetFirewallRule -ErrorAction SilentlyContinue |
    Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Block" -and $_.Enabled -eq "True" })
if (-not $Blocks) { Ok "No firewall rule blocks python.exe" }
foreach ($b in $Blocks) {
    if ($Fix -and $IsAdmin) { $b | Disable-NetFirewallRule; Ok "Disabled block rule '$($b.DisplayName)' ($($b.Profile))" }
    else { Bad "Block rule '$($b.DisplayName)' ($($b.Profile)) stops python.exe; run as Administrator with -Fix to disable it" }
}

# ------------------------------------------------------------------ 5. server
Head "5/6 AIRA server"
$Listen = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
$ServerUp = $false
if (-not $Listen) {
    Bad "The server is not running. Start it in another window:  .\scripts\run_server.ps1"
} elseif (-not ($Listen | Where-Object { $_.LocalAddress -in @("0.0.0.0", "::") })) {
    Bad "The server only listens on $($Listen[0].LocalAddress). Restart it after the .env fix above."
} else {
    $ServerUp = $true
    Ok "Listening on port $Port for the network"
    if ($LaptopIp) {
        try {
            $c = New-Object Net.Sockets.TcpClient
            $ok = $c.ConnectAsync($LaptopIp, [int]$Port).Wait(2000) -and $c.Connected
            $c.Close()
            if ($ok) { Ok "Reachable at https://${LaptopIp}:$Port" } else { Bad "Not reachable at ${LaptopIp}:$Port" }
        } catch { Bad "Not reachable at ${LaptopIp}:$Port ($($_.Exception.Message))" }
    }
}

# ------------------------------------------------------------------ 6. the Pi
Head "6/6 Raspberry Pi"
$Target = if ($PiIp) { $PiIp } else { $PiHost }
$Url = if ($LaptopIp) { "https://${LaptopIp}:$Port" } else { "https://<laptop-ip>:$Port" }
$HaveSsh = [bool](Get-Command ssh -ErrorAction SilentlyContinue)
if (-not $HaveSsh) { Bad "ssh / scp missing: Settings > System > Optional features > OpenSSH Client" }

if ($Deploy) {
    if (-not $HaveSsh) { exit 1 }
    Write-Host "Copying raspberry_pi to ${PiUser}@${Target}:~/  (enter the Pi password if asked)"
    scp -r raspberry_pi "${PiUser}@${Target}:~/"
    if ($LASTEXITCODE -eq 0) { Ok "Copied. Then on AIRA:  cd ~/raspberry_pi && sudo bash install.sh" }
    else { Bad "Copy failed (exit $LASTEXITCODE)" }
}

if ($Pair) {
    if (-not $HaveSsh) { exit 1 }
    if (-not $ServerUp) { Bad "Start the server first (.\scripts\run_server.ps1), then run -Pair again."; exit 1 }
    if ($FpShown) { Info "The Pi shows 'Laptop certificate fingerprint': it must be $FpShown" }
    Info "Then type the pairing code that appears in the SERVER window."
    ssh -t "${PiUser}@${Target}" "cd ~/raspberry_pi && python3 -m aira_pi pair --laptop $Url && sudo systemctl restart aira-pi"
    if ($LASTEXITCODE -eq 0) { Ok "Paired. AIRA's screen restarts and shows ONLINE in a few seconds." }
    else { Bad "Pairing did not finish (exit $LASTEXITCODE). The messages above say why." }
}

Write-Host ""
if ($script:Problems -eq 0) { Write-Host "Everything checked is ready." -ForegroundColor Green }
else { Write-Host "$($script:Problems) thing(s) to fix above." -ForegroundColor Yellow }
if (-not $Pair) {
    Write-Host ""
    Write-Host "To pair, either run:  .\scripts\connect_pi.ps1 -Pair"
    Write-Host "or on AIRA:"
    Write-Host "    cd ~/raspberry_pi"
    Write-Host "    python3 -m aira_pi pair --laptop $Url"
    Write-Host "    sudo systemctl restart aira-pi"
}
