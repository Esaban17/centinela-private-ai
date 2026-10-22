# UI y Panel de Soberanía (Fase 5)

Una sola página en `web/`, sin build, sin CDN y sin fuentes web: HTML, CSS y módulos JS servidos por el
orquestador desde el mismo origen. La página trae una política CSP (`default-src 'self'`) que hace que el
navegador **rechace** cualquier recurso externo, así que no puede colarse un script, una fuente o un ícono
remoto aunque alguien lo agregue por error. La prueba `tests/test_ui.py` revisa además que ningún archivo
de `web/` apunte a una URL externa.

## Tres zonas

| Zona | Qué muestra | De dónde sale |
|---|---|---|
| Conversación | Chat con streaming, transcripción de voz, chips de modelo por paso, tarjetas de herramientas plegables (parámetros y resultado MCP), tarjetas de aprobación, citas RAG | Eventos del contrato v1 (`docs/CONTRATO_EVENTOS.md`) |
| Visor | Documento con los campos extraídos y su cruce con la base; ficha del cliente; gráfica y tabla de anomalías; movimientos con anomalías en rojo; resultados SQL; ticket creado | `documento.extraido` y `herramienta.fin.resultado` (el `structuredContent` de MCP) |
| Panel de Soberanía | Conexiones salientes reales, red del equipo, costo marginal Q0.00 vs. costo equivalente, acumulado, modelos cargados y memoria, latencias del último turno | `GET /api/soberania` (cada 1.5 s) + eventos `turno.fin` y `sesion.acumulado` |

Entrada: texto (Enter envía), **push-to-talk** (mantener el botón «Hablar» o la barra espaciadora fuera del
cuadro de texto), botones de atajo del guion (teclas `1`–`6`), documento de muestra o subido, `Esc` detiene
el turno. El botón «Voz» silencia la respuesta hablada. La voz se reproduce con los `voz.audio` del
orquestador; no se usa `speechSynthesis` del navegador porque algunas voces de Chrome son remotas.

La sesión se guarda en `localStorage`: si se recarga la página en escena, la conversación se reconstruye
con el búfer del orquestador (`?desde_seq=0`). «Nueva sesión» empieza de cero.

## Monitor de soberanía (`orchestrator/soberania/`)

El orquestador lo monta con dos líneas:

```python
from orchestrator.soberania import crear_router
app.include_router(crear_router(cfg))
```

| Ruta | Uso |
|---|---|
| `GET /api/soberania?tokens_entrada=&tokens_salida=` | Instantánea completa del panel. La UI manda los tokens de `sesion.acumulado` para calcular el costo. |
| `GET /api/soberania/conexiones` | Solo el monitor de conexiones (lo usa `make check-offline`). Incluye `guardia_activa` y `bloqueadas`: intentos de salida que la guardia de salida frenó dentro del orquestador (Fase 6). |
| `POST /api/soberania/reiniciar` | Borra el historial de conexiones vistas antes de empezar. |

- **Conexiones salientes:** `psutil` revisa, PID por PID, las conexiones inet del orquestador, de todos sus
  hijos (el servidor MCP por stdio) y de los procesos cuyo nombre coincide con `soberania.procesos`
  (por defecto `ollama`, que también cubre la app de Ollama si está abierta). Cuenta toda dirección remota
  que no sea loopback, incluida la red local. Si aparece una, la tarjeta se pone roja con proceso y destino,
  y el panel recuerda el máximo de la sesión aunque la conexión ya se haya cerrado.
- **Red del equipo:** solo mira interfaces locales (activa, no virtual, con IP no link-local). No hace ping
  ni DNS, porque eso sería una conexión saliente.
- **Modelos cargados:** `GET /api/ps` de Ollama, sin proxy y solo si la URL es local.
- **Costo equivalente:** `costos.precio_entrada_millon` y `costos.precio_salida_millon` de `config.yaml`
  (precio de lista de Claude Opus 5.5, ver DECISIONES.md §7). Sin precios, el panel lo dice y no inventa cifras. Siempre rotulado
  «estimación referencial», junto a «Costo marginal local: Q0.00».

## Selector de modelos y alerta de RAM

La píldora **⚙ Modelos** de la barra muestra la memoria estimada de la selección frente a la RAM del equipo:
verde si cabe, ámbar si va justa (≥ 90 %) y roja si se pasa; en ámbar o rojo aparece además una franja de
alerta bajo la barra. Al hacer clic se abre el diálogo: perfil (`48gb`, `24gb` o personalizado), un modelo por
rol (los del catálogo y los ya descargados en Ollama) y una barra apilada con cada modelo, la reserva del
sistema y la marca de la RAM total, que se recalcula en cada cambio (`POST /api/modelos/estimar`). «Aplicar»
(`POST /api/modelos`) pide confirmación si la selección no cabe. Lógica en `comun/modelos.py` y
`orchestrator/modelos_api.py`; decisiones en DECISIONES.md §8.

## Simulador para ensayar la UI sin modelos

```bash
make seed
make ui-sim        # http://127.0.0.1:8766  (puerto en ui.puerto_simulador)
```

`scripts/simulador_ui.py` habla el contrato v1 en las mismas rutas que el orquestador. Las herramientas,
la aprobación humana (elicitation de MCP) y el Panel de Soberanía son **reales**; los modelos son un guion
por palabras clave con latencias y tokens inventados. No hay STT ni TTS: al soltar el botón de voz, la
«transcripción» es la siguiente pregunta del guion. Crear tickets en el simulador escribe en
`data/banco.db`; `make seed` la deja como nueva.

## Pendiente de medir en la Mac

- Legibilidad desde el fondo del salón con el proyector real (la tipografía escala con el ancho: 20 px a 1920).
- Permiso de micrófono en Safari/Chrome la primera vez (pedirlo antes de subir al escenario).
- Que el monitor vea Ollama: si Ollama corre como otro usuario (p. ej. servicio del sistema), macOS niega
  sus conexiones y el panel lo muestra como «sin permiso».
