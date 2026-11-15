"""Conexión con VMware Private AI Services (PAIS 3.0): configuración, token y embeddings.

Con `proveedor: pais` (config.pais.yaml) los modelos no corren en Ollama sino en PAIS, dentro del datacenter:

- Modelos crudos por la API OpenAI-compatible: `{url}/api/v1/compatibility/openai/v1` → `/chat/completions`,
  `/embeddings` y `/models`. El campo `model` es el `routingName` del ModelEndpoint (p. ej. `llama-3.1-8b`).
- Agentes de PAIS (Agent Builder): `/agents/{agent_id}/chat/completions`, sin `model` (ver orchestrator/pais.py).
- Autenticación: `Authorization: Bearer <access token OIDC>`. El token sale de Keycloak con un usuario de
  servicio (password grant) o es un token estático. Vive solo en este proceso: nunca llega al navegador.

Las credenciales se leen de variables de entorno (`.env`, permisos 600), nunca de config.yaml:
  PAIS_TOKEN                     token estático (si está, no se usa Keycloak)
  PAIS_KC_USUARIO / PAIS_KC_CLAVE   usuario de servicio en Keycloak (p. ej. app-centinela)
  PAIS_KC_CLIENT_SECRET          solo si el client de Keycloak es confidencial
  PAIS_MCP_API_KEY               clave del servidor MCP demo-banco (ver config.pais.yaml → mcp.servidores)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from comun.config import RAIZ
from comun.ollama_http import ErrorOllama

log = logging.getLogger("centinela.pais")

RUTA_OPENAI = "/api/v1/compatibility/openai/v1"


class ErrorPAIS(ErrorOllama):
    """PAIS o Keycloak no respondieron, o respondieron con error. Hereda de ErrorOllama para que el RAG y los
    scripts que ya manejan «el servidor de modelos no responde» lo traten igual."""


def es_pais(cfg: dict[str, Any]) -> bool:
    return str(cfg.get("proveedor") or "ollama").lower() == "pais"


def seccion(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("pais") or {}


def url_base(cfg: dict[str, Any]) -> str:
    return str(seccion(cfg).get("url") or "https://pais.vcflab.local").rstrip("/")


def url_openai(cfg: dict[str, Any]) -> str:
    return url_base(cfg) + RUTA_OPENAI


def verificar_tls(cfg: dict[str, Any], clave: str = "ca") -> str | bool:
    """Ruta del bundle de la CA del lab, o True/False. `pais.ca` vacío y `pais.verificar_tls: false` = sin
    verificar (el certificado del lab no es de una CA pública; en producción use la CA)."""
    p = seccion(cfg)
    ca = str(p.get(clave) or p.get("ca") or "").strip()
    if ca:
        ruta = Path(ca)
        return str(ruta if ruta.is_absolute() else RAIZ / ruta)
    return bool(p.get("verificar_tls", True))


def destinos(cfg: dict[str, Any]) -> list[str]:
    """Hosts a los que Centinela habla en modo PAIS (para la guardia de salida y el panel)."""
    from urllib.parse import urlparse

    hosts = {urlparse(url_base(cfg)).hostname}
    kc = (seccion(cfg).get("keycloak") or {}).get("url_token")
    if kc:
        hosts.add(urlparse(kc).hostname)
    for s in (cfg.get("mcp") or {}).get("servidores") or []:
        if s.get("url"):
            hosts.add(urlparse(s["url"]).hostname)
    return sorted(h for h in hosts if h)


class TokenPAIS:
    """Access token para PAIS. Token estático o password grant de Keycloak, cacheado hasta 30 s antes de vencer.

    Es seguro llamarlo desde varios hilos (el servidor MCP local y el orquestador corren en procesos distintos;
    cada uno tiene su propia instancia)."""

    def __init__(self, cfg: dict[str, Any], transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        kc = seccion(cfg).get("keycloak") or {}
        self.estatico = os.environ.get("PAIS_TOKEN", "").strip()
        self.url_token = str(kc.get("url_token") or "")
        self.client_id = str(kc.get("client_id") or "pais")
        self.usuario = os.environ.get("PAIS_KC_USUARIO", "") or str(kc.get("usuario") or "")
        self.clave = os.environ.get("PAIS_KC_CLAVE", "")
        self.secreto = os.environ.get("PAIS_KC_CLIENT_SECRET", "")
        self._verificar = verificar_tls(cfg, "ca_keycloak")
        self._transport = transport
        self._token: str | None = None
        self._vence = 0.0
        self._candado = threading.Lock()

    @property
    def modo(self) -> str:
        return "token" if self.estatico else ("keycloak" if self.url_token else "sin_credenciales")

    def obtener(self, forzar: bool = False) -> str:
        if self.estatico:
            return self.estatico
        with self._candado:
            if not forzar and self._token and time.time() < self._vence - 30:
                return self._token
            if not self.url_token or not self.usuario or not self.clave:
                raise ErrorPAIS("Faltan credenciales de PAIS: defina PAIS_TOKEN, o PAIS_KC_USUARIO y PAIS_KC_CLAVE "
                                "en .env (y pais.keycloak.url_token en config.pais.yaml).")
            datos = {"grant_type": "password", "client_id": self.client_id, "username": self.usuario,
                     "password": self.clave, "scope": "openid"}
            if self.secreto:
                datos["client_secret"] = self.secreto
            try:
                with httpx.Client(verify=self._verificar, timeout=15, transport=self._transport,
                                  trust_env=False) as c:
                    r = c.post(self.url_token, data=datos)
            except httpx.HTTPError as e:
                raise ErrorPAIS(f"No se pudo contactar a Keycloak ({self.url_token}): {e}") from e
            if r.status_code != 200:
                raise ErrorPAIS(f"Keycloak respondió {r.status_code}: {r.text[:200]}")
            j = r.json()
            self._token = j["access_token"]
            self._vence = time.time() + float(j.get("expires_in", 300))
            log.info("token de PAIS renovado (Keycloak, usuario %s, vence en %ss)", self.usuario,
                     j.get("expires_in", "?"))
            return self._token

    def invalidar(self) -> None:
        with self._candado:
            self._token, self._vence = None, 0.0


class ClientePAIS:
    """Cliente síncrono para la API OpenAI-compatible de PAIS. Tiene la misma forma que `ClienteOllama` en lo
    que usan el RAG y la ingesta (`embeddings`, `version`, `cerrar`)."""

    def __init__(self, cfg: dict[str, Any], *, timeout: float = 120.0, token: TokenPAIS | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self.url = url_openai(cfg)
        self.token = token or TokenPAIS(cfg, transport=transport)
        self._http = httpx.Client(base_url=self.url, timeout=timeout, transport=transport, trust_env=False,
                                  verify=verificar_tls(cfg))

    def _pedir(self, metodo: str, ruta: str, cuerpo: dict[str, Any] | None = None,
               timeout: float | None = None) -> dict[str, Any]:
        for intento in (1, 2):
            try:
                r = self._http.request(metodo, ruta, json=cuerpo,
                                       headers={"Authorization": f"Bearer {self.token.obtener(forzar=intento == 2)}"},
                                       **({"timeout": timeout} if timeout else {}))
            except httpx.HTTPError as e:
                raise ErrorPAIS(f"No se pudo contactar a PAIS en {self.url}: {e}") from e
            if r.status_code == 401 and intento == 1 and not self.token.estatico:
                continue  # token vencido o revocado: se pide otro una vez
            if r.status_code != 200:
                raise ErrorPAIS(f"PAIS respondió {r.status_code} en {ruta}: {r.text[:300]}")
            return r.json()
        raise ErrorPAIS("PAIS rechazó el token (401).")

    def embeddings(self, modelo: str, textos: list[str], timeout: float | None = None) -> list[list[float]]:
        if not textos:
            return []
        datos = self._pedir("POST", "/embeddings", {"model": modelo, "input": textos}, timeout)
        filas = sorted(datos.get("data") or [], key=lambda d: d.get("index", 0))
        vectores = [f.get("embedding") or [] for f in filas]
        if len(vectores) != len(textos):
            raise ErrorPAIS(f"PAIS devolvió {len(vectores)} vectores para {len(textos)} textos.")
        return vectores

    def modelos(self) -> list[str]:
        return [m.get("id") for m in self._pedir("GET", "/models", timeout=10).get("data") or []]

    def version(self) -> str:
        return f"PAIS · {len(self.modelos())} modelos"

    def cerrar(self) -> None:
        self._http.close()


def cliente_embeddings(cfg: dict[str, Any], timeout: float = 120.0) -> Any:
    """El cliente de embeddings que corresponde al proveedor: PAIS o el Ollama local."""
    if es_pais(cfg):
        return ClientePAIS(cfg, timeout=timeout)
    from comun.ollama_http import ClienteOllama

    return ClienteOllama.desde_config(cfg, timeout=timeout)
