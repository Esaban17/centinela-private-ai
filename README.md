# Centinela — asistente de operaciones con IA privada

> **DEMO / DATOS FICTICIOS.** El Banco Atitlán no existe. Todos los clientes, cuentas, documentos y
> alertas son inventados.

Demo 100 % local: modelos abiertos en Ollama, un servidor MCP propio sobre SQLite, RAG local, visión,
voz y un panel de soberanía que demuestra cero conexiones a internet.

Las decisiones de modelos, el presupuesto de memoria y los ajustes a la arquitectura están en
[docs/DECISIONES.md](docs/DECISIONES.md).

## Estado por fases

| Fase | Estado |
|---|---|
| 1. Datos + MCP | ✅ seed determinista, servidor `banco-atitlan` con 9 herramientas, 2 recursos, 2 prompts y pruebas |
| 2. Agente en texto | ✅ orquestador FastAPI (host MCP por stdio), loop del agente, enrutamiento multi-modelo, aprobación humana, eventos por WebSocket/SSE ([contrato](docs/CONTRATO_EVENTOS.md)) |
| 3. RAG + visión | pendiente (`buscar_politicas` responde "índice no construido" sin inventar) |
| 4. Voz | pendiente |
| 5. UI + panel de soberanía | pendiente |
| 6. Endurecimiento y ensayo | pendiente |

## Requisitos (Mac Apple Silicon)

```bash
brew install uv ollama ffmpeg node   # node solo para MCP Inspector durante el desarrollo
```

## Instalación y uso — Fase 1

```bash
make setup     # crea .venv con Python 3.12 e instala dependencias
make seed      # genera data/banco.db (misma semilla + misma fecha ⇒ misma base)
make test      # pruebas
make inspector # abre MCP Inspector contra el servidor
```

`make seed` usa la fecha de hoy como "ahora" de la demo. Para fijarla:
`CENTINELA__seed__fecha_referencia=2026-11-20 make seed`.

### Probar con MCP Inspector

`make inspector` abre la UI de Inspector con el comando `.venv/bin/python -m mcp_server` (transporte stdio).
Pruebas sugeridas:

1. `buscar_cliente` con `texto = lucia cojti` → `CLI-0042`.
2. `detectar_anomalias` con `cliente_id = CLI-0042` → 6 hallazgos: 2 compras de madrugada en Panamá y una
   ráfaga de 4 transferencias ACH, cada uno con sus motivos.
3. `consulta_sql` con `DELETE FROM clientes` → rechazada (y registrada en `auditoria`).
4. `alertas_infraestructura` con `severidad = critica` → clúster VKS y NSX Edge.
5. `crear_ticket` → Inspector muestra el formulario de aprobación (elicitation). Aprobar o rechazar.
6. Recurso `esquema://banco`.

En modo línea de comandos (sin UI, sin elicitation):

```bash
npx -y @modelcontextprotocol/inspector@2.11.0 --cli .venv/bin/python -m mcp_server --method tools/list
```

### Usarlo desde otro cliente MCP

Cualquier cliente que lance servidores por stdio sirve. Ejemplo de configuración:

```json
{
  "mcpServers": {
    "banco-atitlan": {
      "command": "/ruta/a/centinela/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "cwd": "/ruta/a/centinela"
    }
  }
}
```

## Orquestador — Fase 2

```bash
ollama serve                 # en otra terminal (como servicio, no la app de la barra de menú)
make run                     # API en http://127.0.0.1:8765 · /api/docs · /api/salud
make cli                     # o conversar en la terminal, sin UI
make medir MODELOS="qwen3.6:35b-a3b-q4_K_M gemma4:26b-a4b-it-qat" CORRIDAS=5
```

- El orquestador arranca el servidor MCP como proceso hijo por stdio, descubre sus herramientas y las pasa a
  Ollama como funciones. Lo que el servidor exponga, el agente lo puede usar.
- **Varios modelos, cada uno para su tarea:** el agente razona con herramientas; el modelo rápido clasifica la
  intención (en paralelo, sin sumar latencia), titula los tickets y resume la respuesta para la voz; la visión
  lee documentos. Cada uso emite un «chip» (`modelo.uso`) con el modelo y la tarea.
- **Humano en el circuito:** `crear_ticket` y `actualizar_ticket` piden aprobación desde el servidor MCP
  (elicitation). El orquestador la convierte en la tarjeta `aprobacion.solicitada` y espera Aprobar/Rechazar;
  si nadie responde en `orquestador.timeouts_s.aprobacion`, se rechaza.
- **Auditoría:** cada llamada va con `usuario` y `modelo` en `_meta`; el servidor la registra. `GET /api/auditoria`.
- **Logs** en la terminal con las cuentas enmascaradas (`AT-•••42-01`). `CENTINELA_LOG=INFO` muestra cada paso.
- **Memoria por sesión** (últimos `orquestador.memoria_mensajes` mensajes) y **timeouts por etapa** con mensaje
  amable: un turno siempre termina con `turno.fin`, aunque falle Ollama o el servidor MCP.
- `make medir` corre las preguntas del guion (`config.yaml → guion`) contra cada modelo sobre una copia de la
  base y reporta éxito de tool calling y latencias p50/p95. Sirve para elegir el agente en la Mac.

## Servidor MCP `banco-atitlan`

| Herramienta | Tipo | Notas |
|---|---|---|
| `buscar_cliente(texto)` | lectura | Nombre parcial sin importar tildes, o id (`CLI-0042`, `42`) |
| `resumen_cliente(cliente_id)` | lectura | Productos, saldos, riesgo, ejecutivo, tickets abiertos |
| `movimientos(cuenta_id, desde?, hasta?, tipo?)` | lectura | Últimos 30 días por defecto; límite de filas |
| `detectar_anomalias(cliente_id, dias=30)` | lectura | z-score, madrugada, ubicación nueva, ráfagas; cada hallazgo trae su motivo |
| `consulta_sql(sql)` | lectura | Un solo SELECT (sqlglot), LIMIT forzado, conexión `mode=ro` + autorizador de SQLite |
| `buscar_politicas(consulta, k=4)` | lectura | RAG (Fase 3) |
| `alertas_infraestructura(severidad?, horas=24)` | lectura | Alertas del NOC |
| `crear_ticket(...)` | **escritura** | Pide aprobación humana por elicitation |
| `actualizar_ticket(id, estado, nota)` | **escritura** | Pide aprobación humana por elicitation |

Toda llamada queda en la tabla `auditoria` (usuario, herramienta, parámetros con cuentas enmascaradas,
resumen, modelo y estado `ok` / `error` / `rechazado`). El orquestador envía `usuario` y `modelo` en el
`_meta` de cada llamada.

## Configuración

`config.yaml` define modelos, rutas, límites y reglas de anomalías. Cualquier clave se sobreescribe por
entorno: `CENTINELA__<seccion>__<clave>` (ver `.env.example`). No hay nombres de modelo, rutas ni puertos
fijos en el código.

## Estructura

```
centinela/
├── config.yaml / .env.example / Makefile / pyproject.toml
├── comun/          # configuración y enmascarado compartidos
├── scripts/        # seed.py, pull_models.sh
├── mcp_server/     # servidor MCP banco-atitlan
├── orchestrator/   # FastAPI, host MCP, agente, cliente de Ollama, ganchos de visión y voz
├── web/            # Fase 5
├── data/           # banco.db (generada), politicas/, documentos_demo/, vectores/
├── docs/           # DECISIONES.md, CONTRATO_EVENTOS.md
└── tests/
```
