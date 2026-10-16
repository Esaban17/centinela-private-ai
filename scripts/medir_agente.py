"""Mide candidatos a modelo agente con las preguntas del guion (config.yaml → guion) en la Mac.

    python -m scripts.medir_agente --corridas 5
    python -m scripts.medir_agente --modelos qwen3.6:35b-a3b-q4_K_M gemma4:26b-a4b-it-qat --corridas 10

Por cada modelo y corrida abre una sesión nueva, hace las preguntas en orden (aprobando las escrituras)
y revisa que el agente haya usado las herramientas esperadas. Trabaja sobre una COPIA de data/banco.db,
así los tickets de prueba no ensucian la base de la demo. Reporta tasa de éxito y latencias p50/p95.
`scripts/ensayo.py` (Fase 6) amplía esto con voz, visión y criterios de aceptación.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import statistics
import tempfile
from pathlib import Path
from typing import Any

from comun import config
from orchestrator import registro
from orchestrator.mcp_host import HostMCP, parametros_stdio
from orchestrator.ollama import Ollama
from orchestrator.orquestador import Orquestador


def percentil(valores: list[int], p: float) -> int:
    if not valores:
        return 0
    v = sorted(valores)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


async def corrida(cfg: dict[str, Any], db: Path, n: int) -> list[dict[str, Any]]:
    os.environ["CENTINELA__rutas__db"] = str(db)
    orq = Orquestador(cfg, Ollama(cfg), HostMCP(parametros_stdio(cfg)), db=db)
    await orq.host.conectar()
    sesion = orq.sesion(f"medicion-{n}")
    sesion.hablar = False
    cola = sesion.bus.suscribir()
    resultados = []
    try:
        for paso in cfg.get("guion", []):
            orq.iniciar_turno(sesion, paso["pregunta"])
            fin: dict[str, Any] = {}
            while True:
                e = await cola.get()
                if e["tipo"] == "aprobacion.solicitada":
                    orq.resolver_aprobacion(e["datos"]["aprobacion_id"], True, "medición automática")
                elif e["tipo"] == "turno.fin":
                    fin = e["datos"]
                    break
            await sesion.tarea
            usadas = set(fin["metricas"]["herramientas"])
            esperadas = set(paso.get("herramientas_esperadas", []))
            m = fin["metricas"]
            resultados.append({"id": paso["id"], "ok": fin["ok"] and esperadas <= usadas,
                               "faltaron": sorted(esperadas - usadas), "total_ms": m["latencias_ms"]["total"],
                               "ttft_ms": m["ttft_ms"] or 0, "tps": m["tokens_por_s"]})
    finally:
        await orq.cerrar()
    return resultados


async def principal(args: argparse.Namespace) -> None:
    base = config.cargar()
    modelos = args.modelos or [base["modelos"]["agente"]["nombre"]]
    origen = config.ruta("db")
    if not origen.exists():
        raise SystemExit("No existe data/banco.db: ejecute `make seed` primero.")
    for modelo in modelos:
        cfg = {**base, "modelos": {**base["modelos"], "agente": {**base["modelos"]["agente"], "nombre": modelo}}}
        filas: list[dict[str, Any]] = []
        for n in range(args.corridas):
            with tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "banco.db"
                shutil.copy(origen, db)
                filas += await corrida(cfg, db, n)
            print(f"  {modelo} · corrida {n + 1}/{args.corridas}", flush=True)
        print(f"\n=== {modelo} ===")
        for pid in [p["id"] for p in base.get("guion", [])]:
            r = [f for f in filas if f["id"] == pid]
            exitos = sum(f["ok"] for f in r)
            faltas = sorted({h for f in r for h in f["faltaron"]})
            tiempos = [f["total_ms"] for f in r]
            print(f"  {pid:10s} éxito {exitos}/{len(r)} ({100 * exitos // max(1, len(r))} %) · "
                  f"p50 {percentil(tiempos, .5)} ms · p95 {percentil(tiempos, .95)} ms"
                  + (f" · faltó: {', '.join(faltas)}" if faltas else ""))
        total = sum(f["ok"] for f in filas)
        tps = [f["tps"] for f in filas if f["tps"]]
        print(f"  TOTAL      éxito {total}/{len(filas)} ({100 * total // max(1, len(filas))} %) · "
              f"primer token p50 {percentil([f['ttft_ms'] for f in filas], .5)} ms · "
              f"{statistics.median(tps) if tps else 0} tok/s\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--modelos", nargs="*", help="etiquetas de Ollama (por defecto, el agente de config.yaml)")
    p.add_argument("--corridas", type=int, default=3)
    args = p.parse_args()
    registro.configurar("WARNING")
    asyncio.run(principal(args))


if __name__ == "__main__":
    main()
