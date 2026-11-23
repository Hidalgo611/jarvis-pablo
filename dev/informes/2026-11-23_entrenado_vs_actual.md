# Banco de pruebas en el PC (RTX 4060): modelo entrenado vs. actual

| | qwen3.5:4b (actual) | jarvis-pablo (entrenado, LoRA 716 pasos) |
|---|---|---|
| Frases bien | 20/22 | **22/22** |
| Tiempo por respuesta | 0,6–12,7 s (primera con carga) | 0,4–4,9 s |

Fallos del actual: bloqueó el PC ante «hoy tengo turno de tarde»; ante «olvida lo del Barça» guardó otro dato
en vez de borrarlo. Respuestas raras del actual: «Que te canse, Pablo. ¿Quieres que ajuste el brillo…?»,
«Deja de hablar y reproduce la música», «Te dije que el examen es…».

El entrenado: «Vaya, pues ánimo. Luego me cuentas.», «Normal, ha sido un día largo.», «Hecho, borrado de mi
memoria.», «A las seis de la tarde.», «Perdona. Ahí va música relajante.», «Discord abierto.» / «Hecho.».
Sin preguntas de relleno en las 22 frases.
