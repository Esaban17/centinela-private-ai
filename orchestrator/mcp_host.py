"""Host MCP: conecta con el servidor «banco-atitlan» por stdio y traduce sus herramientas a Ollama.

- Las herramientas se descubren con `tools/list`; nada está quemado aquí. Lo que el servidor exponga, el
  agente lo puede usar (y cualquier otro cliente MCP también: el estándar es abierto).
- La aprobación humana de escrituras la pide el SERVIDOR (elicitation). Este host la recibe y la reenvía al
  turno en curso, que la muestra en la UI y espera la decisión de la persona.
- Cada llamada lleva `usuario` y `modelo` en `_meta`, que el servidor guarda en la auditoría.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Protocol

import mcp_types as types
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from comun.config import RAIZ

log = logging.getLogger("centinela.mcp")


class MCPNoDisponible(RuntimeError):
    """El servidor MCP no arrancó o se cayó."""


class SolicitanteAprobacion(Protocol):
    async def pedir_aprobacion(self, mensaje: str, esquema: dict[str, Any] | None) -> tuple[bool, str]: ...


solicitante_actual: contextvars.ContextVar[SolicitanteAprobacion | None] = contextvars.ContextVar(
    "solicitante", default=None)


@dataclass
class Herramienta:
    nombre: str
    titulo: str
    descripcion: str
    esquema: dict[str, Any]
    escritura: bool

    def para_ollama(self) -> dict[str, Any]:
        params = {k: v for k, v in self.esquema.items() if k != "title"}
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        return {"type": "function", "function": {"name": self.nombre, "description": self.descripcion,
                                                 "parameters": params}}

    def para_ui(self) -> dict[str, Any]:
        return {"nombre": self.nombre, "titulo": self.titulo, "descripcion": self.descripcion,
                "escritura": self.escritura}


@dataclass
class ResultadoHerramienta:
    ok: bool
    datos: Any  # structuredContent, o el texto si no hay
    texto: str  # lo que ve el modelo

    def resumen(self) -> str:
        d = self.datos
        if isinstance(d, dict):
            for clave in ("mensaje", "resumen"):
                if d.get(clave):
                    return str(d[clave])[:200]
            partes = [f"{k}: {v}" for k, v in d.items() if isinstance(v, (int, float, bool, str)) and len(str(v)) < 60]
            if partes:
                return " · ".join(partes[:4])[:200]
        return (self.texto or "").replace("\n", " ")[:200]


def parametros_stdio(cfg: dict[str, Any]) -> StdioServerParameters:
    """El servidor MCP como proceso hijo, con las mismas variables de entorno (incluidas las de modo offline)."""
    m = cfg.get("mcp", {})
    comando = m.get("comando") or sys.executable
    args = m.get("args") or ["-m", "mcp_server"]
    return StdioServerParameters(command=comando, args=list(args), cwd=str(RAIZ), env=dict(os.environ))


class HostMCP:
    def __init__(self, objetivo: Any, modo: str = "auto", nombre_cliente: str = "centinela-orquestador"):
        """`objetivo`: StdioServerParameters (producción) o un MCPServer en memoria (pruebas)."""
        self.objetivo = objetivo
        self.modo = modo
        self.nombre_cliente = nombre_cliente
        self.cliente: Client | None = None
        self.herramientas: dict[str, Herramienta] = {}
        self._pila: AsyncExitStack | None = None
        # Las escrituras se serializan: si el SDK entrega la elicitation fuera del contexto de la llamada
        # (protocolo clásico), se sabe a qué turno pertenece.
        self._candado_escritura = asyncio.Lock()
        self._solicitante_escritura: SolicitanteAprobacion | None = None

    @property
    def conectado(self) -> bool:
        return self.cliente is not None

    async def conectar(self) -> None:
        if self.cliente is not None:
            return
        pila = AsyncExitStack()
        try:
            cliente = Client(self.objetivo, mode=self.modo, elicitation_callback=self._elicitar,
                             client_info=types.Implementation(name=self.nombre_cliente, version="0.2.0"))
            self.cliente = await pila.enter_async_context(cliente)
            await self._cargar_herramientas()
        except Exception as e:
            await pila.aclose()
            self.cliente = None
            raise MCPNoDisponible(f"No se pudo iniciar el servidor MCP: {e}") from e
        self._pila = pila
        log.info("MCP conectado: %d herramientas (%s)", len(self.herramientas), ", ".join(self.herramientas))

    async def cerrar(self) -> None:
        if self._pila is not None:
            pila, self._pila, self.cliente = self._pila, None, None
            try:
                await pila.aclose()
            except Exception as e:  # el proceso hijo pudo haber muerto ya
                log.debug("al cerrar MCP: %s", e)

    async def _cargar_herramientas(self) -> None:
        assert self.cliente is not None
        res = await self.cliente.list_tools()
        self.herramientas = {}
        for t in res.tools:
            ann = t.annotations
            escritura = not (ann is not None and ann.read_only_hint is True)
            self.herramientas[t.name] = Herramienta(t.name, t.title or t.name, t.description or "",
                                                    dict(t.input_schema), escritura)

    def para_ollama(self) -> list[dict[str, Any]]:
        return [h.para_ollama() for h in self.herramientas.values()]

    async def _elicitar(self, _ctx: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
        solicitante = solicitante_actual.get() or self._solicitante_escritura
        if solicitante is None:
            log.warning("elicitation sin turno en curso: se rechaza")
            return types.ElicitResult(action="decline")
        esquema = getattr(params, "requested_schema", None)
        aprobar, comentario = await solicitante.pedir_aprobacion(params.message, esquema)
        return types.ElicitResult(action="accept", content={"aprobar": aprobar, "comentario": comentario})

    async def llamar(self, nombre: str, argumentos: dict[str, Any], meta: dict[str, Any], timeout_s: float,
                     solicitante: SolicitanteAprobacion | None = None) -> ResultadoHerramienta:
        if self.cliente is None:
            await self.conectar()
        herramienta = self.herramientas.get(nombre)
        token = solicitante_actual.set(solicitante)
        try:
            if herramienta is not None and herramienta.escritura:
                async with self._candado_escritura:
                    self._solicitante_escritura = solicitante
                    try:
                        r = await self._llamar(nombre, argumentos, meta, timeout_s)
                    finally:
                        self._solicitante_escritura = None
            else:
                r = await self._llamar(nombre, argumentos, meta, timeout_s)
        finally:
            solicitante_actual.reset(token)
        datos = r.structured_content
        texto = "\n".join(getattr(c, "text", "") for c in r.content if getattr(c, "text", None))
        if datos is None:
            datos = texto
        return ResultadoHerramienta(ok=not r.is_error, datos=datos,
                                    texto=json.dumps(datos, ensure_ascii=False, default=str)
                                    if not isinstance(datos, str) else datos)

    async def _llamar(self, nombre: str, argumentos: dict[str, Any], meta: dict[str, Any],
                      timeout_s: float) -> types.CallToolResult:
        assert self.cliente is not None
        try:
            async with asyncio.timeout(timeout_s):
                return await self.cliente.call_tool(nombre, argumentos, meta=meta)
        except TimeoutError:
            raise
        except (OSError, EOFError) as e:  # el proceso del servidor murió: se reintenta en la próxima llamada
            await self.cerrar()
            raise MCPNoDisponible(f"Se perdió la conexión con el servidor MCP: {e}") from e
