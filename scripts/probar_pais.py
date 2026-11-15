"""Diagnóstico del modo PAIS: revisa, paso por paso, todo lo que Centinela necesita del lab.

    CENTINELA_PROVEEDOR=pais python -m scripts.probar_pais        (o `make pais-probar`)

1. Token (Keycloak o estático).
2. GET /models: los routingName publicados (llama-3.1-8b, bge-m3).
3. Chat simple con el modelo agente.
4. Chat con una herramienta: ¿PAIS pasa `tools` al modelo y devuelve `tool_calls`? Si no, el modo «agente propio»
   no puede usar herramientas y conviene el modo agente PAIS.
5. Embeddings con bge-m3.
6. Servidores MCP (demo-banco por HTTP y las políticas por stdio).
7. Agente de PAIS (agente-banco), con el caso CLI-00119.

Nunca imprime el token ni las claves.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from comun import config
from comun import pais as cpais

VERDE, ROJO, AMARILLO, GRIS, FIN = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"
HERRAMIENTA = {"type": "function", "function": {
    "name": "clima", "description": "Devuelve el clima actual de una ciudad de Guatemala.",
    "parameters": {"type": "object", "properties": {"ciudad": {"type": "string"}}, "required": ["ciudad"]}}}


def linea(ok: bool | None, titulo: str, detalle: str = "") -> None:
    marca = {True: f"{VERDE}✓{FIN}", False: f"{ROJO}✗{FIN}", None: f"{AMARILLO}!{FIN}"}[ok]
    print(f" {marca} {titulo}" + (f"{GRIS} · {detalle}{FIN}" if detalle else ""))


async def principal(args: argparse.Namespace) -> int:
    cfg = config.cargar()
    if not cpais.es_pais(cfg):
        print("El proveedor no es PAIS. Corra con CENTINELA_PROVEEDOR=pais (o `make pais-probar`).")
        return 2
    from orchestrator.mcp_host import MCPNoDisponible, crear_host
    from orchestrator.pais import AgentePAIS, PaisLLM, PaisNoDisponible

    print(f"PAIS: {cpais.url_base(cfg)} · agente {cfg['modelos']['agente']['nombre']} · "
          f"embeddings {cfg['modelos']['embeddings']['nombre']}")
    fallas = 0
    token = cpais.TokenPAIS(cfg)
    try:
        t = token.obtener()
        linea(True, f"Token ({token.modo})", f"{len(t)} caracteres")
    except cpais.ErrorPAIS as e:
        linea(False, "Token", str(e))
        return 1

    cliente = cpais.ClientePAIS(cfg, token=token, timeout=30)
    try:
        ids = cliente.modelos()
        faltan = [cfg["modelos"][r]["nombre"] for r in ("agente", "embeddings") if cfg["modelos"][r]["nombre"] not in ids]
        linea(not faltan, "Modelos publicados", ", ".join(ids) + (f" · FALTAN: {', '.join(faltan)}" if faltan else ""))
        fallas += bool(faltan)
    except cpais.ErrorPAIS as e:
        linea(False, "Modelos publicados", str(e))
        fallas += 1

    llm = PaisLLM(cfg, token=token)
    try:
        t0 = time.perf_counter()
        r = await llm.chat("agente", [{"role": "user", "content": "Responda solo: listo"}])
        linea(bool(r.contenido), "Chat simple", f"«{r.contenido.strip()[:60]}» · {time.perf_counter() - t0:.1f} s · "
                                                 f"{r.tokens_entrada}→{r.tokens_salida} tokens")
    except PaisNoDisponible as e:
        linea(False, "Chat simple", str(e))
        fallas += 1

    for stream in ([True, False] if args.ambos else [llm.stream]):
        llm.stream = stream
        try:
            t0 = time.perf_counter()
            r = await llm.chat("agente", [{"role": "system", "content": "Use las herramientas cuando apliquen."},
                                          {"role": "user", "content": "¿Qué clima hace en Antigua Guatemala?"}],
                               herramientas=[HERRAMIENTA])
            seg = time.perf_counter() - t0
            if r.llamadas:
                c = r.llamadas[0]
                linea(True, f"Herramientas (stream={stream})",
                      f"tool_calls: {c.nombre}({json.dumps(c.argumentos, ensure_ascii=False)}) · {seg:.1f} s")
            else:
                linea(None, f"Herramientas (stream={stream})",
                      f"el modelo respondió texto sin tool_calls: «{r.contenido.strip()[:80]}». Si se repite, PAIS no "
                      "pasa `tools` al modelo: use el modo agente PAIS o pruebe pais.stream: false.")
                fallas += 1
        except PaisNoDisponible as e:
            linea(False, f"Herramientas (stream={stream})", str(e))
            fallas += 1
    llm.stream = bool(cpais.seccion(cfg).get("stream", True))

    try:
        v = cliente.embeddings(cfg["modelos"]["embeddings"]["nombre"], ["política de crédito", "alertas de fraude"])
        linea(True, "Embeddings", f"{len(v)} vectores de {len(v[0])} dimensiones")
    except cpais.ErrorPAIS as e:
        linea(False, "Embeddings", str(e))
        fallas += 1
    cliente.cerrar()

    host = crear_host(cfg)
    try:
        await host.conectar()
    except MCPNoDisponible as e:
        linea(False, "Servidores MCP", str(e))
        fallas += 1
    for est in getattr(host, "estado", lambda: [{"nombre": "mcp", "conectado": host.conectado,
                                                  "herramientas": list(host.herramientas)}])():
        linea(est["conectado"], f"MCP {est['nombre']}", ", ".join(est["herramientas"]) or "sin herramientas")
    if "evaluar_solicitud_credito" in host.herramientas:
        try:
            res = await host.llamar("evaluar_solicitud_credito", {"cliente": "CLI-00119", "producto": "Personal",
                                                                  "monto": 50000}, {"usuario": "probar_pais"}, 30)
            linea(res.ok and "SUSPENDIDO" in res.texto, "demo-banco: CLI-00119", res.texto.splitlines()[0][:80])
        except Exception as e:  # noqa: BLE001 (diagnóstico)
            linea(False, "demo-banco: CLI-00119", str(e))
            fallas += 1
    await host.cerrar()

    ag = AgentePAIS(cfg, token=token)
    if ag.configurado and not args.sin_agente:
        try:
            res = await ag.preguntar([{"role": "user", "content": "¿El cliente CLI-00119 califica para un préstamo "
                                                                  "personal de Q50,000?"}])
            linea("SUSPEND" in res["texto"].upper(), f"Agente PAIS ({ag.nombre})",
                  f"«{res['texto'][:90]}» · {res['duracion_ms'] / 1000:.1f} s")
        except PaisNoDisponible as e:
            linea(False, f"Agente PAIS ({ag.nombre})", str(e))
            fallas += 1
    await ag.cerrar()
    await llm.cerrar()
    print(f"\n{'Todo listo.' if not fallas else f'{fallas} punto(s) por revisar (ver docs/PAIS.md).'}")
    return 0 if not fallas else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ambos", action="store_true", help="probar herramientas con stream=true y stream=false")
    p.add_argument("--sin-agente", action="store_true", help="no llamar al agente de PAIS")
    sys.exit(asyncio.run(principal(p.parse_args())))


if __name__ == "__main__":
    main()
