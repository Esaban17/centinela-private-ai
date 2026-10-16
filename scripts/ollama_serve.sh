#!/usr/bin/env bash
# `make ollama`: levanta `ollama serve` con las variables de config.yaml → ollama.servidor:
# solo en 127.0.0.1, los 4 modelos cargados a la vez y sin vencimiento (nada de «cold start» en escena).
# Corre en primer plano; ciérrelo con Ctrl+C. `make demo` lo levanta en segundo plano si hace falta.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"

if pgrep -f "Ollama.app/Contents/MacOS/Ollama" >/dev/null 2>&1; then
  echo "⚠️  La app de Ollama de la barra de menú está abierta: busca actualizaciones por internet y no usa"
  echo "   estas variables. Ciérrela (ícono de la barra → Quit Ollama) y vuelva a correr make ollama."
  exit 1
fi
if command -v brew >/dev/null 2>&1 && brew services list 2>/dev/null | grep -Eq '^ollama +started'; then
  echo "⚠️  Ollama corre como servicio de Homebrew, sin estas variables. Deténgalo: brew services stop ollama"
  exit 1
fi

# export OLLAMA_HOST=… OLLAMA_MAX_LOADED_MODELS=… (los valores salen de config.yaml, nunca del código)
eval "$("$PY" -c '
import shlex
from comun import config
for k, v in (config.cargar()["ollama"].get("servidor") or {}).items():
    print(f"export {k}={shlex.quote(str(v))}")
')"

if curl -s --max-time 2 "http://${OLLAMA_HOST:-127.0.0.1:11434}/api/version" >/dev/null; then
  echo "Ollama ya responde en ${OLLAMA_HOST:-127.0.0.1:11434}. Si lo inició de otra forma, deténgalo para que tome"
  echo "las variables de config.yaml (ollama.servidor) y vuelva a correr make ollama."
  exit 0
fi

echo "ollama serve en ${OLLAMA_HOST} · $(env | grep '^OLLAMA_' | grep -v '^OLLAMA_HOST=' | sort | tr '\n' ' ')"
exec ollama serve
