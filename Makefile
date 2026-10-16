# Centinela — DEMO / DATOS FICTICIOS
PY      ?= .venv/bin/python
OFFLINE := HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 ANONYMIZED_TELEMETRY=False DO_NOT_TRACK=1
INSPECTOR_VERSION ?= 2.11.0

.PHONY: help setup setup-mac seed ingest documentos vision mcp inspector test pull-models ui-sim voz-modelos voces voz-prueba \
        ollama demo run cli logs warmup ensayo medir check-offline reset

help:
	@echo "── El día de la demo (ver GUION.md) ──"
	@echo "make demo         Levanta todo: Ollama si hace falta, el orquestador y la UI (http://127.0.0.1:8765)"
	@echo "make warmup       Carga los modelos sin vencimiento, corre el guion una vez y precalienta la voz"
	@echo "make check-offline  Sin red y 0 conexiones salientes (ESTRICTO=1 exige el Wi-Fi apagado)"
	@echo "make ensayo       Corre el guion N veces y revisa los criterios (CORRIDAS=10 VOZ=1)"
	@echo "make logs         Sigue el log del orquestador (terminal secundaria)"
	@echo "make reset        Base y documentos demo como nuevos (borra los tickets de los ensayos)"
	@echo "make ollama       ollama serve con las variables de config.yaml (solo 127.0.0.1, sin vencimiento)"
	@echo "── Instalación y desarrollo ──"
	@echo "make setup-mac    Instalación desde cero en una Mac (requiere internet una vez; LIMITE_GPU=1 sube el límite de GPU)"
	@echo "make setup        Crea .venv (Python 3.12) e instala dependencias"
	@echo "make seed         Genera data/banco.db (determinista)"
	@echo "make ingest       Indexa data/politicas/ (embeddings locales + sqlite-vec + FTS5)"
	@echo "make documentos   Genera los documentos demo (PNG) desde banco.db"
	@echo "make vision       Lee los documentos demo con el modelo de visión y los valida"
	@echo "make mcp          Corre el servidor MCP banco-atitlan por stdio"
	@echo "make inspector    Abre MCP Inspector contra el servidor (requiere Node; solo durante el desarrollo)"
	@echo "make test         Corre las pruebas"
	@echo "make pull-models  Descarga los modelos de config.yaml en Ollama (requiere internet, una vez)"
	@echo "make voz-modelos  Descarga Whisper y Kokoro a ./modelos (requiere internet, una vez; FASTER=1 agrega faster-whisper)"
	@echo "make voces        Genera muestras de cada voz en salida/voces/ para escucharlas y elegir"
	@echo "make voz-prueba   TTS → STT de una frase del guion y latencia del primer audio en streaming"
	@echo "make run          Levanta el orquestador en http://127.0.0.1:8765 (arranca el servidor MCP solo)"
	@echo "make cli          Conversa con Centinela en la terminal (aprobaciones con s/n)"
	@echo "make medir        Compara modelos agente con el guion (MODELOS=\"a b\" CORRIDAS=5)"
	@echo "make ui-sim       UI + Panel de Soberanía contra el simulador de eventos (sin modelos; requiere seed)"

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements.txt

seed:
	$(OFFLINE) $(PY) -m scripts.seed

ingest:
	$(OFFLINE) $(PY) -m scripts.ingest

documentos:
	$(OFFLINE) $(PY) -m scripts.gen_documentos

vision:
	$(OFFLINE) $(PY) -m documentos --todos

mcp:
	$(OFFLINE) $(PY) -m mcp_server

inspector:
	npx -y @modelcontextprotocol/inspector@$(INSPECTOR_VERSION) $(PY) -m mcp_server

test:
	$(OFFLINE) $(PY) -m pytest -q

pull-models:
	./scripts/pull_models.sh

ui-sim:
	$(OFFLINE) $(PY) -m scripts.simulador_ui

voz-modelos:
	$(PY) -m scripts.descargar_modelos_voz $(if $(FASTER),--faster,)

voces:
	$(OFFLINE) $(PY) -m scripts.comparar_voces

voz-prueba:
	$(OFFLINE) $(PY) -m scripts.probar_voz
	$(OFFLINE) $(PY) -m scripts.probar_voz --streaming

run:
	$(OFFLINE) $(PY) -m orchestrator

cli:
	$(OFFLINE) $(PY) -m orchestrator.cli

medir:
	$(OFFLINE) $(PY) -m scripts.medir_agente --corridas $(or $(CORRIDAS),3) $(if $(MODELOS),--modelos $(MODELOS),)

setup-mac:
	./scripts/setup_mac.sh $(if $(LIMITE_GPU),--limite-gpu,)

ollama:
	PY=$(PY) ./scripts/ollama_serve.sh

demo:
	PY=$(PY) ./scripts/demo.sh

logs:
	@mkdir -p salida && touch salida/centinela.log && tail -n 50 -F salida/centinela.log

warmup:
	$(OFFLINE) $(PY) -m scripts.warmup $(if $(SIN_GUION),--sin-guion,)

ensayo:
	$(OFFLINE) $(PY) -m scripts.ensayo $(if $(CORRIDAS),--corridas $(CORRIDAS),) $(if $(VOZ),--voz,)

check-offline:
	$(OFFLINE) $(PY) -m scripts.check_offline $(if $(ESTRICTO),--estricto,)

reset: seed documentos
