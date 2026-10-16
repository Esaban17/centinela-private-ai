# Decisiones de arquitectura y modelos

Fecha de verificación de las etiquetas: 16 de octubre de 2026 (biblioteca de ollama.com y Hugging Face).
Las cifras de memoria de los modelos de Ollama son los tamaños de descarga publicados; la memoria real
(pesos + caché KV + overhead de Metal) **se mide en la Mac en la Fase 2** con `ollama ps`.

## 1. Modelos elegidos (perfil 48 GB)

| Rol | Etiqueta fijada | Tamaño publicado | Por qué |
|---|---|---|---|
| Agente (tool calling) | `qwen3.6:35b-a3b-q4_K_M` | 24 GB | MoE 35B con ~3B activos: rápido en Apple Silicon. La ficha lo marca con *tools*, *thinking* y *vision*. Contexto 256K (usaremos 16K). |
| Rápido | `qwen3:4b-instruct-2507-q4_K_M` | 2.5 GB | Variante *instruct* sin modo de pensamiento, así que la latencia es baja para clasificar, titular y resumir para voz. |
| Visión / OCR | `qwen3-vl:4b-instruct` | 3.3 GB | Cabe en 48 GB junto al agente. Con 64 GB conviene `qwen3-vl:8b-instruct` (6.1 GB). |
| Embeddings | `bge-m3` | ~1.2 GB | Multilingüe y probado en español; `nomic-embed-text` (v1) está orientado a inglés. |
| STT | `mlx-community/whisper-large-v3-turbo` | 1.6 GB | Vigente (actualizado en abril de 2026), con `language="es"`. |
| TTS | Kokoro-82M (`kokoro-onnx`), voces `em_alex` / `ef_dora` | ~0.35 GB | Ojo: las voces en español son pocas, sin calificación de calidad y con acento de España. Respaldo: `say -v Paulina` (es-MX). |

**Alternativas que caben (se cambian en `config.yaml`, no en el código):**

- Agente plan B: `gemma4:26b-a4b-it-qat` (16 GB) o `qwen3:30b-a3b-instruct-2507-q4_K_M` (19 GB). `gpt-oss:20b` (14 GB) también, pero su español es más débil.
- Rápido: `gemma4:e2b-it-qat` (4.3 GB) o `qwen3.5:2b` (2.7 GB).
- Embeddings: `qwen3-embedding:0.6b`. **No** usar `embeddinggemma-2` todavía: se publicó hace un día.
- Visión plan C: `qwen3.6` es multimodal, así que el propio agente puede leer documentos. Ahorra ~4 GB, pero se pierde el "un modelo por tarea" en el escenario.

Etiquetas `-mlx` (`qwen3.6:35b-mlx`, `gemma4:26b-mlx`): Ollama ya publica variantes MLX para Apple Silicon.
Vale la pena medirlas en la Fase 2 (pueden dar más tokens/s), pero la ficha no documenta sus requisitos. Por
estabilidad, partimos de GGUF `q4_K_M`.

## 2. Presupuesto de memoria estimado

| Componente | Pesos | Caché KV / overhead (estimado) | Total aprox. |
|---|---|---|---|
| Agente qwen3.6 35B-A3B (num_ctx 16K) | 24.0 GB | ~1.0 GB | 25.0 GB |
| Rápido qwen3 4B (num_ctx 4K) | 2.5 GB | ~0.5 GB | 3.0 GB |
| Visión qwen3-vl 4B (num_ctx 8K) | 3.3 GB | ~0.8 GB | 4.1 GB |
| Embeddings bge-m3 | 1.2 GB | — | 1.2 GB |
| Whisper turbo (MLX, fp16) | 1.6 GB | ~0.4 GB | 2.0 GB |
| Kokoro (ONNX) | 0.35 GB | — | 0.4 GB |
| **Total modelos** | | | **≈ 35.7 GB** |

Queda justo en el objetivo de ≤ 35 GB, con margen de error. Si al medir se pasa:

1. Bajar `num_ctx` del agente a 12K (las preguntas del guion no necesitan más).
2. `OLLAMA_KV_CACHE_TYPE=q8_0` + `OLLAMA_FLASH_ATTENTION=1` (reduce la caché KV a la mitad).
3. Agente `gemma4:26b-a4b-it-qat` (−8 GB).

**Límite de memoria de GPU en macOS (importante en 48 GB):** por defecto macOS deja usar a la GPU
alrededor del 75 % de la memoria unificada (~36 GB en una Mac de 48 GB). Si los modelos lo superan, Ollama
reparte capas a la CPU o descarga modelos, y eso es justo el *cold start* que queremos evitar en escena.
`scripts/setup_mac.sh` (Fase 2) subirá el límite con `sudo sysctl iogpu.wired_limit_mb=40960` (se revierte
al reiniciar). Este dato es conocido del ecosistema pero no lo verifiqué en su máquina: se confirma midiendo.

Variables de Ollama para la demo: `OLLAMA_MAX_LOADED_MODELS=4`, `OLLAMA_KEEP_ALIVE=-1`,
`OLLAMA_NUM_PARALLEL=1` (una sola petición a la vez, menos caché KV), `OLLAMA_FLASH_ATTENTION=1`,
`OLLAMA_KV_CACHE_TYPE=q8_0`.

## 3. Ajustes a la arquitectura del prompt

1. **Aprobación humana dentro del protocolo MCP (elicitation), no solo en la UI.** `crear_ticket` y
   `actualizar_ticket` piden la aprobación al *cliente* MCP antes de ejecutar. El parámetro de aprobación lo
   resuelve el servidor, así que el LLM no puede "autoaprobarse". Si el cliente no soporta elicitation, la
   escritura se rechaza. La regla viaja con el servidor, sin importar quién lo consuma. Es un buen punto
   para la charla: el gobierno está en el estándar, no en una pantalla.
2. **SDK `mcp` 2.x.** Desde julio de 2026, `FastMCP` se llama `MCPServer` (`mcp.server.mcpserver`). Fijamos
   `mcp>=2.3,<3`. El servidor funciona con el protocolo nuevo (2026-07-28) y con el clásico; las pruebas
   cubren ambos y MCP Inspector 2.11 lo consume sin cambios.
3. **"Reloj de la demo".** El seed guarda una `fecha_referencia` en la tabla `meta`, y las herramientas la
   usan como "ahora". Así "este mes" y "últimas 24 horas" siempre encuentran los casos sembrados, aunque se
   ensaye días después. Por defecto es la fecha en que se corre `make seed`.
4. **La auditoría vive en el servidor MCP.** Toda llamada se registra, aunque venga de Inspector u otro
   cliente. El orquestador envía `usuario` y `modelo` en `_meta` de cada llamada.
5. **RAG con `sqlite-vec` + FTS5 (híbrido) en vez de Chroma.** Menos dependencias, sin telemetría que
   apagar, un solo archivo. Si el modelo de embeddings falla en escena, la búsqueda léxica sigue citando.
6. **Ollama como servicio (`brew services` / `ollama serve`), no la app de la barra de menú.** La app
   busca actualizaciones, y una conexión saliente pondría el monitor en rojo. Es un riesgo inferido: se
   confirma en `make check-offline`.
7. **Monitor de conexiones por PID.** En macOS, `psutil.net_connections()` global requiere root. Se revisan
   las conexiones de los procesos de la demo (orquestador, MCP, Ollama), que corren con el mismo usuario.
8. **`think: false` por defecto** en el agente y el modelo rápido, para que la primera palabra llegue en
   ≤ 5 s. Se activa solo si mejora la tasa de éxito del guion.
9. **Canal `POS`** agregado a `transacciones.canal`: las compras con tarjeta en comercio no encajaban en
   app/agencia/ATM/ACH.

## 4. Orquestador (Fase 2)

1. **Un solo proceso MCP para toda la demo**, hijo del orquestador por stdio y abierto mientras viva la API.
   Las herramientas se descubren con `tools/list` y se pasan a Ollama tal cual: agregar una herramienta en el
   servidor no requiere tocar el orquestador.
2. **La tarjeta de aprobación es la elicitation del servidor.** El orquestador no decide qué es escritura: el
   servidor pide la confirmación, el orquestador la muestra y devuelve la respuesta. Si nadie contesta en
   `timeouts_s.aprobacion`, se rechaza. Las escrituras se serializan (un candado), para saber a qué turno
   pertenece cada pedido en el protocolo clásico.
3. **Clasificación de intención en paralelo** con la primera llamada al agente: el chip del modelo rápido
   aparece sin sumar latencia. No se usa para filtrar herramientas (eso sí podría romper el guion).
4. **Cliente HTTP propio para Ollama** (`httpx`, `trust_env=False`) en lugar de la librería `ollama`: control de
   streaming y métricas (`prompt_eval_count`, `eval_count`, `eval_duration`), y ninguna variable de proxy del
   entorno puede sacarlo de `localhost`.
5. **Voz en modo `primeras_oraciones` por defecto** (propuesta de la Fase 4): el locutor habla mientras el agente
   escribe y no se espera al resumen del modelo rápido. Con `resumen` se recupera la versión más corta.
6. **El modelo agente se elige midiendo** con `make medir` en la Mac (no se pudo en el contenedor Linux, sin GPU
   ni modelos). Criterio: ≥ 90 % de éxito en las preguntas del guion y primer token ≤ 2 s; si `qwen3.6` no llega,
   probar `gemma4:26b-a4b-it-qat` antes de alargar el prompt.
