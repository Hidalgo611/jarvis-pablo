# Instala el Jarvis Server en ESTE PC (el que hace de cerebro para el portátil de Pablo).
# Ejecutar en PowerShell como administrador:   powershell -ExecutionPolicy Bypass -File .\instalar_servidor.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Paso($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }

# 1) Python (para el servidor) -------------------------------------------------------------
Paso "Comprobando Python"
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py -or $py -like "*WindowsApps*") {
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $py = (Get-Command python).Source
}
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
& $py -m pip install --quiet --upgrade psutil

# 2) Ollama, optimizado para gastar menos VRAM --------------------------------------------
Paso "Configurando Ollama (atención rápida y caché comprimida: menos VRAM con el mismo modelo)"
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q8_0", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "127.0.0.1:11434", "User")   # Ollama solo en local: entra el Jarvis Server
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "1", "User")
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    winget install -e --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
    $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
}
Get-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
$env:OLLAMA_FLASH_ATTENTION = "1"; $env:OLLAMA_KV_CACHE_TYPE = "q8_0"; $env:OLLAMA_HOST = "127.0.0.1:11434"
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
Start-Sleep 4
$cfg = Get-Content .\jarvis_server.json -ErrorAction SilentlyContinue | ConvertFrom-Json
# el oficial siempre (respaldo y pieza de visión); jarvis-pablo no se descarga: se crea con dev\entreno
ollama pull qwen3.5:4b
if (-not ((ollama list) -match "^jarvis-pablo")) {
    Write-Host "  Aún no existe el modelo entrenado jarvis-pablo: Jarvis usará qwen3.5:4b hasta que lo crees (dev\entreno\LEEME.md)" -ForegroundColor Yellow
}

# 3) Energía: no suspender nunca, pantallas fuera a los 10 min ------------------------------
Paso "Energía: el PC no se suspende (las pantallas sí se apagan) para que Jarvis funcione de noche"
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10

# 4) Firewall: solo desde Tailscale (100.64.0.0/10) -----------------------------------------
Paso "Abriendo el puerto 11500 solo para la red privada de Tailscale"
Get-NetFirewallRule -DisplayName "Jarvis Server" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "Jarvis Server" -Direction Inbound -Protocol TCP -LocalPort 11500 `
    -RemoteAddress 100.64.0.0/10 -Action Allow | Out-Null

# 5) Arranque automático (al iniciar sesión, con permisos altos para el modo noche de la gráfica) --
Paso "Arranque automático del Jarvis Server"
$act = New-ScheduledTaskAction -Execute $pyw -Argument "`"$PSScriptRoot\jarvis_server.py`"" -WorkingDirectory $PSScriptRoot
$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "Jarvis Server" -Action $act -Trigger $trg -Settings $set -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName "Jarvis Server"
Start-Sleep 3

# 6) Datos para el portátil ---------------------------------------------------------------
$key = & $py "$PSScriptRoot\jarvis_server.py" --clave
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue)
$name = ""
if ($ts) { try { $name = "$((tailscale status --json | ConvertFrom-Json).Self.DNSName)".TrimEnd(".") } catch { } }
if (-not $name) {
    $name = "<nombre-de-este-PC-en-Tailscale>"
    Write-Host "`nTailscale no tiene la sesión iniciada en este PC: abre la app de Tailscale, entra con tu cuenta y mira" -ForegroundColor Yellow
    Write-Host "el nombre (o la IP 100.x.x.x) con:  tailscale status" -ForegroundColor Yellow
}
Write-Host "`nListo. En el portátil de Pablo, Ajustes de Jarvis:" -ForegroundColor Green
Write-Host "  Servidor de IA:     http://$($name):11500"
Write-Host "  Clave del servidor: $key"
if (-not $ts) { Write-Host "`nFalta Tailscale: instálalo aquí y en el portátil (misma cuenta): https://tailscale.com/download" -ForegroundColor Yellow }
