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
`scripts/setup_mac.sh --limite-gpu` sube el límite con `sudo sysctl iogpu.wired_limit_mb=40960` (memoria total
menos 8 GB; se revierte al reiniciar). Este dato es conocido del ecosistema pero no lo verifiqué en su máquina: se confirma midiendo.

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

## 4. Fase 3: RAG y visión

1. **Un fragmento por sección.** Las políticas se cortan por `##` / `###` (máx. 1 500 caracteres; si una sección
   es más larga se parte por párrafos y la segunda parte se cita como «(cont.)»). Así la cita es siempre
   «documento + sección», que es lo que el agente dice en voz alta. El embedding incluye el título del
   documento y de la sección como contexto.
2. **Búsqueda híbrida con RRF** (vectorial + BM25 de FTS5, `k=60`). La léxica cubre siglas y nombres propios
   (VKS, NSX, etcd) donde los embeddings flaquean, y sirve de plan B si el modelo de embeddings no responde.
3. **El índice recuerda con qué modelo se construyó.** Si se cambia `modelos.embeddings` sin reindexar, la
   búsqueda no mezcla espacios vectoriales: usa solo la léxica y pide `make ingest`.
4. **La visión es un gancho del orquestador, la validación es MCP.** Extraer campos es trabajo del modelo de
   visión (chip propio en la UI); cruzarlos con la base es una herramienta MCP de solo lectura
   (`validar_documento`), auditada como las demás y reutilizable desde cualquier cliente MCP.
5. **Salida estructurada con el JSON Schema de Pydantic** (`format` de Ollama) y `temperature: 0`. El prompt le
   pide al modelo transcribir sin corregir nombres: la discrepancia de titular la decide el código, no el modelo.
6. **Comparación de nombres explicable.** Se ignoran tildes y mayúsculas (la lectura óptica las pierde). Si cada
   palabra difiere en una letra como máximo es «parecido» (advertencia: posible error de lectura); cualquier
   otra diferencia es discrepancia, y si el nombre se parece se señala posible suplantación.
7. **Documentos generados desde la base.** `make documentos` usa cuentas y movimientos reales de `banco.db`, por
   eso se regeneran después de cada `make seed` y no se versionan. `manifiesto.json` guarda los campos
   esperados para medir la extracción en la Mac (`make vision`).
8. **Fuentes locales.** Los PNG se dibujan con Arial de macOS (o DejaVu en Linux); la ruta es configurable en
   `vision.fuentes`. No se descarga nada.

## 5. Orquestador (Fase 2)

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

## 6. Endurecimiento y ensayo (Fase 6)

1. **Guardia de salida además del monitor.** El monitor (psutil) *observa*; la guardia *impide*. Un audit hook de
   Python (PEP 578) en el orquestador, el servidor MCP y los scripts rechaza `socket.connect`/`sendto` a direcciones
   que no sean loopback y `getaddrinfo`/`gethostbyname` de nombres que no sean `localhost` (resolver un nombre ya es
   una consulta DNS que sale). Falla con `PermissionError`, que las librerías tratan como un error de red normal,
   y queda en el log. No cubre a Ollama (es Go): para él están `OLLAMA_HOST=127.0.0.1`, `make ollama` y el monitor.
2. **Un solo camino para warmup, ensayo, medir y check-offline** (`scripts/guion.py`): el orquestador real, el
   servidor MCP real por stdio y una copia de la base. Lo que se ensaya es exactamente lo que sube al escenario.
3. **El warmup carga los modelos por el mismo cliente que la demo.** Si el `num_ctx` de la petición cambiara,
   Ollama recargaría el modelo en escena; por eso no se usa `ollama run` ni otro cliente. Además corre el guion una
   vez: con `OLLAMA_NUM_PARALLEL=1`, el prefijo del prompt (sistema + herramientas) queda en la caché del modelo.
4. **La voz se precalienta dentro del orquestador.** Whisper y Kokoro viven en su proceso, así que un warmup en otro
   proceso no le serviría. La API los carga al arrancar (en segundo plano) y `make warmup` se lo vuelve a pedir con
   `POST /api/precalentar`. Si Kokoro no carga y no hay respaldo, el warmup lo dice (antes informaba «ok»).
5. **El acto de visión entra al guion** (`documento_id: demo-boleta_deposito_discrepancia.png`): los atajos de la UI
   pasan a ser 6 y el ensayo mide también la visión. Cada paso puede exigir `respuesta_contiene` (p. ej. «titular»).
6. **El ensayo revisa la auditoría y la red en cada corrida**, no solo el tool calling: cuenta que cada llamada esté
   en `auditoria` con el modelo que la pidió y muestrea el monitor de conexiones cada segundo.
7. **El primer audio se mide desde que el orquestador recibe la pregunta** (`--voz` entra por voz con la pregunta
   dicha por Kokoro). En el navegador se suma la subida del audio, local y de milisegundos.
8. **`make ollama` rechaza la app de la barra y `brew services`**: ambos levantan Ollama sin las variables de la
   demo, y la app busca actualizaciones por internet.
9. **`make check-offline` sin red es la prueba de verdad.** Con red solo es parcial (lo dice); `ESTRICTO=1` exige el
   Wi-Fi apagado. Sin Ollama ni modelos, `--sin-prueba` hace solo las revisiones estáticas.
10. **Medido en el contenedor (Linux, sin GPU), con un Ollama simulado por HTTP:** warmup, ensayo y check-offline
    completan los 6 actos con 0 conexiones salientes. Las latencias reales, la memoria y el primer audio se miden en
    la Mac (GUION.md §6).

## 7. Precios del costo equivalente (2026-10-17)

El Panel de Soberanía compara la sesión con lo que habría costado en una API Frontier de pago. Como referencia se usa
el **precio de lista público de Claude Opus 5.5: US$4 por millón de tokens de entrada y US$20 por millón de salida**
([precios de la API](https://platform.claude.com/docs/en/about-claude/pricing), consultado el 2026-10-17).

1. **Precio de lista, sin descuentos.** No se aplican caché de prompt ni Batch API: el panel compara contra lo que
   pagaría un banco que manda cada pregunta a la API tal cual, y así la cifra no depende de supuestos de uso.
2. **Se nombra el modelo en el panel** (`costos.referencia`), para que nadie lo lea como una tarifa genérica.
3. **El tipo de cambio sigue en 7.7 GTQ/USD.** Es referencial; si cambia, basta editar `costos.tipo_cambio_gtq`.
4. **Ejemplo:** una sesión con 60 000 tokens de entrada y 3 000 de salida da US$0.30 (≈ Q2.31) frente a
   «Costo marginal local: Q0.00».
5. Los precios de las API cambian; antes del evento conviene revisar la página de precios y actualizar la fecha aquí.

## 8. Selección de modelos por RAM y perfil de 24 GB (2026-10-21)

Pedido: que Centinela corra en una Mac de 24 GB y que se puedan elegir los modelos, con una alerta si la
combinación no cabe en la RAM del equipo donde corre.

- **Perfiles en `config.yaml → seleccion_modelos`.** `perfil: auto` elige el perfil más grande cuya
  `ram_minima_gb` cabe en la RAM real (psutil): 40 GB o más → `48gb` (la sección `modelos` de siempre), menos →
  `24gb`. También se puede fijar `48gb`, `24gb` o `manual`. El perfil se aplica dentro de `config.cargar()`, así
  orquestador, servidor MCP, `make pull-models` y `make warmup` usan los mismos modelos.
- **Perfil `24gb`:** agente `gemma4:12b-it-qat` (7.2 GB de descarga, *tools* y *vision*) que **también hace la
  visión**, más `qwen3:4b-instruct-2507` (rápido) y `bge-m3` (el mismo índice de políticas que en 48 GB).
  Agente y visión usan el mismo `num_ctx` (16K) para que Ollama no recargue el modelo al pasar de una tarea a
  otra. Estimado ≈ 13.7 GB de modelos + 6 GB de sistema ≈ 19.7 GB de 24 GB. Se descartó `gemma4:26b-a4b-it-qat`
  (16 GB) como agente: con Whisper en la GPU pasa del ~16 GB que macOS deja a la GPU en 24 GB y no queda aire
  para el navegador. **Las cifras del 12B son estimadas: hay que medir en la Mac de 24 GB** con
  `make warmup` (`ollama ps`) y `make ensayo` (calidad del tool calling en español).
- **Estimación (`comun/modelos.py`).** Catálogo en `config.yaml` con la memoria de cada modelo ya cargado:
  `medido` (de `ollama ps`, GUION.md §6) o `estimado` (descarga + caché KV q8_0). Un modelo que se comparte
  entre roles se cuenta una vez; uno fuera del catálogo se estima con su tamaño en `/api/tags`. Se suman
  Whisper y Kokoro (corren en el orquestador) y una reserva de 6 GB para macOS, navegador y Python. Nivel:
  `ok`, `justo` (≥ 90 % de la RAM) o `excede` (> 100 %). Aparte avisa si lo que va a la GPU (Ollama + Whisper
  MLX) pasa el límite de GPU de macOS (2/3 de la RAM hasta 36 GB, 3/4 arriba, o `iogpu.wired_limit_mb`).
- **Dónde salta la alerta:** píldora «⚙ Modelos» y franja roja/ámbar en la UI, log del orquestador al arrancar,
  `make modelos` (sale con código 1 si no cabe) y la fase «RAM del equipo» de `make warmup`, que además ajusta el
  presupuesto de `ollama ps` a lo que deja libre este equipo.
- **Selector en la UI** (`POST /api/modelos`): agente, rápido y visión cambian en caliente (se descargan de
  Ollama los que quedan sin uso y se precargan los nuevos). Embeddings y voz rigen al reiniciar: el servidor MCP
  tiene su propio proceso y el índice se armó con el modelo anterior (`make ingest`). Rechaza modelos `:cloud`,
  modelos no descargados (no hay red para bajarlos) y, sin confirmación explícita, una selección que no cabe. La
  elección se guarda en `config.local.yaml` (no se versiona; se borra para volver a `config.yaml`).
- `CENTINELA_RAM_GB=24` (o `make modelos RAM_GB=24`) simula otra máquina para ensayar desde la Mac de 48 GB.
