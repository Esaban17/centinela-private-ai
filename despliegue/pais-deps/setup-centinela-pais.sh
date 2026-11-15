#!/usr/bin/env bash
# Despliega Centinela (modo PAIS) en pais-deps. Ejecutar desde la raíz del repo clonado en la VM:
#   bash despliegue/pais-deps/setup-centinela-pais.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
DOCKER="sudo docker"
COMPOSE="$DOCKER compose -f despliegue/pais-deps/docker-compose.yml"

if [[ ! -s .env ]]; then
  echo "== Configuración (se guarda en .env con permisos 600)"
  umask 077
  cp .env.pais.example .env
  echo "Autenticación ante PAIS:"
  echo "  1) Usuario de servicio en Keycloak (recomendado)"
  echo "  2) Token estático"
  read -rp "Opción [1]: " OP; OP=${OP:-1}
  if [[ "$OP" == "2" ]]; then
    read -rsp "Token: " TK; echo
    sed -i "s|^# PAIS_TOKEN=.*|PAIS_TOKEN=$TK|; s|^PAIS_KC_USUARIO=.*|# PAIS_KC_USUARIO=|" .env
  else
    read -rp "Usuario Keycloak [app-centinela]: " KU; KU=${KU:-app-centinela}
    read -rsp "Contraseña de $KU: " KP; echo
    sed -i "s|^PAIS_KC_USUARIO=.*|PAIS_KC_USUARIO=$KU|; s|^PAIS_KC_CLAVE=.*|PAIS_KC_CLAVE=$KP|" .env
  fi
  CLAVE_MCP=""
  for f in ~/mcp-demo/.env ~/mcp-demo-v4/.env; do
    [[ -f "$f" ]] && CLAVE_MCP=$(grep -E '^MCP_API_KEY=' "$f" | cut -d= -f2- || true) && [[ -n "$CLAVE_MCP" ]] && break
  done
  if [[ -z "$CLAVE_MCP" ]]; then read -rsp "Clave del MCP demo-banco (MCP_API_KEY de mcp-demo/.env): " CLAVE_MCP; echo; fi
  sed -i "s|^PAIS_MCP_API_KEY=.*|PAIS_MCP_API_KEY=$CLAVE_MCP|" .env
  chmod 600 .env
fi

echo "== Construyendo imagen gq-centinela-pais:1.0 (VOZ=${VOZ:-1})"
$COMPOSE build -q
echo "== Diagnóstico contra PAIS (token, modelos, herramientas, embeddings, MCP, agente)"
$COMPOSE run --rm --no-deps centinela probar --ambos || echo "(revise los puntos marcados; ver docs/PAIS.md)"
if [[ "${VOZ:-1}" == "1" ]]; then
  echo "== Modelos de voz (una vez, requiere internet): Kokoro + faster-whisper small"
  $COMPOSE run --rm --no-deps centinela voz || echo "(sin voz: Centinela funciona por texto)"
fi
$COMPOSE up -d --force-recreate
sleep 5
curl -s http://127.0.0.1:8300/api/salud; echo
IP=$(hostname -I | awk '{print $1}')
cat <<MSG

Listo. Centinela corre en http://$IP:8300
Para usar el micrófono el navegador exige HTTPS o localhost. Desde la Mac:
  ssh -N -L 8300:127.0.0.1:8300 pais@$IP     y abra http://127.0.0.1:8300
Logs: $COMPOSE logs -f
MSG
