# Convierte el modelo fundido (safetensors) a GGUF con llama.cpp, lo comprime a 4 bits (Q4_K_M) y lo crea en Ollama.
# (Ollama no puede importar Qwen3.5 desde safetensors sin su motor MLX; desde GGUF sí, como el qwen3.5 oficial.)
#   powershell -ExecutionPolicy Bypass -File .\dev\entreno\a_ollama.ps1 -Fusionado dev\entreno\salida\fusionado -Nombre jarvis-pablo
param(
    [Parameter(Mandatory = $true)][string]$Fusionado,
    [string]$Nombre = "jarvis-pablo",
    [string]$Base = "qwen3.5:4b"          # de aquí se reutiliza la pieza de visión (mirar la pantalla)
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root
function Paso($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Run { & $args[0] $args[1..($args.Count - 1)]; if ($LASTEXITCODE -ne 0) { throw "Falló: $($args -join ' ')" } }
$py = Join-Path $root ".venv-entreno\Scripts\python.exe"
$Fusionado = Resolve-Path $Fusionado
$tools = Join-Path $root "dev\entreno\llama.cpp"

# 1) llama.cpp oficial (GitHub de ggml-org): el conversor (código fuente) y llama-quantize (programa ya compilado)
if (-not (Test-Path "$tools\bin\llama-quantize.exe") -or -not (Test-Path "$tools\src\convert_hf_to_gguf.py")) {
    if (Test-Path $tools) { Remove-Item -Recurse -Force $tools }
    Paso "Descargando llama.cpp (herramientas oficiales de conversión)"
    New-Item -ItemType Directory -Force $tools | Out-Null
    $rel = Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
    $tag = $rel.tag_name
    # paquete de Windows x64 para CPU (el nombre cambia entre versiones: se elige por criterio, no por nombre exacto)
    $win = $rel.assets | Where-Object { $_.name -match "win" -and $_.name -match "\.zip$" -and
        $_.name -notmatch "arm64|cuda|cudart|vulkan|hip|sycl|opencl|kompute|rocm|radeon" }
    $asset = ($win | Where-Object { $_.name -match "cpu" } | Select-Object -First 1)
    if (-not $asset) { $asset = ($win | Where-Object { $_.name -match "x64|amd64" } | Select-Object -First 1) }
    if (-not $asset) { $asset = ($win | Select-Object -First 1) }
    if (-not $asset) {
        Write-Host "Paquetes de la versión $tag de llama.cpp:" -ForegroundColor Yellow
        $rel.assets | ForEach-Object { Write-Host "  $($_.name)" }
        throw "No encuentro el paquete de Windows en la versión $tag de llama.cpp (pásale la lista a Claude)"
    }
    Write-Host "  paquete: $($asset.name)"
    Invoke-WebRequest $asset.browser_download_url -OutFile "$tools\bin.zip"
    Expand-Archive "$tools\bin.zip" "$tools\bin" -Force
    Invoke-WebRequest "https://github.com/ggml-org/llama.cpp/archive/refs/tags/$tag.zip" -OutFile "$tools\src.zip"
    Expand-Archive "$tools\src.zip" "$tools\tmp" -Force
    if (Test-Path "$tools\src") { Remove-Item -Recurse -Force "$tools\src" }
    Move-Item (Get-ChildItem "$tools\tmp" | Select-Object -First 1).FullName "$tools\src"
    Remove-Item -Recurse -Force "$tools\tmp", "$tools\bin.zip", "$tools\src.zip"
    $q = Get-ChildItem "$tools\bin" -Recurse -Filter "*quantize*.exe" | Select-Object -First 1
    if (-not $q) { throw "El paquete $($asset.name) no trae el programa de cuantización (llama-quantize)" }
    if ($q.DirectoryName -ne "$tools\bin") { Get-ChildItem $q.DirectoryName | Move-Item -Destination "$tools\bin" -Force }
    if ($q.Name -ne "llama-quantize.exe") { Copy-Item (Join-Path "$tools\bin" $q.Name) "$tools\bin\llama-quantize.exe" }
    Write-Host "  llama.cpp $tag"
}
Run $py -m pip install --quiet -e "$tools\src\gguf-py" sentencepiece protobuf

# 2) safetensors → GGUF (bf16) → Q4_K_M (como el oficial; ~3 GB)
$dir = Split-Path $Fusionado
$f16 = Join-Path $dir "$Nombre-bf16.gguf"
$q4 = Join-Path $dir "$Nombre-q4_k_m.gguf"
Paso "Convirtiendo a GGUF"
Run $py "$tools\src\convert_hf_to_gguf.py" $Fusionado --outfile $f16 --outtype bf16
Paso "Comprimiendo a 4 bits (Q4_K_M)"
Run "$tools\bin\llama-quantize.exe" $f16 $q4 Q4_K_M
Remove-Item $f16

# 3) Modelfile: el modelo entrenado + la pieza de visión del oficial + el renderer/parser de Qwen3.5 (herramientas)
Paso "Creando $Nombre en Ollama"
$oficial = (ollama show $Base --modelfile) -join "`n"
$froms = [regex]::Matches($oficial, "(?m)^FROM (.+)$") | ForEach-Object { $_.Groups[1].Value.Trim() }
# el oficial trae 2 piezas: el modelo (~3 GB) y la de visión (mucho más pequeña); nos quedamos con la pequeña
$vision = $null
if ($froms.Count -ge 2) {
    $vision = ($froms | Where-Object { Test-Path $_ } | Sort-Object { (Get-Item $_).Length } | Select-Object -First 1)
}
$mf = @("FROM $q4")
if ($vision) { $mf += "FROM $vision" }
$mf += @("RENDERER qwen3.5", "PARSER qwen3.5", "PARAMETER temperature 0.3", "PARAMETER top_k 20", "PARAMETER top_p 0.8")
$mfPath = Join-Path $dir "Modelfile.$Nombre"
Set-Content $mfPath $mf
ollama create $Nombre -f $mfPath
if ($LASTEXITCODE -ne 0 -and $vision) {
    Write-Host "  Con la pieza de visión no ha ido; lo creo sin ella (la visión la hará $Base)" -ForegroundColor Yellow
    Set-Content $mfPath ($mf | Where-Object { $_ -ne "FROM $vision" })
    ollama create $Nombre -f $mfPath
    if ($LASTEXITCODE -ne 0) { throw "ollama create ha fallado" }
    Write-Host ('  En los ajustes de Jarvis (settings.json) pon  "vision_model": "' + $Base + '"') -ForegroundColor Yellow
} elseif ($LASTEXITCODE -ne 0) { throw "ollama create ha fallado" }
Write-Host "`nModelo $Nombre creado en Ollama." -ForegroundColor Green
