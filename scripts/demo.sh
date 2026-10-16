#!/usr/bin/env bash
# `make demo`: levanta todo con un comando. Prepara lo que falte (base, índice, documentos), levanta Ollama en
# segundo plano si no responde, abre la UI en el navegador y deja el orquestador en primer plano con sus logs.
# Para precalentar: `make warmup` en otra terminal.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 ANONYMIZED_TELEMETRY=False DO_NOT_TRACK=1

[[ -x "$PY" ]] || { echo "Falta el entorno de Python: make setup (o ./scripts/setup_mac.sh)"; exit 1; }

# Rutas y URLs desde config.yaml (nunca fijas aquí).
eval "$("$PY" -c '
import shlex
from comun import config
c = config.cargar()
host, puerto = c["orquestador"].get("host", "127.0.0.1"), c["orquestador"].get("puerto", 8765)
for k, v in {"DB": config.ruta("db"), "VECTORES": config.ruta("vectores"), "DOCS": config.ruta("documentos_demo"),
             "OLLAMA_URL": c["ollama"]["url"].rstrip("/"), "URL_UI": f"http://{host}:{puerto}"}.items():
    print(f"{k}={shlex.quote(str(v))}")
')"

[[ -f "$DB" ]] || { echo "▸ Falta la base: make seed"; make seed PY="$PY"; }
[[ -f "$VECTORES" ]] || { echo "▸ Falta el índice de políticas: make ingest"; make ingest PY="$PY"; }
ls "$DOCS"/*.png >/dev/null 2>&1 || { echo "▸ Faltan los documentos demo: make documentos"; make documentos PY="$PY"; }

if ! curl -s --max-time 2 "$OLLAMA_URL/api/version" >/dev/null; then
  mkdir -p salida
  echo "▸ Ollama no responde: lo levanto en segundo plano (log en salida/ollama.log)"
  nohup ./scripts/ollama_serve.sh > salida/ollama.log 2>&1 &
  for _ in $(seq 1 30); do curl -s --max-time 1 "$OLLAMA_URL/api/version" >/dev/null && break; sleep 1; done
  curl -s --max-time 2 "$OLLAMA_URL/api/version" >/dev/null || { echo "❌ Ollama no arrancó: vea salida/ollama.log"; exit 1; }
fi

if [[ "$(uname -s)" == "Darwin" && "${DEMO_ABRIR:-1}" != "0" ]]; then
  ( for _ in $(seq 1 60); do curl -s --max-time 1 "$URL_UI/api/salud" >/dev/null && { open "$URL_UI"; break; }; sleep 1; done ) &
fi

echo "▸ Centinela en $URL_UI · precaliente con «make warmup» en otra terminal · logs: make logs"
exec "$PY" -m orchestrator
