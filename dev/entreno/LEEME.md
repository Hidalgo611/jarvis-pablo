# Entrenar a Jarvis (LoRA sobre Qwen3.5-4B)

Enseña al modelo el estilo y las decisiones de Jarvis: qué herramienta usar y con qué datos, responder corto,
no inventar, aceptar correcciones, recordar lo dicho y no cambiar de tema. No necesita un modelo más grande: el
resultado ocupa lo mismo que el 4B de ahora.

## En Windows (lo más fácil): un solo script

En PowerShell, desde la carpeta del código, primero `git pull` y después:
```
powershell -ExecutionPolicy Bypass -File .\dev\entreno\entrenar_windows.ps1 -Prueba
```
Si la prueba (5 pasos) va bien, lanza lo mismo sin `-Prueba` para el entrenamiento completo. El script
crea su propio entorno de Python, instala lo necesario, genera los datos, entrena, funde y crea `jarvis-pablo`
en Ollama.

## En Linux / WSL2 (alternativa)

Si en Windows el entrenamiento falla o va muy lento, usa **WSL2 (Ubuntu)**: en el PC con la gráfica NVIDIA. Ahí las librerías rápidas de la "atención
lineal" de Qwen3.5 (`flash-linear-attention`, `causal-conv1d`) funcionan sin problemas. Sin ellas el entrenamiento
es mucho más lento y gasta mucha más memoria.

```bash
# 1) Entorno (una vez), dentro de WSL2 y en la carpeta del código
python3 -m venv .venv-entreno && . .venv-entreno/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install "transformers>=5" peft accelerate bitsandbytes huggingface_hub flash-linear-attention causal-conv1d

# 2) Datos (2400 conversaciones, ~1 min)
python dev/entreno/generar_datos.py --n 2400

# 3) Entrenar (8 GB de VRAM; unas horas). Se puede parar: guarda el adaptador cada 50 pasos
python dev/entreno/entrenar.py --model Qwen/Qwen3.5-4B --out dev/entreno/salida

# 4) Fundir el adaptador con el modelo original
BASE=$(python -c "from huggingface_hub import snapshot_download as s; print(s('Qwen/Qwen3.5-4B'))")
python dev/entreno/fusionar.py --base "$BASE" --lora dev/entreno/salida/lora --out dev/entreno/salida/fusionado
```

5) Importar en Ollama (en Windows, en la carpeta del código), con el Modelfile `dev/entreno/Modelfile`:
```
ollama create jarvis-pablo -f dev/entreno/Modelfile --quantize int4
```

6) Compararlo con el modelo actual, con el banco de pruebas:
```
python dev/banco.py                              # modelo actual (qwen3.5:4b)
set JARVIS_MODEL=jarvis-pablo && python dev/banco.py   # modelo entrenado
```
Solo se cambia `model` a `jarvis-pablo` en los ajustes de Jarvis si gana.

## Estado de la validación (hecha en la nube, sin gráfica, con el modelo mini Qwen3.5-0.8B)
- ✅ Generación de datos: formato idéntico al de Jarvis, 0 errores de hora/plantilla en 2400 conversaciones.
- ✅ Plantilla de Qwen coherente: se entrena solo lo que dice Jarvis.
- ✅ Entrenamiento: carga, LoRA en todas las capas del lenguaje, la pérdida baja y se guarda el adaptador.
- ✅ Fusión: 186 matrices actualizadas en los pesos originales (visión intacta).
- ⚠️ Importación en Ollama (paso 5): sin probar. Esta versión de Ollama importa Qwen3.5 desde safetensors con su
  motor MLX, que aquí no estaba disponible. Si en tu PC falla, la alternativa es convertir a GGUF con llama.cpp
  (`convert_hf_to_gguf.py` + `llama-quantize Q4_K_M`) y usar `FROM ./jarvis-pablo.gguf` en el Modelfile.
