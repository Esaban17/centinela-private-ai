#!/usr/bin/env bash
# Descarga en Ollama los modelos definidos en config.yaml. Requiere internet: se corre UNA vez en la instalación.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
for rol in agente rapido vision embeddings; do
  modelo=$("$PY" -c "from comun import config; print(config.cargar()['modelos']['$rol']['nombre'])")
  case "$modelo" in
    *:cloud|*-cloud) echo "❌ $rol: $modelo es un modelo :cloud de Ollama (corre fuera del equipo)"; exit 1 ;;
  esac
  echo "→ $rol: $modelo"
  ollama pull "$modelo"
done
ollama list
