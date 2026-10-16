"""Centinela en la terminal (sin UI): sirve para probar el agente en texto y para mostrar «qué pasa por dentro».

    python -m orchestrator.cli                      # conversación interactiva
    python -m orchestrator.cli "¿Hay alertas críticas?"
    python -m orchestrator.cli --modelo gemma4:26b-a4b-it-qat

Las aprobaciones se responden en la terminal (s/n).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from comun import config
from orchestrator import ganchos as mod_ganchos
from orchestrator import registro
from orchestrator.mcp_host import HostMCP, parametros_stdio
from orchestrator.ollama import Ollama
from orchestrator.orquestador import Orquestador

GRIS, VERDE, AMARILLO, ROJO, AZUL, FIN = "\033[90m", "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[0m"


def _corto(valor: Any, n: int = 160) -> str:
    texto = json.dumps(valor, ensure_ascii=False, default=str) if not isinstance(valor, str) else valor
    return texto if len(texto) <= n else texto[:n] + "…"


async def _pintar(orq: Orquestador, cola: asyncio.Queue, auto: str | None) -> None:
    while True:
        e = await cola.get()
        t, d = e["tipo"], e["datos"]
        if t == "modelo.uso":
            print(f"{GRIS}  [{d['rol']}] {d['modelo']} · {d['tarea']}{FIN}")
        elif t == "llm.delta":
            print(d["texto"], end="", flush=True)
        elif t == "herramienta.inicio":
            print(f"\n{AZUL}  → {d['nombre']}({_corto(d['argumentos'])}){FIN}")
        elif t == "herramienta.fin":
            color = VERDE if d["ok"] else ROJO
            print(f"{color}  ← {d['nombre']} · {d['duracion_ms']} ms · {d['resumen']}{FIN}")
        elif t == "aprobacion.solicitada":
            print(f"\n{AMARILLO}{d['mensaje']}{FIN}")
            if auto is not None:
                decision = auto == "aprobar"
            else:
                r = await asyncio.to_thread(input, f"{AMARILLO}¿Aprueba? [s/N]: {FIN}")
                decision = r.strip().lower() in {"s", "si", "sí", "y"}
            orq.resolver_aprobacion(d["aprobacion_id"], decision, "desde la terminal")
        elif t == "aprobacion.resuelta":
            print(f"{AMARILLO}  aprobación: {'APROBADA' if d['aprobada'] else 'rechazada'} ({d['origen']}){FIN}")
        elif t == "citas":
            for c in d["citas"]:
                print(f"{GRIS}  cita: {c['documento']} § {c['seccion']}{FIN}")
        elif t in ("error", "aviso"):
            print(f"\n{ROJO}  {t}: {d['mensaje']}{FIN}")
        elif t == "respuesta.final" and d.get("resumen_hablado"):
            print(f"\n{GRIS}  (hablado) {d['resumen_hablado']}{FIN}")
        elif t == "turno.fin":
            m = d["metricas"]
            lat = m["latencias_ms"]
            print(f"{GRIS}  ── {lat['total']} ms · primer token {m['ttft_ms']} ms · {m['tokens_entrada']}→"
                  f"{m['tokens_salida']} tokens · {m['tokens_por_s']} tok/s · {', '.join(m['modelos'])}{FIN}\n")


async def principal(args: argparse.Namespace) -> None:
    cfg = config.cargar()
    if args.modelo:
        cfg["modelos"]["agente"]["nombre"] = args.modelo
    ollama = Ollama(cfg)
    orq = Orquestador(cfg, ollama, HostMCP(parametros_stdio(cfg)), mod_ganchos.cargar(cfg, ollama))
    orq.sesion("terminal").hablar = False
    await orq.host.conectar()
    sesion = orq.sesion("terminal")
    pintor = asyncio.create_task(_pintar(orq, sesion.bus.suscribir(), args.auto))
    print(f"Centinela · {cfg['demo']['banco']} · {cfg['demo']['marca_demo']} · agente {ollama.modelo('agente')}")
    try:
        preguntas = [" ".join(args.pregunta)] if args.pregunta else None
        while True:
            if preguntas is not None:
                if not preguntas:
                    break
                texto = preguntas.pop(0)
                print(f"> {texto}")
            else:
                try:
                    texto = await asyncio.to_thread(input, "> ")
                except EOFError:
                    break
                if texto.strip() in {"salir", "exit", "quit"}:
                    break
                if not texto.strip():
                    continue
            orq.iniciar_turno(sesion, texto)
            await sesion.tarea
            await asyncio.sleep(0.05)  # que el pintor vacíe la cola
    finally:
        pintor.cancel()
        await orq.cerrar()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pregunta", nargs="*", help="pregunta única (si no, modo interactivo)")
    p.add_argument("--modelo", help="modelo agente a usar en lugar del de config.yaml")
    p.add_argument("--auto", choices=["aprobar", "rechazar"], help="responder aprobaciones sin preguntar")
    p.add_argument("--log", default="WARNING", help="nivel de log en stderr (INFO muestra el detalle enmascarado)")
    args = p.parse_args()
    registro.configurar(args.log)
    try:
        asyncio.run(principal(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
