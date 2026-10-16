"""Ensayo automatizado del guion (`make ensayo`): corre las preguntas N veces y revisa los criterios de aceptación.

    python -m scripts.ensayo                         # config.yaml → ensayo.corridas (10)
    python -m scripts.ensayo --corridas 3 --voz      # entrada por voz: TTS de cada pregunta → STT → agente → TTS
    python -m scripts.ensayo --modelos qwen3.6:35b-a3b-q4_K_M gemma4:26b-a4b-it-qat --corridas 5   # comparar

Cada corrida abre una sesión nueva sobre una COPIA de data/banco.db, hace las preguntas en orden (aprobando
los tickets) y revisa por pregunta: que el agente usó las herramientas esperadas, que no hubo error, las citas y
lo que la respuesta debe mencionar. Además vigila durante todo el ensayo:

- conexiones salientes de los procesos de la demo (el mismo monitor del panel) y la guardia de salida;
- que cada llamada a herramienta haya quedado en `auditoria` con el modelo que la pidió.

Criterios (config.yaml → ensayo): ≥ 90 % de éxito por pregunta, 0 conexiones salientes, auditoría completa y,
con --voz, primer audio ≤ 5 s. Imprime p50/p95 y deja el detalle en salida/ensayos/<fecha>.json.

Con --voz el ensayo carga su propio Whisper y Kokoro: córralo con el orquestador apagado para no duplicar memoria.
El «primer audio» se mide desde que el orquestador recibe el audio; en el navegador se suma la subida (local).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from typing import Any

from comun import config, salida
from orchestrator import registro
from orchestrator.soberania.monitor import MonitorSoberania
from scripts.guion import (ResultadoPaso, auditoria_desde, copia_db, correr_guion, crear_orquestador, percentil,
                           revisar_auditoria, ultimo_id_auditoria)


class Vigia:
    """Muestra el monitor de conexiones cada `intervalo` s mientras corre el ensayo."""

    def __init__(self, cfg: dict[str, Any], intervalo: float = 1.0):
        self.monitor = MonitorSoberania(cfg, pid_raiz=os.getpid())
        self.intervalo = intervalo
        self.muestras = 0
        self._tarea: asyncio.Task | None = None

    async def _bucle(self) -> None:
        while True:
            await asyncio.to_thread(self.monitor.instantanea)
            self.muestras += 1
            await asyncio.sleep(self.intervalo)

    def __enter__(self) -> Vigia:
        self._tarea = asyncio.create_task(self._bucle())
        return self

    def __exit__(self, *_: Any) -> None:
        if self._tarea:
            self._tarea.cancel()
        self.monitor.instantanea()  # una última muestra al terminar
        self.muestras += 1

    @property
    def max_salientes(self) -> int:
        return self.monitor.max_salientes

    @property
    def detalle(self) -> list[dict[str, Any]]:
        return self.monitor.historial_salientes


async def audios_del_guion(cfg: dict[str, Any], pasos: list[dict[str, Any]]) -> dict[str, tuple[bytes, str]]:
    """La pregunta de cada paso dicha por Kokoro: entra al orquestador como si fuera el micrófono."""
    from orchestrator import voz

    tts = voz.crear_sintetizador(cfg)
    audios = {}
    for paso in pasos:
        if not paso.get("documento_id"):
            audios[paso["id"]] = ((await tts.sintetizar(paso["pregunta"])).datos, "wav")
    return audios


def resumir(pasos: list[dict[str, Any]], filas: list[ResultadoPaso]) -> list[dict[str, Any]]:
    out = []
    for paso in pasos:
        r = [f for f in filas if f.id == paso["id"]]
        exitos = sum(f.ok for f in r)
        audio = [f.primer_audio_ms for f in r if f.primer_audio_ms is not None]
        out.append({
            "id": paso["id"], "corridas": len(r), "exitos": exitos, "tasa": exitos / len(r) if r else 0.0,
            "total_p50_ms": percentil([f.total_ms for f in r], .5), "total_p95_ms": percentil([f.total_ms for f in r], .95),
            "ttft_p50_ms": percentil([f.ttft_ms for f in r], .5),
            "primer_audio_p50_ms": percentil(audio, .5) if audio else None,
            "primer_audio_p95_ms": percentil(audio, .95) if audio else None,
            "stt_p50_ms": percentil([f.stt_ms for f in r if f.stt_ms is not None], .5) if audio else None,
            "problemas": Counter(p for f in r for p in f.problemas).most_common(3),
        })
    return out


def criterios(cfg: dict[str, Any], resumen: list[dict[str, Any]], max_salientes: int, bloqueos: int,
              auditoria: list[str], voz: bool) -> list[tuple[str, str, str]]:
    """(estado, criterio, detalle) con estado ok | aviso | falla."""
    e = cfg.get("ensayo") or {}
    minimo = float(e.get("exito_minimo", 0.9))
    out = []
    bajos = [f"{r['id']} {r['tasa']:.0%}" for r in resumen if r["tasa"] < minimo]
    out.append(("falla" if bajos else "ok", f"éxito ≥ {minimo:.0%} en cada pregunta",
                ", ".join(bajos) if bajos else "todas las preguntas"))
    out.append(("falla" if max_salientes else "ok", "0 conexiones salientes durante el ensayo",
                f"máximo observado: {max_salientes}"))
    if bloqueos:
        out.append(("falla", "guardia de salida sin intentos bloqueados", f"{bloqueos} intentos (ver log)"))
    out.append(("falla" if auditoria else "ok", "toda llamada a herramienta queda en auditoría",
                "; ".join(sorted(set(auditoria))) if auditoria else "completa en todas las corridas"))
    if voz:
        tope = int(e.get("primer_audio_max_ms", 5000))
        p95 = [r["primer_audio_p95_ms"] for r in resumen if r["primer_audio_p95_ms"] is not None]
        p50 = [r["primer_audio_p50_ms"] for r in resumen if r["primer_audio_p50_ms"] is not None]
        if p95:
            out.append(("ok" if max(p95) <= tope else "aviso", f"primer audio ≤ {tope / 1000:.0f} s (objetivo)",
                        f"p50 {statistics.median(p50) / 1000:.1f} s · peor p95 {max(p95) / 1000:.1f} s"))
        else:
            out.append(("aviso", "primer audio", "no se generó audio: ¿TTS disponible?"))
    return out


async def ensayar_modelo(cfg: dict[str, Any], args: argparse.Namespace,
                         audios: dict[str, tuple[bytes, str]] | None) -> dict[str, Any]:
    pasos = [p for p in cfg.get("guion", []) if not args.solo or p["id"] in args.solo]
    filas: list[ResultadoPaso] = []
    problemas_auditoria: list[str] = []
    bloqueos_antes = len(salida.bloqueos())
    with Vigia(cfg) as vigia:
        for n in range(args.corridas):
            with copia_db(config.ruta("db", cfg)) as db:
                orq = crear_orquestador(cfg, db, voz=bool(audios))
                desde = ultimo_id_auditoria(db)
                try:
                    await orq.host.conectar()
                    t = time.perf_counter()
                    r = await correr_guion(orq, pasos, f"ensayo-{n + 1}", hablar=bool(audios), audios=audios)
                finally:
                    await orq.cerrar()
                problemas_auditoria += revisar_auditoria(r, auditoria_desde(db, desde))
            filas += r
            fallas = [f"{x.id}: {'; '.join(x.problemas)}" for x in r if not x.ok]
            print(f"  corrida {n + 1}/{args.corridas} · {sum(x.ok for x in r)}/{len(r)} ok · "
                  f"{time.perf_counter() - t:.0f} s" + (f" · {' | '.join(fallas)}" if fallas else ""), flush=True)
    resumen = resumir(pasos, filas)
    return {"modelo": cfg["modelos"]["agente"]["nombre"], "resumen": resumen,
            "criterios": criterios(cfg, resumen, vigia.max_salientes, len(salida.bloqueos()) - bloqueos_antes,
                                   problemas_auditoria, bool(audios)),
            "conexiones_salientes": vigia.detalle, "muestras_monitor": vigia.muestras,
            "tokens_por_s_p50": statistics.median([f.tokens_por_s for f in filas if f.tokens_por_s] or [0]),
            "corridas": [asdict(f) for f in filas]}


def imprimir(rep: dict[str, Any], voz: bool) -> None:
    print(f"\n=== {rep['modelo']} ===")
    print(f"  {'pregunta':10s} {'éxito':>11s} {'p50':>8s} {'p95':>8s} {'1.er token':>10s}"
          + (f" {'STT':>7s} {'1.er audio':>10s}" if voz else ""))
    for r in rep["resumen"]:
        linea = (f"  {r['id']:10s} {r['exitos']:>3d}/{r['corridas']:<3d}{r['tasa']:>4.0%} "
                 f"{r['total_p50_ms'] / 1000:>7.1f}s {r['total_p95_ms'] / 1000:>7.1f}s {r['ttft_p50_ms'] / 1000:>9.1f}s")
        if voz:
            stt = f"{r['stt_p50_ms'] / 1000:.1f}s" if r["stt_p50_ms"] is not None else "—"
            pa = f"{r['primer_audio_p50_ms'] / 1000:.1f}s" if r["primer_audio_p50_ms"] is not None else "—"
            linea += f" {stt:>7s} {pa:>10s}"
        print(linea)
        for problema, veces in r["problemas"]:
            print(f"  {'':10s}   ↳ {veces}× {problema}")
    print(f"  tokens/s del agente (p50): {rep['tokens_por_s_p50']}")
    print("\n  Criterios de aceptación")
    for estado, nombre, detalle in rep["criterios"]:
        print(f"  {'✅' if estado == 'ok' else '⚠️ ' if estado == 'aviso' else '❌'} {nombre} — {detalle}")


async def principal(args: argparse.Namespace) -> int:
    base = config.cargar()
    modelos = args.modelos or [base["modelos"]["agente"]["nombre"]]
    audios = None
    if args.voz:
        print("Generando el audio de cada pregunta con la voz local…", flush=True)
        audios = await audios_del_guion(base, base.get("guion", []))
    reportes = []
    for modelo in modelos:
        cfg = {**base, "modelos": {**base["modelos"], "agente": {**base["modelos"]["agente"], "nombre": modelo}}}
        print(f"\nEnsayo de {modelo}: {args.corridas} corridas del guion", flush=True)
        rep = await ensayar_modelo(cfg, args, audios)
        imprimir(rep, bool(audios))
        reportes.append(rep)
    carpeta = config.RAIZ / "salida" / "ensayos"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / f"ensayo-{datetime.now():%Y%m%d-%H%M%S}.json"
    archivo.write_text(json.dumps({"fecha": datetime.now().isoformat(" ", "seconds"), "voz": bool(audios),
                                   "reportes": reportes}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nDetalle: {archivo.relative_to(config.RAIZ)}")
    return 1 if any(estado == "falla" for r in reportes for estado, *_ in r["criterios"]) else 0


def argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corridas", type=int, default=int((config.cargar().get("ensayo") or {}).get("corridas", 10)))
    p.add_argument("--modelos", nargs="*", help="etiquetas de Ollama para el agente (por defecto, el de config.yaml)")
    p.add_argument("--voz", action="store_true", help="entrada y salida por voz (mide STT y primer audio)")
    p.add_argument("--solo", nargs="*", help="ids del guion a ensayar (por defecto, todos)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = argumentos(argv)
    registro.configurar("WARNING")
    salida.instalar(config.cargar())
    sys.exit(asyncio.run(principal(args)))


if __name__ == "__main__":
    main()
