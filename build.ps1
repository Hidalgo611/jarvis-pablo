# Compila Jarvis.exe (un solo archivo) en dist\Jarvis.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name Jarvis `
  --icon build_assets\jarvis.ico `
  --collect-all sherpa_onnx --collect-all trafilatura --collect-all justext --collect-all courlan `
  --collect-all htmldate --collect-data docx --collect-data fpdf --collect-all ddgs `
  --collect-submodules pycaw --collect-submodules comtypes `
  --hidden-import win32timezone --hidden-import pyautogui `
  --exclude-module tkinter --exclude-module matplotlib --exclude-module scipy --exclude-module sklearn `
  --exclude-module IPython --exclude-module pytest `
  run_jarvis.py
Get-Item dist\Jarvis.exe | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}
