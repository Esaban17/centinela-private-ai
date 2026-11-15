#!/usr/bin/env bash
# Arranque del contenedor: base local (solo la usa la auditoría del servidor de políticas), índice de políticas con
# los embeddings bge-m3 de PAIS (una vez, queda en el volumen /datos) y el orquestador.
set -euo pipefail
cd /app
mkdir -p /datos/vectores /datos/modelos
export CENTINELA__rutas__db=/datos/banco.db
export CENTINELA__rutas__vectores=/datos/vectores/politicas_pais.db
export CENTINELA__rutas__modelos_voz=/datos/modelos
[[ -f /datos/banco.db ]] || python -m scripts.seed
if [[ ! -f /datos/vectores/politicas_pais.db || "${REINDEXAR:-0}" == "1" ]]; then
  echo "▸ Indexando políticas con bge-m3 de PAIS…"
  python -m scripts.ingest || { echo "▸ Sin embeddings de PAIS: índice solo léxico"; python -m scripts.ingest --sin-embeddings; }
fi
case "${1:-run}" in
  run)    exec python -m orchestrator ;;
  probar) exec python -m scripts.probar_pais "${@:2}" ;;
  cli)    exec python -m orchestrator.cli "${@:2}" ;;
  voz)    unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE; exec python -m scripts.descargar_modelos_voz --faster --sin-mlx ;;
  *)      exec "$@" ;;
esac
