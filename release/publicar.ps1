# Compila Jarvis y publica la versión en GitHub: el Jarvis de Pablo la descarga e instala solo (te avisa hablando).
#   1) Sube la versión en jarvis\config.py (VERSION = "x.y.z") y escribe las novedades en release\NOVEDADES.md
#   2) powershell -ExecutionPolicy Bypass -File .\release\publicar.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\.."
Set-Location $root
$ver = [regex]::Match((Get-Content jarvis\config.py -Raw), 'VERSION = "([^"]+)"').Groups[1].Value
if (-not $ver) { throw "No encuentro VERSION en jarvis\config.py" }
$tag = "v$ver"
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "`n==> Compilando Jarvis $ver" -ForegroundColor Cyan
powershell -ExecutionPolicy Bypass -File .\build.ps1
if (-not (Test-Path dist\Jarvis.exe)) { throw "No se ha generado dist\Jarvis.exe" }

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "`nInstalando GitHub CLI (solo la primera vez)…" -ForegroundColor Cyan
    winget install -e --id GitHub.cli --silent --accept-package-agreements --accept-source-agreements
    $env:Path += ";$env:ProgramFiles\GitHub CLI"
}
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nInicia sesión en GitHub (solo la primera vez): elige GitHub.com, HTTPS y 'Login with a web browser'." -ForegroundColor Yellow
    gh auth login
}
Write-Host "`n==> Publicando $tag (rama $branch)" -ForegroundColor Cyan
git push origin $branch
gh release create $tag dist\Jarvis.exe --repo Hidalgo611/jarvis-pablo --target $branch --title "Jarvis $ver" --notes-file release\NOVEDADES.md
if ($LASTEXITCODE -ne 0) { throw "No se ha podido crear la versión $tag (¿ya existe? sube VERSION en jarvis\config.py)" }
Write-Host "`nPublicada Jarvis $ver. El Jarvis de Pablo la detecta en menos de una hora, se lo dice y se actualiza solo." -ForegroundColor Green
