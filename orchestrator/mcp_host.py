"""Host MCP: conecta con el servidor «banco-atitlan» por stdio y traduce sus herramientas a Ollama.

Con `mcp.servidores` (modo PAIS, config.pais.yaml) conecta varios servidores a la vez, por stdio o por
Streamable HTTP (p. ej. demo-banco en pais-deps:8101), y `HostMCPMultiple` enruta cada herramienta a su servidor.

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


@dataclass
class ServidorHTTP:
    """Un servidor MCP por Streamable HTTP. `encabezados` va en cada petición (p. ej. la Apikey)."""
    url: str
    encabezados: dict[str, str]
    timeout_s: float = 30.0


def parametros_stdio(cfg: dict[str, Any]) -> StdioServerParameters:
    """El servidor MCP como proceso hijo, con las mismas variables de entorno (incluidas las de modo offline)."""
    m = cfg.get("mcp", {})
    comando = m.get("comando") or sys.executable
    args = m.get("args") or ["-m", "mcp_server"]
    return StdioServerParameters(command=comando, args=list(args), cwd=str(RAIZ), env=dict(os.environ))


def causa_real(e: BaseException) -> str:
    """anyio envuelve el error verdadero en ExceptionGroup («unhandled errors in a TaskGroup»): se desenvuelve
    para que el panel y `make pais-probar` muestren la causa (401, conexión rechazada, etc.)."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    texto = str(e) or type(e).__name__
    resp = getattr(e, "response", None)
    if resp is not None and getattr(resp, "status_code", None):
        texto = f"HTTP {resp.status_code} en {getattr(resp, 'url', '')}: {texto}"
    return f"{type(e).__name__}: {texto}"


class HostMCP:
    def __init__(self, objetivo: Any, modo: str = "auto", nombre_cliente: str = "centinela-orquestador",
                 permitidas: list[str] | None = None, nombre: str = "", solo_lectura: bool = False):
        """`objetivo`: StdioServerParameters (producción), ServidorHTTP o un MCPServer en memoria (pruebas).
        `permitidas`: solo esas herramientas se ofrecen al modelo (vacío = todas). Con un modelo chico (Llama 3.1
        8B) menos herramientas = prompt más corto = menos errores."""
        self.objetivo = objetivo
        self.permitidas = list(permitidas or [])
        self.nombre = nombre
        self.solo_lectura = solo_lectura  # el servidor es de solo lectura aunque no anote read_only_hint
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
            objetivo = self.objetivo
            if isinstance(objetivo, ServidorHTTP):
                import httpx2
                from mcp.client.streamable_http import streamable_http_client

                http = await pila.enter_async_context(httpx2.AsyncClient(
                    headers=objetivo.encabezados, trust_env=False,
                    timeout=httpx2.Timeout(objetivo.timeout_s, read=300.0)))
                objetivo = streamable_http_client(objetivo.url, http_client=http)
            cliente = Client(objetivo, mode=self.modo, elicitation_callback=self._elicitar,
                             client_info=types.Implementation(name=self.nombre_cliente, version="0.2.0"))
            self.cliente = await pila.enter_async_context(cliente)
            await self._cargar_herramientas()
        except Exception as e:
            await pila.aclose()
            self.cliente = None
            raise MCPNoDisponible(f"No se pudo iniciar el servidor MCP: {causa_real(e)}") from e
        self._pila = pila
        log.info("MCP %sconectado: %d herramientas (%s)", f"{self.nombre} " if self.nombre else "",
                 len(self.herramientas), ", ".join(self.herramientas))

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
            if self.permitidas and t.name not in self.permitidas:
                continue
            ann = t.annotations
            escritura = not self.solo_lectura and not (ann is not None and ann.read_only_hint is True)
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
        elif isinstance(datos, dict) and list(datos) == ["result"] and isinstance(datos["result"], str):
            datos = datos["result"]  # FastMCP 1.x envuelve los resultados de texto: el modelo ve el texto tal cual
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


class HostMCPMultiple:
    """Varios servidores MCP detrás de la misma interfaz que `HostMCP`. Cada herramienta va a su servidor; si
    dos exponen el mismo nombre, gana el primero de la lista."""

    def __init__(self, hosts: list[HostMCP]):
        self.hosts = hosts
        self._de: dict[str, HostMCP] = {}

    @property
    def conectado(self) -> bool:
        return bool(self.hosts) and all(h.conectado for h in self.hosts)

    @property
    def herramientas(self) -> dict[str, Herramienta]:
        todas: dict[str, Herramienta] = {}
        self._de = {}
        for h in self.hosts:
            for nombre, herramienta in h.herramientas.items():
                if nombre not in todas:
                    todas[nombre] = herramienta
                    self._de[nombre] = h
        return todas

    def estado(self) -> list[dict[str, Any]]:
        return [{"nombre": h.nombre, "conectado": h.conectado, "herramientas": list(h.herramientas)}
                for h in self.hosts]

    async def conectar(self) -> None:
        errores = []
        for h in self.hosts:
            try:
                await h.conectar()
            except MCPNoDisponible as e:
                errores.append(f"{h.nombre or 'servidor'}: {e}")
        if errores:
            raise MCPNoDisponible("; ".join(errores))

    async def cerrar(self) -> None:
        # En orden inverso: cada conexión abre cancel scopes de anyio en esta misma tarea, y deben cerrarse LIFO.
        for h in reversed(self.hosts):
            await h.cerrar()

    def para_ollama(self) -> list[dict[str, Any]]:
        return [h.para_ollama() for h in self.herramientas.values()]

    async def llamar(self, nombre: str, argumentos: dict[str, Any], meta: dict[str, Any], timeout_s: float,
                     solicitante: SolicitanteAprobacion | None = None) -> ResultadoHerramienta:
        self.herramientas  # refresca el índice
        host = self._de.get(nombre)
        if host is None:
            for h in self.hosts:  # un servidor caído al arrancar: se intenta reconectar
                if not h.conectado:
                    try:
                        await h.conectar()
                    except MCPNoDisponible:
                        continue
            self.herramientas
            host = self._de.get(nombre)
        if host is None:
            raise MCPNoDisponible(f"Ningún servidor MCP conectado ofrece «{nombre}».")
        return await host.llamar(nombre, argumentos, meta, timeout_s, solicitante=solicitante)


def crear_host(cfg: dict[str, Any]) -> HostMCP | HostMCPMultiple:
    """`mcp.servidores` (lista) → varios servidores; si no, el «banco-atitlan» local por stdio, como siempre.

    Cada servidor: {nombre, transporte: stdio|http, url, api_key_env, encabezado_api_key, herramientas: [...]}.
    """
    servidores = (cfg.get("mcp") or {}).get("servidores") or []
    if not servidores:
        return HostMCP(parametros_stdio(cfg))
    hosts = []
    for s in servidores:
        permitidas = list(s.get("herramientas") or [])
        nombre = str(s.get("nombre") or "")
        if (s.get("transporte") or "stdio") == "http":
            encabezados = dict(s.get("encabezados") or {})
            clave = os.environ.get(str(s.get("api_key_env") or ""), "") if s.get("api_key_env") else ""
            if clave:
                encabezados[str(s.get("encabezado_api_key") or "Authorization")] = \
                    f"{s.get('esquema_api_key', 'Apikey')} {clave}".strip()
            # `url_env`: la URL puede venir de .env (p. ej. un túnel ssh a 127.0.0.1 cuando un proxy corporativo
            # corta el HTTP en claro hacia el puerto del MCP).
            url = (os.environ.get(str(s["url_env"]), "").strip() if s.get("url_env") else "") or str(s["url"])
            objetivo: Any = ServidorHTTP(url, encabezados, float(s.get("timeout_s", 30)))
        else:
            sub = {**cfg, "mcp": {**cfg.get("mcp", {}), "comando": s.get("comando", ""), "args": s.get("args", [])}}
            objetivo = parametros_stdio(sub)
        hosts.append(HostMCP(objetivo, permitidas=permitidas, nombre=nombre,
                             solo_lectura=bool(s.get("solo_lectura", False))))
    return HostMCPMultiple(hosts)
