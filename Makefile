# Centinela — DEMO / DATOS FICTICIOS
PY      ?= .venv/bin/python
OFFLINE := HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 ANONYMIZED_TELEMETRY=False DO_NOT_TRACK=1
INSPECTOR_VERSION ?= 2.11.0

.PHONY: help setup seed ingest documentos vision mcp inspector test pull-models warmup run ensayo check-offline

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

warmup run ensayo check-offline:
	@echo "«$@» llega en las fases 2-6 (ver README)."
