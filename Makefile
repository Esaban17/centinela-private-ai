# Centinela — DEMO / DATOS FICTICIOS
PY      ?= .venv/bin/python
OFFLINE := HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 ANONYMIZED_TELEMETRY=False DO_NOT_TRACK=1
INSPECTOR_VERSION ?= 2.11.0

.PHONY: help setup seed mcp inspector test pull-models voz-modelos voces voz-prueba warmup run ensayo check-offline

help:
	@echo "make setup        Crea .venv (Python 3.12) e instala dependencias"
	@echo "make seed         Genera data/banco.db (determinista)"
	@echo "make mcp          Corre el servidor MCP banco-atitlan por stdio"
	@echo "make inspector    Abre MCP Inspector contra el servidor (requiere Node; solo durante el desarrollo)"
	@echo "make test         Corre las pruebas"
	@echo "make pull-models  Descarga los modelos de config.yaml en Ollama (requiere internet, una vez)"
	@echo "make voz-modelos  Descarga Whisper y Kokoro a ./modelos (requiere internet, una vez; FASTER=1 agrega faster-whisper)"
	@echo "make voces        Genera muestras de cada voz en salida/voces/ para escucharlas y elegir"
	@echo "make voz-prueba   TTS → STT de una frase del guion y latencia del primer audio en streaming"

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements.txt

seed:
	$(OFFLINE) $(PY) -m scripts.seed

mcp:
	$(OFFLINE) $(PY) -m mcp_server

inspector:
	npx -y @modelcontextprotocol/inspector@$(INSPECTOR_VERSION) $(PY) -m mcp_server

test:
	$(OFFLINE) $(PY) -m pytest -q

pull-models:
	./scripts/pull_models.sh

voz-modelos:
	$(PY) -m scripts.descargar_modelos_voz $(if $(FASTER),--faster,)

voces:
	$(OFFLINE) $(PY) -m scripts.comparar_voces

voz-prueba:
	$(OFFLINE) $(PY) -m scripts.probar_voz
	$(OFFLINE) $(PY) -m scripts.probar_voz --streaming

warmup run ensayo check-offline:
	@echo "«$@» llega en las fases 2-6 (ver README)."
