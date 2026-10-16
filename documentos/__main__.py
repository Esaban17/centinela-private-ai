"""Prueba de la visión documental desde la terminal (sin orquestador):

    python -m documentos data/documentos_demo/boleta_deposito_discrepancia.png
    python -m documentos --todos            # los documentos demo, comparados con manifiesto.json

Extrae los campos con el modelo de visión local y los valida contra data/banco.db.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from comun import config
from documentos.validacion import validar
from mcp_server.db import conectar_lectura
from orchestrator.vision import crear_vision


async def _uno(vision, con, ruta: Path, tipo: str | None) -> dict:
    t0 = time.perf_counter()
    ext = await vision.extraer(ruta.read_bytes(), ruta.name, tipo=tipo)
    res = validar(con, ext.tipo_documento, ext.campos)
    return {"archivo": ruta.name, "extraccion": ext.a_dict(), "validacion": res,
            "segundos": round(time.perf_counter() - t0, 1)}


async def main() -> None:
    p = argparse.ArgumentParser(description="Extrae y valida documentos con el modelo de visión local.")
    p.add_argument("imagen", nargs="?", type=Path)
    p.add_argument("--tipo", choices=["boleta_deposito", "solicitud_credito", "factura"])
    p.add_argument("--todos", action="store_true", help="procesa data/documentos_demo/ y compara con el manifiesto")
    args = p.parse_args()
    cfg = config.cargar()
    vision = crear_vision(cfg)
    con = conectar_lectura(config.ruta("db"))
    if not args.todos:
        if args.imagen is None:
            p.error("indique una imagen o --todos")
        print(json.dumps(await _uno(vision, con, args.imagen, args.tipo), ensure_ascii=False, indent=2))
        return
    carpeta = config.ruta("documentos_demo")
    manifiesto = json.loads((carpeta / "manifiesto.json").read_text(encoding="utf-8"))
    aciertos = 0
    for nombre, esperado in manifiesto["documentos"].items():
        r = await _uno(vision, con, carpeta / nombre, None)
        campos, ok_campos = r["extraccion"]["campos"], []
        for clave, valor in esperado["campos"].items():
            if campos.get(clave) != valor:
                ok_campos.append(f"{clave}: leyó {campos.get(clave)!r}, esperado {valor!r}")
        bien = r["validacion"]["veredicto"] == esperado["veredicto_esperado"]
        aciertos += bien
        print(f"{'✓' if bien else '✗'} {nombre}: {r['validacion']['veredicto']} ({r['segundos']} s)")
        for d in ok_campos:
            print(f"    campo distinto · {d}")
    print(f"{aciertos}/{len(manifiesto['documentos'])} veredictos correctos.")


if __name__ == "__main__":
    asyncio.run(main())
