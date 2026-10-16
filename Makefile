# Centinela — DEMO / DATOS FICTICIOS
PY      ?= .venv/bin/python
OFFLINE := HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 ANONYMIZED_TELEMETRY=False DO_NOT_TRACK=1
INSPECTOR_VERSION ?= 2.11.0

.PHONY: help setup seed ingest documentos vision mcp inspector test pull-models ui-sim run cli medir warmup ensayo check-offline

help:
	@echo "make setup        Crea .venv (Python 3.12) e instala dependencias"
	@echo "make seed         Genera data/banco.db (determinista)"
	@echo "make ingest       Indexa data/politicas/ (embeddings locales + sqlite-vec + FTS5)"
	@echo "make documentos   Genera los documentos demo (PNG) desde banco.db"
	@echo "make vision       Lee los documentos demo con el modelo de visión y los valida"
	@echo "make mcp          Corre el servidor MCP banco-atitlan por stdio"
	@echo "make inspector    Abre MCP Inspector contra el servidor (requiere Node; solo durante el desarrollo)"
	@echo "make test         Corre las pruebas"
	@echo "make pull-models  Descarga los modelos de config.yaml en Ollama (requiere internet, una vez)"
	@echo "make run          Levanta el orquestador en http://127.0.0.1:8765 (arranca el servidor MCP solo)"
	@echo "make cli          Conversa con Centinela en la terminal (aprobaciones con s/n)"
	@echo "make medir        Mide el modelo agente con el guion (MODELOS=\"a b\" CORRIDAS=5)"
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

run:
	$(OFFLINE) $(PY) -m orchestrator

cli:
	$(OFFLINE) $(PY) -m orchestrator.cli

medir:
	$(OFFLINE) $(PY) -m scripts.medir_agente --corridas $(or $(CORRIDAS),3) $(if $(MODELOS),--modelos $(MODELOS),)

warmup ensayo check-offline:
	@echo "«$@» llega en las fases 5-6 (ver README)."
