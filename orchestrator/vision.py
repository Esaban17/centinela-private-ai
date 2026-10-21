"""Gancho de visión para el orquestador (contrato de eventos v1, §5).

El orquestador busca `crear_vision(cfg, ollama)` y llama `await vision.extraer(imagen, nombre)`. La lógica vive
en `documentos.extraccion`; este módulo solo la adapta al cliente de Ollama del orquestador, para que el uso
del modelo de visión (rol `vision`) aparezca en el chip y en los tokens del panel.

Después, el agente cruza los campos con la base mediante la herramienta MCP `validar_documento`.
"""

from __future__ import annotations

import inspect
from typing import Any

from documentos.extraccion import ErrorExtraccion, Extraccion, LlamarVision, extraer, llamador_ollama

__all__ = ["crear_vision", "Vision", "Extraccion", "ErrorExtraccion"]


def _texto(respuesta: Any) -> str:
    """Saca el texto de la respuesta, sea un dict de Ollama o un objeto del cliente del orquestador."""
    if isinstance(respuesta, str):
        return respuesta
    if isinstance(respuesta, dict):
        return (respuesta.get("message") or {}).get("content") or respuesta.get("texto") or respuesta.get("content") or ""
    for attr in ("texto", "contenido", "content"):
        valor = getattr(respuesta, attr, None)
        if isinstance(valor, str):
            return valor
    mensaje = getattr(respuesta, "message", None)
    return getattr(mensaje, "content", "") if mensaje is not None else ""


def _llamador_orquestador(ollama: Any, opciones: dict[str, Any]) -> LlamarVision:
    parametros = set(inspect.signature(ollama.chat).parameters)
    clave_formato = next((p for p in ("formato", "format") if p in parametros), None)
    clave_opciones = next((p for p in ("opciones", "options") if p in parametros), None)

    async def llamar(mensajes: list[dict[str, Any]], imagenes: list[bytes], formato: dict[str, Any] | None) -> str:
        kw: dict[str, Any] = {"imagenes": imagenes}
        if clave_formato and formato is not None:
            kw[clave_formato] = formato
        if clave_opciones:
            kw[clave_opciones] = opciones
        respuesta = ollama.chat("vision", mensajes, **kw)
        if inspect.isawaitable(respuesta):
            respuesta = await respuesta
        return _texto(respuesta)

    return llamar


class Vision:
    def __init__(self, cfg: dict[str, Any], llamar: LlamarVision):
        v = cfg.get("vision", {})
        self.cfg = cfg
        self.max_lado = int(v.get("max_lado_px", 1600))
        self.clasificar = bool(v.get("clasificar", True))
        self._llamar = llamar

    @property
    def modelo(self) -> str:
        """Se lee en cada uso: la UI puede cambiar el modelo de visión en caliente."""
        return self.cfg["modelos"]["vision"]["nombre"]

    async def extraer(self, imagen: bytes, nombre: str, tipo: str | None = None) -> Extraccion:
        """Imagen → campos validados con Pydantic. Lanza `ErrorExtraccion` con un mensaje para la persona."""
        return await extraer(self._llamar, imagen, nombre, tipo=tipo, modelo=self.modelo, max_lado=self.max_lado,
                             clasificar_con_modelo=self.clasificar)


def crear_vision(cfg: dict[str, Any], ollama: Any | None = None) -> Vision:
    """`ollama`: el cliente del orquestador (con `chat(rol, mensajes, imagenes=...)`) o None para usar el
    cliente HTTP directo (`comun.ollama_http.ClienteOllama`), útil en la línea de comandos."""
    opciones = {"temperature": 0, "num_ctx": int(cfg["modelos"]["vision"].get("num_ctx", 8192))}
    if ollama is None or not hasattr(ollama, "chat") or "imagenes" not in inspect.signature(ollama.chat).parameters:
        if ollama is None:
            from comun.ollama_http import ClienteOllama

            ollama = ClienteOllama.desde_config(cfg, timeout=float(cfg.get("vision", {}).get("timeout_s", 90)))
        return Vision(cfg, llamador_ollama(ollama, cfg["modelos"]["vision"]["nombre"], opciones))
    # El cliente del orquestador agrega el num_ctx vigente de config (cambia si la UI elige otro modelo).
    return Vision(cfg, _llamador_orquestador(ollama, {"temperature": 0}))
