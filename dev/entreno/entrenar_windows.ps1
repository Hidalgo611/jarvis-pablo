# Entrena a Jarvis en Windows con la gráfica NVIDIA, de principio a fin, y lo deja en Ollama como «jarvis-pablo».
# Desde la carpeta del código (p. ej. F:\jarvis-src), en PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\dev\entreno\entrenar_windows.ps1
# Se puede volver a lanzar: se salta los pasos ya hechos. Para empezar de cero, borra dev\entreno\salida.
param(
    [string]$Modelo = "Qwen/Qwen3.5-4B",
    [int]$Conversaciones = 2400,
    [switch]$Prueba          # prueba rápida: pocos pasos, para ver que todo funciona antes de las horas de verdad
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root
function Paso($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Run { & $args[0] $args[1..($args.Count - 1)]; if ($LASTEXITCODE -ne 0) { throw "Falló: $($args -join ' ')" } }

# 1) Entorno de Python aparte (no toca el de Jarvis) -------------------------------------
$venv = Join-Path $root ".venv-entreno"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Paso "Creando el entorno de entrenamiento"
    Run py -3.12 -m venv $venv
}
Paso "Instalando librerías (la primera vez tarda: PyTorch con CUDA son ~3 GB)"
Run $py -m pip install --quiet --upgrade pip
Run $py -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cu128
Run $py -m pip install --quiet "transformers>=5" peft accelerate bitsandbytes huggingface_hub safetensors triton-windows flash-linear-attention
# (causal-conv1d no se instala: en Windows necesita compilar con CUDA; sin él va igual de bien, un poco más lento)
Run $py -c "import torch; assert torch.cuda.is_available(), 'PyTorch no ve la gráfica NVIDIA'; print('GPU:', torch.cuda.get_device_name(0), round(torch.cuda.get_device_properties(0).total_memory/2**30,1), 'GB')"

# 2) Datos ----------------------------------------------------------------------------------
if (-not (Test-Path "dev\entreno\datos\jarvis_train.jsonl")) {
    Paso "Generando $Conversaciones conversaciones de entrenamiento"
    # usa el entorno de Jarvis (.venv, el de compilar el .exe): el generador carga el código real de Jarvis
    $pyJarvis = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $pyJarvis)) {
        Paso "No encuentro .venv de Jarvis: instalo sus librerías en el entorno de entrenamiento"
        Run $py -m pip install --quiet -r requirements.txt
        $pyJarvis = $py
    }
    Run $pyJarvis dev\entreno\generar_datos.py --n $Conversaciones
}

# 3) Entrenamiento --------------------------------------------------------------------------
Paso "Liberando la gráfica (descargo los modelos de Ollama; Jarvis los vuelve a cargar solo cuando haga falta)"
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $cargados = (ollama ps) | Select-Object -Skip 1 | ForEach-Object { ($_ -split "\s+")[0] } | Where-Object { $_ }
    foreach ($m in $cargados) { ollama stop $m | Out-Null; Write-Host "  descargado: $m" }
}
Write-Host "  (cierra juegos y vídeos mientras entrena: necesita casi toda la memoria de la gráfica)"
$salida = "dev\entreno\salida"
if ($Prueba) {
    Paso "PRUEBA: 5 pasos para comprobar que todo funciona"
    Run $py dev\entreno\entrenar.py --model $Modelo --out "$salida-prueba" --max-steps 5 --limit 60
    Paso "PRUEBA: fusión y creación en Ollama con el adaptador de prueba (jarvis-prueba)"
    $base = & $py -c "from huggingface_hub import snapshot_download as s; print(s('$Modelo'))"
    Run $py dev\entreno\fusionar.py --base $base.Trim() --lora "$salida-prueba\lora" --out "$salida-prueba\fusionado"
    try { & "$PSScriptRoot\a_ollama.ps1" -Fusionado "$salida-prueba\fusionado" -Nombre jarvis-prueba }
    catch { Write-Host "`nFalla el paso a Ollama: pásale el error a Claude ANTES del entrenamiento largo.`n$_" -ForegroundColor Yellow; exit 1 }
    ollama run jarvis-prueba "Hola, ¿qué tal? Responde en una frase."
    ollama rm jarvis-prueba | Out-Null
    Write-Host "`nLa prueba completa ha ido bien (entrenar, fundir y crear en Ollama). Lanza el script sin -Prueba." -ForegroundColor Green
    exit 0
}
if (-not (Test-Path "$salida\lora\adapter_model.safetensors") -or -not (Test-Path "$salida\terminado.txt")) {
    Paso "Entrenando (varias horas; puedes seguir usando el PC para cosas ligeras)"
    Run $py dev\entreno\entrenar.py --model $Modelo --out $salida
    Set-Content "$salida\terminado.txt" (Get-Date)
}

# 4) Fusión con el modelo original ----------------------------------------------------------
if (-not (Test-Path "$salida\fusionado\config.json")) {
    Paso "Fundiendo lo aprendido con el modelo original"
    $base = & $py -c "from huggingface_hub import snapshot_download as s; print(s('$Modelo'))"
    Run $py dev\entreno\fusionar.py --base $base.Trim() --lora "$salida\lora" --out "$salida\fusionado"
}

# 5) Ollama (GGUF Q4_K_M) ------------------------------------------------------------------
& "$PSScriptRoot\a_ollama.ps1" -Fusionado "$salida\fusionado" -Nombre jarvis-pablo
Write-Host "Compáralo con el actual:  `$env:JARVIS_MODEL='jarvis-pablo'; .\.venv\Scripts\python.exe dev\banco.py"
