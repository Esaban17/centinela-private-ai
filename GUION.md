# GUION — Centinela en escena

> **DEMO / DATOS FICTICIOS.** El Banco Atitlán no existe; clientes, cuentas y documentos son inventados.

Charla de 9 a 11 minutos. Mensaje central: **con modelos abiertos en infraestructura propia se construye un
asistente útil (voz, visión, base de datos, herramientas vía MCP) sin enviar datos a terceros ni pagar por token,
y lo que corre en esta laptop escala a un clúster on-prem.**

Las preguntas exactas están en `config.yaml → guion`: son los botones de atajo de la UI (teclas **1 a 5**) y lo que
corren `make warmup`, `make ensayo` y `make check-offline`. Si cambia una pregunta, cámbiela allí y vuelva a ensayar.

---

## 0. Antes de subir

### La semana anterior

| Qué | Cómo | Listo cuando |
|---|---|---|
| Elegir el modelo agente | `make medir MODELOS="qwen3.6:35b-a3b-q4_K_M gemma4:26b-a4b-it-qat" CORRIDAS=5` | ≥ 90 % por pregunta. Si `qwen3.6` no llega, cambie de modelo antes de alargar el prompt |
| Ensayo formal | `make ensayo` (10 corridas) | ✅ en los criterios; el detalle queda en `salida/ensayos/` |
| Ensayo con voz | `make ensayo VOZ=1 CORRIDAS=3` con `make demo` **apagado** | primer audio p50 ≤ 5 s (objetivo; anote lo medido en §6) |
| Elegir la voz | `make voces` y escuchar `salida/voces/index.html` | ✅ fijado en `em_alex` (es-419) |
| Precios del costo equivalente | `config.yaml → costos` (hoy Claude Opus 5.5: US$4 / US$20 por millón, DECISIONES.md §7) | ✅ el panel muestra un monto; revisar que el precio siga vigente |
| Memoria | `make warmup` → sección «Memoria (ollama ps)» | 4 modelos, 100 % en GPU, ≤ 35 GB. Si no: `make setup-mac LIMITE_GPU=1` o bajar `num_ctx` |
| Video de respaldo | §4 | archivo en el escritorio **y** en una memoria USB |
| MCP Inspector sin red | `make inspector` una vez con internet | queda en la caché de npm por si alguien pide verlo |

### El día, una hora antes (T-60)

1. Laptop **conectada a la corriente**. En una terminal aparte: `caffeinate -dims` (no se duerme ni apaga la pantalla).
2. Activar **No molestar**. Cerrar Slack, correo, Dropbox/iCloud, Docker y todo lo que no sea la demo.
   Cerrar la app de Ollama de la barra de menú si estuviera abierta (busca actualizaciones por internet).
3. Base limpia (borra los tickets de los ensayos): con `make demo` apagado, `make reset`.
4. **Terminal 1:** `make demo` → levanta Ollama si hace falta, el orquestador, y abre `http://127.0.0.1:8765`.
5. **Terminal 2:** `make warmup` → debe terminar en **✅ LISTO**. Lea los ⚠️: un modelo «en CPU» o «vence» se
   arregla ahora, no en escena.
6. **Apague el Wi-Fi** y corra `make check-offline ESTRICTO=1` → **✅ SIN SALIDAS A INTERNET**.
   Vuelva a encender el Wi-Fi solo si quiere apagarlo en vivo en el acto 1 (recomendado: es el momento clave).
7. **Terminal 3 (secundaria, a mano para preguntas):** `make logs`.
8. En el navegador: pantalla completa (⌃⌘F), zoom para el salón, permiso de micrófono concedido. Pruebe una vez
   la barra espaciadora (push-to-talk) y la tecla 5.
9. Pulse **Nueva sesión**: limpia la conversación, el acumulado y el historial del monitor de conexiones.

---

## 1. Los cinco actos

Tiempos orientativos. «Tecla» es el atajo del guion si la voz falla; el texto completo de cada pregunta está en
`config.yaml`.

### Acto 1 — «Esto no es la nube» (0:00 – 2:00)

| | |
|---|---|
| **Hago** | Señalo el Panel de Soberanía: modelos cargados, memoria, **Conexiones externas: 0**. **Apago el Wi-Fi en vivo** (Centro de control). El panel pasa a «Red: sin conexión ✈». |
| **Digo (voz)** | «Centinela, ¿cómo está la cartera de la clienta Lucía Fernanda Cojtí Barrios?» — tecla **1** |
| **Debe verse** | Chips `buscar_cliente` → `resumen_cliente` (CLI-0042), respuesta escrita y hablada, chips de modelo: Whisper, agente, rápido, Kokoro. |
| **Mensaje** | «Todo lo que acaban de ver corrió en esta laptop, sin red. Ningún dato del cliente salió.» |
| **Si falla** | Voz: tecla 1. Si el modelo no busca a la clienta, repita con la tecla (es probabilístico, igual que en la nube). |

### Acto 2 — Fraude y humano en el circuito (2:00 – 4:30)

| | |
|---|---|
| **Digo** | «¿Ve movimientos raros en sus cuentas este mes?» — tecla **2** |
| **Debe verse** | `detectar_anomalias`: 6 hallazgos (2 compras de madrugada en Panamá y una ráfaga de 4 transferencias ACH), cada uno con su motivo; el visor resalta las transacciones. |
| **Digo** | «Abra un ticket para el equipo de fraude con esos hallazgos.» — tecla **3** |
| **Debe verse** | Tarjeta **«Centinela quiere crear este ticket — Aprobar / Rechazar»** con el título que propuso el modelo rápido. Apruebo → «ticket creado y registrado en auditoría». |
| **Mensaje** | «La aprobación no es un botón de la pantalla: la exige el servidor MCP (elicitation). El modelo no puede aprobarse a sí mismo, y cualquier cliente MCP que use este servidor hereda la regla. Todo queda auditado.» |
| **Si falla** | Si no aparece la tarjeta, repita la tecla 3. Si la tarjeta vence (120 s), se rechaza sola: dígalo como parte del gobierno. |

### Acto 3 — Política interna con cita (4:30 – 5:30)

| | |
|---|---|
| **Digo** | «¿Qué dice nuestra política sobre bloqueo preventivo de tarjetas en este caso?» — tecla **4** |
| **Debe verse** | `buscar_politicas` y la respuesta con **documento + sección**; expando la cita. |
| **Mensaje** | «RAG local: los manuales del banco nunca salieron a un servicio de embeddings externo.» |
| **Si falla** | Si los embeddings no responden, la búsqueda sigue en modo léxico y cita igual (lo dice en el aviso). |

### Acto 4 — Visión documental (5:30 – 7:30)

| | |
|---|---|
| **Hago** | Tecla **5** (o menú Documento → `boleta_deposito_discrepancia.png`). |
| **Debe verse** | Chip del **modelo de visión**, el documento en el visor con los campos extraídos al lado, luego `validar_documento`: **«la cuenta existe y el depósito cuadra, pero el titular no coincide»**. |
| **Mensaje** | «Un modelo de 4B lee el documento; la validación contra la base es una herramienta MCP auditada, no la opinión del modelo.» |
| **Si falla** | Lectura mala: repita la tecla 5. Si la visión no responde, pase al acto 5 y vuelva al final si sobra tiempo. |

### Acto 5 — Cierre con el panel (7:30 – 9:30)

| | |
|---|---|
| **Señalo** | **0 conexiones externas** en toda la sesión · **Datos sensibles que no salieron del equipo**: el total y sus niveles (restringido, confidencial, interno), «con IA en la nube, todo esto habría salido del banco, y nuestra propia política lo prohíbe» · tokens procesados · **costo equivalente (estimación referencial) vs. Costo marginal local: Q0.00** · modelos distintos que colaboraron (agente, rápido, visión, embeddings, Whisper, Kokoro) · herramientas ejecutadas y auditadas. |
| **Diapositiva final** | De la laptop al clúster (tabla abajo). |

| Hoy, en esta laptop | En producción on-prem |
|---|---|
| Ollama (GGUF, Metal) | vLLM / NVIDIA NIM sobre GPU (NVIDIA AI Enterprise, Dell AI Factory) |
| Un proceso por componente | Contenedores en VKS sobre VCF |
| SQLite `banco.db` | La base del core o un réplica de lectura |
| Servidor MCP `banco-atitlan` | **El mismo**, con autenticación real |
| Monitor de soberanía con psutil | Políticas de red (NSX) y observabilidad del clúster |
| Aprobación por elicitation | La misma, integrada al flujo de aprobaciones del banco |

---

## 2. Plan B

| Si pasa esto | Hago esto |
|---|---|
| El micrófono o Whisper no responde | Teclas 1-5 o escribir la pregunta. El chat ya muestra el aviso amable. |
| Kokoro falla | Pasa solo al respaldo `say` (voz Paulina); el chip de TTS lo muestra. Si toda la voz falla: botón 🔇 y sigo con texto. |
| Una respuesta tarda más de ~20 s | **Esc** cancela; repito con la tecla. Ninguna etapa congela la pantalla: cada una tiene su timeout. |
| El agente no usa la herramienta o responde mal | Repito con la tecla. Si vuelve a fallar, salto al siguiente acto y lo menciono con naturalidad. |
| El panel marca una conexión saliente | Leo el detalle (proceso → IP). Si es Ollama, casi siempre es la app de la barra: por eso la demo usa `ollama serve`. Lo ideal es haberlo visto en `make check-offline`. |
| Se cae Ollama | Terminal 2: `make ollama &` y luego `make warmup SIN_GUION=1` (recargar ~30 GB tarda): mientras, paso al video. |
| El navegador se cuelga | Recargar (⌘R): el id de la sesión queda guardado en el navegador y la conversación sigue en el orquestador. |
| Nada funciona | **Video de respaldo** (§4): «lo grabé en esta misma laptop, sin red, ayer». |

## 3. Si preguntan «¿qué está pasando por dentro?»

- **Terminal 3** (`make logs`): cada llamada al modelo, cada herramienta y cada aprobación, con las cuentas
  enmascaradas (`AT-•••42-01`).
- **Auditoría**: `sqlite3 data/banco.db "SELECT fecha, herramienta, estado, modelo FROM auditoria ORDER BY id DESC LIMIT 8"`.
- **El estándar es abierto**: `make inspector` abre MCP Inspector contra el mismo servidor (requiere haberlo
  corrido una vez con red). Cualquier cliente MCP lo puede usar.
- **API**: `http://127.0.0.1:8765/api/docs`.
- **Guardia de salida**: además de medir, los procesos de la demo *bloquean* cualquier conexión fuera del equipo;
  si alguna librería lo intentara, quedaría en el log como «CONEXIÓN SALIENTE BLOQUEADA».

## 4. Video de respaldo

Grábelo con la configuración final, en la misma laptop y **con el Wi-Fi apagado**, después de `make warmup`.

1. Audio: `⌘⇧5` y QuickTime graban la pantalla pero **no** el audio del sistema, y la voz de Centinela es parte de
   la demo. Use **OBS Studio** (`brew install --cask obs`, instalar con red): fuente «Captura de pantalla de macOS»
   con audio de la aplicación (macOS 13 o superior) y su micrófono como segunda fuente para narrar. Haga una prueba
   de 10 segundos y escúchela antes de grabar todo.
2. 1080p, 30 fps. Pantalla completa del navegador, con el panel visible.
3. Siga los 5 actos con la voz (no con las teclas), incluido apagar el Wi-Fi al inicio.
4. Recorte los silencios largos. Copie el archivo al escritorio y a una memoria USB.
5. En escena, tenga el video abierto y en pausa en otro escritorio (Mission Control) para cambiar en 2 segundos.

## 5. Criterios de aceptación y cómo se verifican

| Criterio (prompt §11) | Verificación |
|---|---|
| Con el Wi-Fi apagado se completan los 5 actos | `make check-offline ESTRICTO=1` (corre el guion completo sin red) y el ensayo general |
| 0 conexiones salientes durante toda la demo | Panel (máximo de la sesión), `make check-offline` y `make ensayo` (monitor cada segundo + guardia de salida) |
| ≥ 90 % de éxito en 10 corridas | `make ensayo` → criterio «éxito ≥ 90 % en cada pregunta» |
| Primera palabra hablada ≤ 5 s | `make ensayo VOZ=1` → columna «1.er audio» (objetivo: se reporta lo medido) |
| Ninguna escritura sin aprobación; toda llamada en `auditoria` | Pruebas `tests/test_agente.py` y `tests/test_herramientas.py`; `make ensayo` revisa la auditoría en cada corrida |
| Instalación desde cero siguiendo el README | `./scripts/setup_mac.sh` en una Mac limpia |

## 6. Mediciones en la Mac (completar)

| Fecha | Modelo agente | `make ensayo` (éxito) | p50 / p95 por turno | Primer audio p50 | Memoria `ollama ps` | Notas |
|---|---|---|---|---|---|---|
| 2026-10-16 | qwen3.6:35b-a3b-q4_K_M | Texto: 60/60 (100 %) · 0 conexiones salientes | p50 6.2–9.0 s (documento 11.9 s) / p95 7.2–13.0 s (documento 14.0 s) · 1.er token p50 2.1–6.1 s · 55 tok/s | — | 28.1 GB: agente 21.4 + rápido 2.7 + visión 3.4 + embeddings 0.6, todos sin vencimiento | M5 Pro 48 GB, `make ollama`. `make warmup` LISTO, `make medir` 18/18 |
| 2026-10-16 | qwen3.6:35b-a3b-q4_K_M | Voz (`VOZ=1`): 60/60 (100 %) · 0 conexiones salientes | p50 7.4–12.1 s (documento 12.4 s) / p95 9.8–12.8 s (documento 15.7 s) · STT p50 0.5–0.7 s · 50 tok/s | 6.4 s (noc 3.6 s, cartera 10.7 s; peor p95 11.3 s): no cumple el objetivo de 5 s | igual | Con PR #9 (telemetría de ONNX Runtime) y PR #10 (regla del prompt). Sin ellos: 1 conexión saliente a Microsoft y fraude 7/10. Falta `check-offline ESTRICTO=1` |
