# Contrato de eventos: orquestador ↔ UI (y puntos de enganche)

Versión del contrato: **1** (revisión 1.1, solo agregados). Dueño: Fase 2 (orquestador). Un cambio incompatible sube
la versión y se avisa a las fases 3, 4 y 5.

Revisión 1.1: mensaje `preferencias`, evento `pong`, código `documento_ilegible`, voz en modo
`primeras_oraciones` (locutor de la Fase 4) y `ollama.chat` acepta imágenes en bytes y `opciones`.

Todo es local: el orquestador escucha en `127.0.0.1:<orquestador.puerto>` (8765 por defecto). La UI se sirve
desde el mismo origen (`web/`), así que no hace falta CORS ni nada externo.

## 1. Transporte

| Canal | Ruta | Uso |
|---|---|---|
| WebSocket (principal) | `GET /ws/{sesion_id}` | Bidireccional: la UI envía mensajes y aprobaciones; recibe todos los eventos. |
| SSE (alternativa) | `GET /api/sesiones/{sesion_id}/eventos` | Solo recepción. Respeta `Last-Event-ID` (= `seq`) para reanudar. |
| REST | ver §4 | Equivalentes de lo que la UI envía por WebSocket, más subida de audio e imágenes. |

- `sesion_id` lo genera la UI (UUID o cualquier cadena `[A-Za-z0-9_-]{1,64}`). La sesión se crea al primer uso y
  guarda la memoria de la conversación mientras el orquestador esté vivo.
- Al conectar por WebSocket, el servidor envía primero `sesion.lista`. Con `?desde_seq=N` reenvía los eventos
  guardados con `seq > N` (búfer de los últimos 1000 por sesión).
- Varios clientes pueden escuchar la misma sesión (p. ej. la UI y una terminal de logs): todos reciben lo mismo.

## 2. Sobre de cada evento (servidor → UI)

```json
{
  "v": 1,
  "seq": 42,
  "tipo": "herramienta.fin",
  "sesion_id": "demo-1",
  "turno_id": "t-0003",
  "ts": "2026-10-16T18:30:12.345Z",
  "datos": { }
}
```

- `seq`: entero creciente por sesión (sirve de `id` en SSE).
- `turno_id`: `null` en eventos fuera de un turno (`sesion.lista`, `sesion.acumulado`, `error` de protocolo).
- `ts`: hora real del equipo en UTC (no el «reloj de la demo» de la base).
- Los datos que viajan a la UI **no** se enmascaran (la UI es local y la persona los necesita). Lo que se
  enmascara son los logs y la auditoría.
- La UI debe ignorar tipos que no conozca (compatibilidad hacia adelante).

## 3. Tipos de evento

`rol` de modelo siempre es uno de: `agente`, `rapido`, `vision`, `embeddings`, `stt`, `tts`.
`etapa` siempre es una de: `stt`, `vision`, `intencion`, `llm`, `herramienta`, `aprobacion`, `resumen`, `tts`.

### Sesión

| tipo | datos |
|---|---|
| `sesion.lista` | `banco`, `marca_demo`, `modelos` (`{rol: nombre}`), `herramientas` (`[{nombre, titulo, descripcion, escritura}]`), `voz_disponible` (bool), `vision_disponible` (bool), `version_contrato` |
| `sesion.acumulado` | `turnos`, `tokens_entrada`, `tokens_salida`, `herramientas_ejecutadas`, `documentos_analizados`, `aprobaciones` (`{aprobadas, rechazadas}`), `modelos_usados` (lista de nombres distintos). Se emite al cerrar cada turno. |

### Turno

| tipo | datos |
|---|---|
| `turno.inicio` | `entrada` (`texto` \| `voz` \| `documento`), `texto`, `documento_id` (o `null`) |
| `transcripcion` | `texto`, `modelo`, `duracion_ms`, `idioma`. Llega antes del `turno.inicio` de un turno de voz (mismo `turno_id`). |
| `modelo.uso` | El «chip»: `paso` (entero, orden dentro del turno), `rol`, `modelo`, `tarea` (texto corto en español, p. ej. «Clasificar intención»). Uno por cada vez que un modelo trabaja. |
| `intencion` | `intencion` (`cartera` \| `fraude` \| `politicas` \| `documento` \| `noc` \| `tickets` \| `otro`), `modelo`, `duracion_ms` |
| `llm.inicio` | `paso`, `rol`, `modelo`, `iteracion` (vuelta del loop, desde 1) |
| `llm.delta` | `paso`, `texto` (fragmento nuevo de la respuesta; concatenar en orden) |
| `llm.fin` | `paso`, `rol`, `modelo`, `iteracion`, `ttft_ms`, `duracion_ms`, `tokens_entrada`, `tokens_salida`, `tokens_por_s`, `herramientas_pedidas` (lista de nombres) |
| `herramienta.inicio` | `llamada_id`, `paso`, `nombre`, `argumentos` (objeto), `escritura` (bool) |
| `aprobacion.solicitada` | `aprobacion_id`, `llamada_id`, `herramienta`, `mensaje` (texto del servidor MCP: «Centinela quiere crear este ticket: …»), `argumentos`, `expira_en_s` |
| `aprobacion.resuelta` | `aprobacion_id`, `llamada_id`, `aprobada` (bool), `comentario`, `origen` (`persona` \| `tiempo_agotado` \| `cancelado`) |
| `herramienta.fin` | `llamada_id`, `nombre`, `ok` (bool), `duracion_ms`, `resumen` (una línea), `resultado` (el `structuredContent` de MCP, completo) |
| `citas` | `citas`: `[{documento, seccion, fragmento, puntaje}]`. Se emite tras cada `buscar_politicas` con fragmentos. |
| `documento.extraido` | `documento_id`, `nombre`, `tipo_documento`, `campos` (objeto validado por Pydantic), `modelo`, `duracion_ms`, `advertencias` (lista) |
| `respuesta.final` | `texto` (respuesta escrita completa), `resumen_hablado` (texto corto para voz, o `null`), `citas` |
| `voz.audio` | `indice` (orden de la oración), `texto`, `url` (`/api/audio/{id}`, servido por el orquestador), `formato` (`wav`), `motor` (`kokoro` \| `say`), `duracion_ms` |
| `turno.fin` | `ok` (bool), `cancelado` (bool), `iteraciones`, `metricas` (ver abajo) |

`metricas` de `turno.fin`:

```json
{
  "latencias_ms": {"stt": 0, "vision": 0, "intencion": 180, "llm": 2400, "herramientas": 95,
                   "aprobacion": 4100, "resumen": 300, "tts": 0, "total": 7300},
  "ttft_ms": 640,
  "tokens_entrada": 3120,
  "tokens_salida": 210,
  "tokens_por_s": 38.5,
  "modelos": ["qwen3.6:35b-a3b-q4_K_M", "qwen3:4b-instruct-2507-q4_K_M"],
  "herramientas": ["buscar_cliente", "resumen_cliente"]
}
```

`ttft_ms` es el tiempo desde `turno.inicio` hasta el primer `llm.delta` con texto para la persona. `tokens_por_s`
es el del modelo agente (tokens de salida / tiempo de generación que reporta Ollama).

### Errores y avisos

| tipo | datos |
|---|---|
| `aviso` | `mensaje` (amable, para mostrar), `etapa` |
| `error` | `codigo`, `mensaje` (amable, en español), `etapa` (o `null`), `recuperable` (bool) |

Códigos: `turno_en_curso`, `tiempo_agotado`, `ollama_no_disponible`, `mcp_no_disponible`, `max_iteraciones`,
`voz_no_disponible`, `vision_no_disponible`, `documento_no_encontrado`, `documento_ilegible`, `mensaje_invalido`,
`interno`.
Un turno con error siempre termina con `turno.fin` (`ok: false`): la UI nunca queda esperando.

## 4. Lo que la UI envía

Por WebSocket (JSON):

```json
{"tipo": "mensaje", "texto": "¿Cómo está la cartera de Lucía Cojtí?", "documento_id": null}
{"tipo": "aprobacion", "aprobacion_id": "ap-7f3c", "aprobar": true, "comentario": ""}
{"tipo": "cancelar"}
{"tipo": "preferencias", "hablar": false}
{"tipo": "ping"}
```

`ping` responde con un evento `pong` (sin `turno_id`). `preferencias` con `hablar: false` es el botón de silenciar:
corta la voz que está sonando y no sintetiza en los turnos siguientes (`true` la reactiva). Un `mensaje` mientras hay un turno en curso devuelve
`error` con `turno_en_curso`.

Por REST:

| Método y ruta | Cuerpo | Respuesta |
|---|---|---|
| `POST /api/sesiones/{id}/mensajes` | `{"texto": "...", "documento_id": null}` | `202 {"turno_id"}` |
| `POST /api/sesiones/{id}/audio` | multipart `audio` (webm/ogg/wav) | `202 {"turno_id"}`; emite `transcripcion` y sigue como un turno de voz |
| `POST /api/aprobaciones/{aprobacion_id}` | `{"aprobar": true, "comentario": ""}` | `200 {"ok": true}` |
| `POST /api/sesiones/{id}/cancelar` | — | `200 {"ok": true}` |
| `POST /api/sesiones/{id}/preferencias` | `{"hablar": false}` | `200 {"ok": true}` |
| `POST /api/documentos` | multipart `archivo` (png/jpg/pdf) | `200 {"documento_id", "nombre"}` |
| `GET /api/documentos` | — | `[{documento_id, nombre}]` (incluye los de `data/documentos_demo/`) |
| `GET /api/documentos/{documento_id}` | — | el archivo, para el visor |
| `GET /api/audio/{id}` | — | `audio/wav` |
| `GET /api/sesiones/{id}/historial` | — | mensajes de la conversación (sin el prompt de sistema) |
| `GET /api/auditoria?limite=20` | — | últimas filas de `auditoria` (parámetros ya enmascarados por el servidor MCP) |
| `GET /api/salud` | — | `{ollama, mcp, voz, vision, modelos, fecha_demo, precalentado}` (`precalentado`: estado de la carga de STT y TTS) |
| `POST /api/precalentar` | — | `200 {"stt": "ok · mlx · 900 ms", "tts": "…"}`: vuelve a cargar Whisper y Kokoro en el orquestador (`make warmup`, Fase 6) |
| `GET /api/guion` | — | preguntas del guion para los botones de atajo (`id`, `pregunta`, `documento_id` opcional en el acto de visión) |

Un turno con documento: la UI sube la imagen (`POST /api/documentos`) o elige una de la lista, y luego envía un
`mensaje` con `documento_id` (el texto puede ir vacío; se usa «Valide este documento contra la base»).

## 5. Puntos de enganche (para las fases 3 y 4)

Están en `orchestrator/ganchos.py` como `Protocol`. El orquestador trae implementaciones «no disponible» y las
reemplaza por las reales si existen los módulos indicados. Cada fase entrega su módulo; nadie edita el agente.

| Gancho | Módulo que lo implementa | Fábrica que se busca | Firma |
|---|---|---|---|
| Visión | `orchestrator/vision.py` (Fase 3) | `crear_vision(cfg, ollama)` | `async extraer(imagen: bytes, nombre: str) -> Extraccion` |
| STT | `orchestrator/voz.py` (Fase 4) | `crear_transcriptor(cfg)` | `async transcribir(audio: bytes, formato: str) -> Transcripcion` |
| TTS | `orchestrator/voz.py` (Fase 4) | `crear_sintetizador(cfg)` | `async sintetizar(texto: str) -> Audio` |

- `Extraccion`: `tipo_documento`, `campos` (dict ya validado con Pydantic), `modelo`, `advertencias`.
  El orquestador emite `documento.extraido` y pasa los campos al agente para que los cruce con la base vía MCP.
- `Transcripcion`: `texto`, `modelo`, `idioma`.
- `Audio`: `datos` (bytes WAV), `formato`, `motor`. El orquestador guarda el audio, emite `voz.audio` con su
  `url` y mide la etapa `tts`.
- `ollama` es el cliente del orquestador (`orchestrator/ollama.py`):
  `await ollama.chat(rol, mensajes, imagenes=[bytes | base64], formato=esquema, opciones={...})` devuelve un
  `RespuestaLLM` (`.contenido`, `.tokens_entrada`, …) y ya registra el modelo usado y los tokens; la visión lo usa
  para que el chip y el panel salgan solos. Las imágenes van al último mensaje con `role: user`.
- Las fábricas pueden lanzar cualquier excepción (p. ej. `VozNoDisponible`): el componente queda «no disponible».
  En ejecución, un error de STT da `voz_no_disponible`; uno de visión, `documento_ilegible` (si es `ErrorExtraccion`,
  con su mensaje); uno de TTS, un `aviso` y la respuesta sigue en pantalla.
- **Qué se habla** (`voz.modo_habla` en `config.yaml`):
  - `primeras_oraciones` (recomendado por la Fase 4): si el sintetizador tiene `locutor()`, el orquestador lo
    alimenta con cada `llm.delta`, emite un `voz.audio` por cada fragmento en cuanto está listo y no pide
    `resumen_hablado` (llega `null`). La primera palabra suena antes de que termine la respuesta escrita.
  - `resumen`: el modelo rápido genera `resumen_hablado` al final y se sintetiza oración por oración.

## 6. Panel de soberanía (Fase 5)

Los datos por turno y acumulados del panel salen de `turno.fin`, `llm.fin` y `sesion.acumulado`. Los modelos
cargados (`/api/ps` de Ollama), la memoria y el monitor de conexiones salientes son de la Fase 5: puede agregar
sus rutas bajo `/api/soberania/*` y su propio evento `soberania.estado` sin cambiar este contrato.
