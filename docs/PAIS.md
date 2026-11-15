# Centinela sobre VMware Private AI Services (PAIS 3.0)

> **DEMO / DATOS FICTICIOS.** Banco Demo GQ no existe.

La misma app (UI, voz, panel de soberanía, aprobación humana, auditoría), pero los modelos ya no corren en Ollama
en la Mac: corren en **PAIS 3.0 sobre VCF 9.1.1**, dentro del datacenter (GQ LAB). Los datos del banco vienen del
**MCP demo-banco** (Postgres en pais-deps) y las políticas son las mismas de la base de conocimientos
`politicas-banco` de PAIS.

```
Navegador ─► Centinela (Mac o pais-deps:8300)
               ├─ modo «agente Centinela» ─► PAIS /v1/chat/completions  (llama-3.1-8b, con tools)
               │     ├─ MCP demo-banco  http://10.2.15.51:8101/mcp   (evaluar_solicitud_credito, perfil_crediticio, …)
               │     └─ MCP políticas   stdio local, RAG híbrido con  PAIS /v1/embeddings (bge-m3)
               └─ modo «agente PAIS»    ─► PAIS /agents/{agente-banco}/chat/completions  (MCP + RAG dentro de PAIS)
             token OIDC de Keycloak (usuario de servicio), solo en el backend
```

## Qué reemplaza a qué

| Centinela local (Ollama) | Centinela sobre PAIS |
|---|---|
| `qwen3.6:35b` (agente) y `qwen3:4b` (rápido) en Ollama | `llama-3.1-8b` (ModelEndpoint `llama31-8b-cpu`, llama.cpp en CPU) para los dos roles |
| `bge-m3` en Ollama | `bge-m3` (ModelEndpoint `bge-m3-cpu`, Infinity en CPU) |
| `qwen3-vl` (visión) | sin visión: no hay GPU en el lab (`vision.habilitada: false`) |
| MCP `banco-atitlan` (SQLite, 9 herramientas) | MCP `demo-banco` (Postgres, 5 herramientas) + `buscar_politicas` local |
| Políticas del Banco Atitlán | POL-CR-001, POL-CR-002, NR-005 y GE-010 (`data/politicas_pais/`) |
| Guardia: nada sale del equipo | Guardia: nada sale del datacenter (`soberania.destinos_permitidos`) |
| Whisper + Kokoro en la Mac | igual en la Mac; en pais-deps, faster-whisper `small` + Kokoro en CPU |

Todo se activa con `CENTINELA_PROVEEDOR=pais` (en `.env`), que aplica [config.pais.yaml](../config.pais.yaml) encima
de `config.yaml`. Sin esa variable, Centinela sigue siendo la demo 100 % local de siempre.

## Los dos modos (botón «🧠 Agente» en la conversación)

- **Agente Centinela** (por defecto): el loop de Centinela con los modelos de PAIS. Se ven las tarjetas de cada
  herramienta, los chips de modelo, las citas de políticas y la auditoría. Es lo que un banco construye *encima* de
  PAIS usando solo la API OpenAI-compatible.
- **Agente PAIS**: el turno completo lo resuelve `agente-banco` del Agent Builder. Se ve solo el chip del agente y la
  respuesta (la API de agentes no informa qué herramientas usó). Sirve para comparar «agente propio» contra
  «agente administrado por PAIS», y de respaldo si el 8B falla con las herramientas en el modo propio.

## Prerrequisitos en el lab

1. ModelEndpoints `llama31-8b-cpu` (routingName `llama-3.1-8b`, con `--jinja` para tool calling) y `bge-m3-cpu`
   en estado Ready (`pais-3.0/03-…` y `04-…`).
2. MCP demo-banco corriendo en pais-deps:8101 (mcp-demo v4) y su `MCP_API_KEY`.
3. `agente-banco` creado (solo para el modo agente PAIS). Su ID va en `pais.agente.id`.
4. Usuario de servicio en Keycloak (`https://keycloak.vcflab.local:8443`, realm `lab`):
   - **Clients › pais › Settings › Capability config**: activar **Direct access grants**.
   - **Users › Create user** `app-centinela` con correo, nombre y apellido, *Email verified* activado (si no,
     Keycloak responde «Account is not fully set up») y **Join groups** `pais-users`.
   - **Credentials › Set password**, *Temporary* apagado.
   (Puede reutilizar `app-banca` si ya existe: cambie `PAIS_KC_USUARIO`.)

## Opción A: en la Mac (recomendada para la demo con voz)

La voz (Whisper MLX y Kokoro) corre en la Mac, como siempre; solo los LLM y los embeddings se van a PAIS.

```bash
cp .env.pais.example .env && chmod 600 .env    # llenar PAIS_KC_CLAVE y PAIS_MCP_API_KEY
make setup && make seed
make pais-probar        # diagnóstico paso a paso (ver abajo)
make pais-ingest        # políticas del Banco Demo GQ con bge-m3 de PAIS
make pais-run           # UI en http://127.0.0.1:8765
make pais-cli PAIS=1    # o en la terminal, delegando a agente-banco
```

La Mac necesita resolver `pais.vcflab.local` (10.2.20.10) y `keycloak.vcflab.local` (10.2.15.51) en `/etc/hosts`,
y Zscaler debe dejar pasar 10.2.15.0/24 y 10.2.20.0/24.

Si `make pais-probar` dice que el MCP demo-banco falla con «Connection reset by peer» (Zscaler corta el HTTP en claro
hacia el puerto 8101, aunque `nc` conecte), use un túnel ssh en otra terminal y apunte Centinela a él:

```bash
ssh -N -L 8101:127.0.0.1:8101 pais@10.2.15.51
echo "PAIS_MCP_URL=http://127.0.0.1:8101/mcp" >> .env
```

## Opción B: contenedor en pais-deps

```bash
scp -r centinela-private-ai pais@10.2.15.51:        # o git clone en la VM
ssh -t pais@10.2.15.51 'cd centinela-private-ai && bash despliegue/pais-deps/setup-centinela-pais.sh'
```

El script pide la clave del usuario de servicio (la del MCP la toma de `~/mcp-demo/.env` si existe), construye la
imagen, corre el diagnóstico, descarga la voz una vez y deja Centinela en `http://10.2.15.51:8300`.
El micrófono del navegador solo funciona con HTTPS o localhost, así que para hablar use un túnel desde la Mac:
`ssh -N -L 8300:127.0.0.1:8300 pais@10.2.15.51` y abra `http://127.0.0.1:8300`. `VOZ=0` construye la imagen sin voz.

## Diagnóstico: `make pais-probar`

Revisa token, `/models`, un chat simple, **tool calling** (con y sin streaming), embeddings, los dos servidores
MCP (con el caso CLI-00119) y el agente de PAIS. Si «Herramientas» sale en amarillo, PAIS no está pasando `tools`
al modelo: pruebe `CENTINELA__pais__stream=false`; si sigue igual, revise que el ModelEndpoint tenga `--jinja` o use
el modo agente PAIS.

## Guion sugerido (botones 1-5)

| Pregunta | Esperado |
|---|---|
| ¿El cliente CLI-00119 califica para un préstamo personal de Q50,000? | SUSPENDIDO por alerta de fraude (NR-005), aunque el score sea 800 |
| ¿El cliente CLI-00142 califica para un préstamo de vehículo de Q300,000? | APROBADO CON CONDICIONES, Q200,000 (uso de tarjetas > 90 %) |
| ¿Qué dice la política sobre clientes con alertas de fraude? | Cita NR-005 sección 1 |
| ¿Cómo está la mora de la cartera? | resumen_cartera_mora |
| ¿Qué alertas de fraude están abiertas? | alertas_fraude_abiertas |

Haga la misma pregunta en los dos modos para comparar tiempos y respuestas.

## Ajustes para Llama 3.1 8B en CPU

- Una sola llamada al modelo a la vez (`--parallel 1`): `config.pais.yaml` apaga el clasificador de intención, el
  titulador y el resumen hablado (cada uno sería otra llamada en cola). El modo agente PAIS tampoco clasifica.
- Pocas herramientas (5 + `buscar_politicas`): con 12, el 8B daba «Parsing inference server response failed».
- El dictamen de crédito lo decide `evaluar_solicitud_credito` (reglas deterministas); el modelo solo lo comunica.
- `memoria_mensajes: 8` y resultados de herramienta de hasta 3,000 caracteres: el contexto del endpoint es 8192.
- Timeouts de 300 s: en CPU una respuesta con herramientas puede tardar uno o dos minutos.

## Seguridad

- Las credenciales viven solo en `.env` (600) y en el proceso del orquestador; el navegador nunca ve el token.
- La guardia de salida permite solo `soberania.destinos_permitidos` (redes del lab) y los hosts de `pais` y
  `mcp.servidores`; cualquier otro destino (Hugging Face, una API pública) se bloquea y aparece en el panel.
- El panel cuenta aparte las conexiones al datacenter («datacenter») de las salientes a internet.
- `pais.verificar_tls: false` porque el certificado del lab no es de una CA pública; en producción use `pais.ca`
  con el bundle de la CA del banco.
