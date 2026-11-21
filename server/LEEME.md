# Jarvis Server: el cerebro en el PC de casa

El portátil de Pablo solo escucha, habla y ejecuta. El modelo de IA corre en el PC de casa, que hace de servidor.

```
Portátil de Pablo (Jarvis.exe) ──Tailscale──▶ PC de casa: Jarvis Server :11500 ──▶ Ollama :11434 (solo local)
```

## Instalación (una vez)

1. **Tailscale** en los dos equipos, con la misma cuenta: https://tailscale.com/download.
   Es una red privada cifrada, así que funciona aunque Pablo esté en otra casa o con el móvil compartiendo datos,
   sin abrir puertos del router.
2. En el PC de casa, PowerShell **como administrador**:
   ```
   powershell -ExecutionPolicy Bypass -File .\instalar_servidor.ps1
   ```
   El script instala Python y Ollama si faltan, descarga el modelo, configura la energía (no suspende y apaga las
   pantallas), abre el puerto solo para Tailscale, deja el servidor arrancando solo y al final muestra la **URL** y la
   **clave**.
3. En el portátil: Ajustes de Jarvis, **Servidor de IA** = la URL y **Clave del servidor** = la clave.
   Si el portátil ya no necesita su Ollama local, se puede desinstalar.

## Dejarlo encendido de noche gastando poco

- El PC **no se suspende**, pero las pantallas se apagan a los 10 min (o al momento con `python jarvis_server.py --pantallas`).
  La suspensión de verdad (S3) apaga la CPU y la gráfica, y en ese estado nada puede responder.
- Con el modelo cargado y sin peticiones, la gráfica está en reposo: unos 10–20 W. Un PC de sobremesa sin pantallas
  y sin carga suele estar en unos 40–70 W. Solo consume más en los segundos en que Jarvis está pensando una respuesta.
- Opcional, en `jarvis_server.json`, se puede limitar el consumo máximo de la gráfica de noche (hace falta que el servidor
  corra como administrador, y la tarea programada ya lo hace así):
  ```json
  "night": {"start": "01:00", "end": "08:00", "gpu_power_limit_w": 120}
  ```

## Jugar sin que la IA te quite VRAM (modo juego)

El servidor detecta solo si estás jugando: juegos de Steam, Epic, Riot, Xbox, EA, Ubisoft, Battle.net o GOG, o
cualquier app a pantalla completa que no sea el navegador ni un reproductor. Al detectarlo, **saca el modelo de la VRAM
en unos segundos**, y mientras dure la partida atiende a Pablo según `gaming_mode`:

| `gaming_mode` | Qué hace mientras juegas | VRAM | Inconveniente |
|---|---|---|---|
| `"cpu"` (por defecto) | El mismo modelo, pero en la CPU con 4 hilos (`cpu_threads`) | 0 | Responde algo más lento y usa algo de CPU solo cuando Pablo habla |
| `"cloud"` | Un modelo en la nube de Ollama (`cloud_model`, requiere `ollama signin`) | 0 | Depende de internet y del plan de Ollama |
| `"pause"` | Avisa de que el servidor está en modo juego | 0 | Pablo se queda sin Jarvis durante la partida |

Al cerrar el juego, el servidor vuelve a cargar el modelo en la gráfica. Si algún juego no se detecta, añade su `.exe`
en `"extra_games"`. Si algo se detecta como juego sin serlo, va en `"not_games"`.

Estado del servidor: `http://localhost:11500/jarvis/status`. Registro: `jarvis_server.log`.

## Si el PC de casa está apagado

En el portátil se puede poner un respaldo en `settings.json` (`%LOCALAPPDATA%\Jarvis`), por ejemplo un Ollama local con
un modelo pequeño:
```json
"fallback_url": "http://127.0.0.1:11434",
"fallback_model": "qwen3.5:2b"
```
Si no hay respaldo, Jarvis avisa de que el ordenador servidor parece apagado.
