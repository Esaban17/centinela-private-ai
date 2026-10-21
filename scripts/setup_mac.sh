#!/usr/bin/env bash
# Instalación desde cero en una Mac Apple Silicon. Requiere internet UNA vez (Homebrew, Python, modelos);
# después la demo corre con el Wi-Fi apagado.
#
#   ./scripts/setup_mac.sh                # todo menos el límite de GPU
#   ./scripts/setup_mac.sh --limite-gpu   # además sube el límite de memoria de GPU (pide sudo; dura hasta reiniciar)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Este script es para macOS en Apple Silicon. En Linux: make setup && make seed ingest documentos test."
  exit 1
fi
command -v brew >/dev/null || { echo "Falta Homebrew: instálelo desde https://brew.sh y vuelva a correr."; exit 1; }

paso() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

paso "Herramientas (Homebrew): uv, ollama, ffmpeg"
for p in uv ollama ffmpeg; do brew list --formula "$p" >/dev/null 2>&1 || brew install "$p"; done

paso "Entorno de Python 3.12 (.venv) y dependencias"
make setup

[[ -f .env ]] || { cp .env.example .env; echo "Creado .env a partir de .env.example"; }

paso "Ollama (temporal, para descargar los modelos)"
OLLAMA_PID=""
if ! curl -s --max-time 2 http://127.0.0.1:11434/api/version >/dev/null; then
  mkdir -p salida
  ./scripts/ollama_serve.sh > salida/ollama.log 2>&1 &
  OLLAMA_PID=$!
  for _ in $(seq 1 30); do curl -s --max-time 1 http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
fi

paso "Modelos de Ollama del perfil que corresponde a esta RAM (make modelos; ~31 GB en 48 GB, ~11 GB en 24 GB)"
make pull-models

paso "Modelos de voz: Whisper (MLX) y Kokoro en ./modelos"
make voz-modelos

paso "Datos ficticios: base, índice de políticas y documentos demo"
make seed
make ingest
make documentos

paso "Pruebas"
make test

if [[ -n "$OLLAMA_PID" ]]; then kill "$OLLAMA_PID" 2>/dev/null || true; fi

if [[ "${1:-}" == "--limite-gpu" ]]; then
  total_mb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 ))
  limite_mb="${LIMITE_GPU_MB:-$(( total_mb - 8192 ))}"   # deja 8 GB al sistema (48 GB → 40960 MB)
  paso "Límite de memoria de GPU: ${limite_mb} MB de ${total_mb} MB (se revierte al reiniciar)"
  sudo sysctl iogpu.wired_limit_mb="$limite_mb"
fi

cat <<'FIN'

✅ Instalación lista. Para la demo (ver GUION.md):
   1. make demo          levanta Ollama (si hace falta), el orquestador y abre la UI
   2. make warmup        en otra terminal: carga los modelos y corre el guion una vez
   3. make check-offline con el Wi-Fi apagado: 0 conexiones salientes
FIN
