"""Rutas /api/modelos/*: elegir los modelos desde la UI y avisar si no caben en la RAM del equipo.

- `GET  /api/modelos`          perfiles, modelos que se pueden elegir por rol, la selección vigente y su estimación.
- `POST /api/modelos/estimar`  estimación de una selección sin aplicarla (la UI la pide en cada cambio).
- `POST /api/modelos`          aplica la selección: agente, rápido y visión cambian en caliente (se descargan de
  Ollama los que ya no se usan y se precargan los nuevos); embeddings y voz rigen al reiniciar. Se guarda en
  config.local.yaml, así el próximo `make demo` arranca con lo mismo.

Nunca acepta un modelo `:cloud` ni uno que no esté descargado en Ollama (aquí no se descarga nada: no hay red).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from comun import config
from comun import modelos as sel_modelos

log = logging.getLogger("centinela.modelos")

ROLES_EN_CALIENTE = ("agente", "rapido", "vision")


class Seleccion(BaseModel):
    perfil: str = "manual"
    modelos: dict[str, str] = {}
    forzar: bool = False


def armar_seleccion(cfg: dict[str, Any], perfil: str, nombres: dict[str, str]) -> dict[str, dict[str, Any]]:
    """La sección `modelos` que resulta de un perfil (o «manual» desde lo vigente) y de los nombres elegidos."""
    sel = cfg.get("seleccion_modelos") or {}
    if perfil != "manual" and perfil not in (sel.get("perfiles") or {}):
        raise HTTPException(400, f"Perfil desconocido: {perfil}")
    if perfil == "manual":
        vigente = {r: {k: v for k, v in (cfg["modelos"].get(r) or {}).items() if k in sel_modelos.CLAVES_ROL}
                   for r in sel_modelos.ROLES_OLLAMA}
        modelos = sel_modelos.modelos_de_perfil(cfg, "manual", manual=vigente)
    else:
        modelos = sel_modelos.modelos_de_perfil(cfg, perfil)
    catalogo = sel.get("catalogo") or {}
    for rol in sel_modelos.ROLES_OLLAMA:
        nombre = (nombres.get(rol) or "").strip()
        if not nombre or nombre == modelos.get(rol, {}).get("nombre"):
            continue
        m = {k: v for k, v in modelos.get(rol, {}).items() if k != "think"}
        m["nombre"] = nombre
        if "think" in (catalogo.get(nombre) or {}):
            m["think"] = catalogo[nombre]["think"]
        modelos[rol] = m
    # Un mismo modelo en dos roles: el num_ctx del agente para todos, o Ollama lo recargaría en cada cambio.
    agente = modelos.get("agente") or {}
    for rol in ("rapido", "vision"):
        m = modelos.get(rol) or {}
        if m.get("nombre") and m.get("nombre") == agente.get("nombre") and agente.get("num_ctx"):
            m["num_ctx"] = agente["num_ctx"]
    return modelos


class ClienteModelos:
    """Lo poco que hace falta de Ollama: qué hay descargado, y cargar o descargar de memoria."""

    def __init__(self, cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=cfg["ollama"]["url"], transport=transport, trust_env=False,
                                      timeout=httpx.Timeout(10.0, connect=2.0))

    async def descargados(self) -> dict[str, float] | None:
        """{nombre:etiqueta: GB en disco} o None si Ollama no responde."""
        try:
            r = await self.http.get("/api/tags")
            r.raise_for_status()
            return {sel_modelos.con_etiqueta(m.get("name") or m.get("model")): round(int(m.get("size") or 0) / sel_modelos.GB, 1)
                    for m in r.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            return None

    async def liberar(self, nombre: str) -> None:
        try:
            await self.http.post("/api/generate", json={"model": nombre, "keep_alive": 0})
        except httpx.HTTPError as e:
            log.warning("no se pudo descargar %s de la memoria: %s", nombre, e)

    async def precargar(self, nombre: str, num_ctx: int | None, keep_alive: Any) -> None:
        cuerpo: dict[str, Any] = {"model": nombre, "keep_alive": keep_alive}
        if num_ctx:
            cuerpo["options"] = {"num_ctx": int(num_ctx)}
        try:
            await self.http.post("/api/generate", json=cuerpo, timeout=300)
            log.info("modelo precargado: %s", nombre)
        except httpx.HTTPError as e:
            log.warning("no se pudo precargar %s: %s", nombre, e)


def crear_router(cfg: dict[str, Any], ocupado: Callable[[], bool] = lambda: False,
                 al_aplicar: Callable[[], None] | None = None, cliente: ClienteModelos | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/modelos", tags=["modelos"])
    cli = cliente or ClienteModelos(cfg)
    tareas: set[asyncio.Task[None]] = set()
    estado: dict[str, Any] = {"pendiente_reinicio": []}

    def _resuelto() -> dict[str, Any]:
        return (cfg.get("seleccion_modelos") or {}).get("resuelto") or {}

    def _estimar(modelos: dict[str, Any], tamanos: dict[str, float] | None) -> dict[str, Any]:
        return sel_modelos.estimar(modelos, cfg, _resuelto().get("ram_gb"), tamanos)

    @router.get("")
    async def listar() -> dict[str, Any]:
        tamanos = await cli.descargados()
        sel = cfg.get("seleccion_modelos") or {}
        perfiles = []
        for nombre, p in (sel.get("perfiles") or {}).items():
            m = sel_modelos.modelos_de_perfil(cfg, nombre)
            perfiles.append({"id": nombre, "descripcion": (p or {}).get("descripcion", ""),
                             "ram_minima_gb": (p or {}).get("ram_minima_gb"),
                             "modelos": {r: m[r]["nombre"] for r in sel_modelos.ROLES_OLLAMA if m.get(r)},
                             "estimacion": _estimar(m, tamanos)})
        return {"perfil": _resuelto().get("perfil", "manual"), "motivo": _resuelto().get("motivo", ""),
                "seleccion": {r: cfg["modelos"][r]["nombre"] for r in sel_modelos.ROLES_OLLAMA if cfg["modelos"].get(r)},
                "estimacion": _estimar(cfg["modelos"], tamanos), "perfiles": perfiles,
                "roles": sel_modelos.opciones_por_rol(cfg, list(tamanos) if tamanos is not None else None),
                "ollama_disponible": tamanos is not None, "pendiente_reinicio": estado["pendiente_reinicio"]}

    @router.post("/estimar")
    async def estimar(cuerpo: Seleccion) -> dict[str, Any]:
        modelos = armar_seleccion(cfg, cuerpo.perfil, cuerpo.modelos)
        return _estimar(modelos, await cli.descargados())

    @router.post("")
    async def aplicar(cuerpo: Seleccion) -> dict[str, Any]:
        modelos = armar_seleccion(cfg, cuerpo.perfil, cuerpo.modelos)
        nube = [f"{r}: {m['nombre']}" for r, m in modelos.items()
                if r in sel_modelos.ROLES_OLLAMA and sel_modelos.es_nube(str(m.get("nombre") or ""))]
        if nube:
            raise HTTPException(400, {"codigo": "nube", "mensaje": "Los modelos :cloud corren fuera del equipo: "
                                      + ", ".join(nube)})
        tamanos = await cli.descargados()
        if tamanos is not None:
            faltan = sorted({m["nombre"] for r, m in modelos.items() if r in sel_modelos.ROLES_OLLAMA
                             and sel_modelos.con_etiqueta(m["nombre"]) not in tamanos})
            if faltan:
                raise HTTPException(409, {"codigo": "no_descargado", "faltan": faltan,
                                          "mensaje": "No están descargados en Ollama (hace falta internet una vez): "
                                          + " · ".join(f"ollama pull {n}" for n in faltan)})
        est = _estimar(modelos, tamanos)
        if est["nivel"] == "excede" and not cuerpo.forzar:
            raise HTTPException(409, {"codigo": "excede", "mensaje": est["mensaje"], "estimacion": est})
        if ocupado():
            raise HTTPException(409, {"codigo": "turno_en_curso",
                                      "mensaje": "Hay una respuesta en curso; aplique al terminar."})

        antes = {sel_modelos.con_etiqueta(cfg["modelos"][r]["nombre"]) for r in ROLES_EN_CALIENTE if cfg["modelos"].get(r)}
        for rol in ROLES_EN_CALIENTE:
            if rol in modelos:
                cfg["modelos"][rol] = modelos[rol]
        reinicio = []
        if modelos.get("embeddings", {}).get("nombre") != cfg["modelos"].get("embeddings", {}).get("nombre"):
            # El servidor MCP usa los embeddings en su propio proceso y el índice se armó con el modelo anterior.
            reinicio.append("embeddings: reinicie Centinela y corra `make ingest` (el índice de políticas se rehace)")
        estado["pendiente_reinicio"] = reinicio
        perfil = cuerpo.perfil
        if perfil != "manual" and any(modelos[r] != sel_modelos.modelos_de_perfil(cfg, perfil).get(r)
                                      for r in sel_modelos.ROLES_OLLAMA):
            perfil = "manual"
        cfg.setdefault("seleccion_modelos", {})["resuelto"] = {**_resuelto(), "perfil": perfil, "pedido": perfil,
                                                               "motivo": "elegido en la UI"}
        guardado = {"seleccion_modelos": {"perfil": perfil}}
        if perfil == "manual":
            guardado["seleccion_modelos"]["manual"] = {r: modelos[r] for r in sel_modelos.ROLES_OLLAMA if r in modelos}
        ruta = config.guardar_local(guardado)
        log.info("modelos elegidos en la UI (%s): %s · %s", perfil,
                 {r: modelos[r]["nombre"] for r in sel_modelos.ROLES_OLLAMA if r in modelos}, est["mensaje"])

        # Memoria: primero se sueltan los que ya nadie usa, después se cargan los nuevos (en segundo plano).
        despues = {sel_modelos.con_etiqueta(cfg["modelos"][r]["nombre"]) for r in ROLES_EN_CALIENTE}
        for nombre in antes - despues:
            await cli.liberar(nombre)
        keep_alive = (cfg.get("ollama") or {}).get("keep_alive", -1)
        for rol in ROLES_EN_CALIENTE:
            m = cfg["modelos"][rol]
            if sel_modelos.con_etiqueta(m["nombre"]) in despues - antes:
                t = asyncio.create_task(cli.precargar(m["nombre"], m.get("num_ctx"), keep_alive))
                tareas.add(t)
                t.add_done_callback(tareas.discard)
                antes.add(sel_modelos.con_etiqueta(m["nombre"]))  # un modelo compartido se carga una vez
        if al_aplicar:
            al_aplicar()
        return {"ok": True, "perfil": perfil, "estimacion": est, "pendiente_reinicio": reinicio,
                "archivo": str(ruta.name)}

    router.cliente = cli  # type: ignore[attr-defined]  (acceso en pruebas)
    return router
