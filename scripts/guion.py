"""Corre las preguntas del guion (config.yaml → guion) contra el orquestador real, dentro de este proceso.

Lo usan `make warmup` (una pasada), `make ensayo` (N corridas y criterios de aceptación), `make medir`
(comparar modelos agente) y `make check-offline`. Siempre trabaja sobre una COPIA de data/banco.db, así los
tickets de prueba no ensucian la base de la demo, y aprueba solo las escrituras (como haría la persona).

Cada paso del guion acepta:

    id, pregunta                 obligatorios
    documento_id                 opcional: «demo-<archivo>» de data/documentos_demo/ (acto de visión)
    herramientas_esperadas       el paso falla si el agente no usó todas
    respuesta_contiene           opcional: la respuesta debe mencionar al menos una (sin importar tildes)
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator import ganchos as mod_ganchos
from orchestrator.ganchos import Ganchos
from orchestrator.mcp_host import HostMCP, parametros_stdio
from orchestrator.ollama import Ollama
from orchestrator.orquestador import Orquestador


@dataclass
class ResultadoPaso:
    id: str
    ok: bool
    problemas: list[str] = field(default_factory=list)
    total_ms: int = 0
    ttft_ms: int = 0
    tokens_por_s: float = 0.0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    stt_ms: int | None = None
    primer_audio_ms: int | None = None   # desde que llega la pregunta (o el audio) hasta el primer audio listo
    herramientas: list[str] = field(default_factory=list)
    modelos: list[str] = field(default_factory=list)
    respuesta: str = ""
    citas: int = 0


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower()) if unicodedata.category(c) != "Mn")


def percentil(valores: list[int | float], p: float) -> int:
    """Percentil por rango más cercano (suficiente para 10-50 muestras)."""
    v = sorted(x for x in valores if x is not None)
    if not v:
        return 0
    return int(v[min(len(v) - 1, int(round(p * (len(v) - 1))))])


def evaluar(paso: dict[str, Any], eventos: list[dict[str, Any]]) -> list[str]:
    """Problemas del turno frente a lo que el guion espera. Lista vacía = paso exitoso."""
    problemas: list[str] = []
    fin = next((e["datos"] for e in eventos if e["tipo"] == "turno.fin"), None)
    if fin is None:
        return ["el turno no terminó"]
    if not fin["ok"]:
        err = next((e["datos"] for e in reversed(eventos) if e["tipo"] == "error"), {})
        problemas.append(f"error {err.get('codigo', '?')}: {err.get('mensaje', 'sin detalle')}")
    usadas = set(fin["metricas"]["herramientas"])
    faltaron = [h for h in paso.get("herramientas_esperadas", []) if h not in usadas]
    if faltaron:
        problemas.append("no usó " + ", ".join(faltaron))
    for f in (e["datos"] for e in eventos if e["tipo"] == "herramienta.fin"):
        if not f["ok"] and f["nombre"] in paso.get("herramientas_esperadas", []):
            problemas.append(f"{f['nombre']} falló: {f.get('resumen', '')}"[:160])
    if "buscar_politicas" in paso.get("herramientas_esperadas", []) and not any(
            e["tipo"] == "citas" for e in eventos):
        problemas.append("sin citas de política")
    claves = paso.get("respuesta_contiene") or []
    if claves and fin["ok"]:
        final = next((e["datos"].get("texto", "") for e in eventos if e["tipo"] == "respuesta.final"), "")
        if not any(sin_tildes(c) in sin_tildes(final) for c in claves):
            problemas.append("la respuesta no menciona " + " / ".join(claves))
    return problemas


async def correr_paso(orq: Orquestador, sesion: Any, cola: asyncio.Queue, paso: dict[str, Any],
                      audio: tuple[bytes, str] | None = None, aprobar: bool = True,
                      timeout_s: float = 600) -> ResultadoPaso:
    """Un turno completo. Aprueba (o rechaza) las escrituras y mide el primer audio si la sesión habla."""
    documento_id = paso.get("documento_id")
    entrada = "voz" if audio else ("documento" if documento_id else "texto")
    t0 = time.perf_counter()
    orq.iniciar_turno(sesion, "" if audio else paso["pregunta"], entrada, documento_id, audio)
    eventos: list[dict[str, Any]] = []
    primer_audio_ms = None
    limite = t0 + timeout_s
    while True:
        e = await asyncio.wait_for(cola.get(), max(1.0, limite - time.perf_counter()))
        eventos.append(e)
        if e["tipo"] == "aprobacion.solicitada":
            orq.resolver_aprobacion(e["datos"]["aprobacion_id"], aprobar, "ensayo automático")
        elif e["tipo"] == "voz.audio" and primer_audio_ms is None:
            primer_audio_ms = int((time.perf_counter() - t0) * 1000)
        elif e["tipo"] == "turno.fin":
            break
    if sesion.tarea is not None:
        await sesion.tarea
    fin = eventos[-1]["datos"]
    m = fin["metricas"]
    problemas = evaluar(paso, eventos)
    return ResultadoPaso(
        id=paso["id"], ok=not problemas, problemas=problemas, total_ms=m["latencias_ms"]["total"],
        ttft_ms=m["ttft_ms"] or 0, tokens_por_s=m["tokens_por_s"], tokens_entrada=m["tokens_entrada"],
        tokens_salida=m["tokens_salida"], stt_ms=m["latencias_ms"].get("stt") if audio else None,
        primer_audio_ms=primer_audio_ms, herramientas=list(m["herramientas"]), modelos=list(m["modelos"]),
        respuesta=next((x["datos"].get("texto", "") for x in eventos if x["tipo"] == "respuesta.final"), ""),
        citas=sum(len(x["datos"].get("citas", [])) for x in eventos if x["tipo"] == "citas"))


async def correr_guion(orq: Orquestador, pasos: list[dict[str, Any]], sesion_id: str = "ensayo", *,
                       hablar: bool = False, audios: dict[str, tuple[bytes, str]] | None = None,
                       al_terminar_paso: Any = None) -> list[ResultadoPaso]:
    """Todas las preguntas en orden, en una misma sesión (las preguntas 2-4 dependen de la 1)."""
    sesion = orq.sesion(sesion_id)
    sesion.hablar = hablar
    cola = sesion.bus.suscribir()
    resultados = []
    try:
        for paso in pasos:
            r = await correr_paso(orq, sesion, cola, paso, (audios or {}).get(paso["id"]))
            resultados.append(r)
            if al_terminar_paso:
                al_terminar_paso(r)
    finally:
        sesion.bus.desuscribir(cola)
    return resultados


def crear_orquestador(cfg: dict[str, Any], db: Path, *, vision: bool = True, voz: bool = False,
                      ollama: Ollama | None = None, host: HostMCP | None = None,
                      log_mcp: str = "WARNING") -> Orquestador:
    """Orquestador real contra `db`. El servidor MCP hijo recibe por entorno la ruta de la copia y su nivel de
    log (en WARNING, para que el reporte del script se lea limpio)."""
    ollama = ollama or Ollama(cfg)
    if host is None:
        params = parametros_stdio(cfg)
        entorno = {**(params.env or {}), "CENTINELA__rutas__db": str(db), "CENTINELA_LOG": log_mcp}
        host = HostMCP(params.model_copy(update={"env": entorno}))
    if voz:
        g = mod_ganchos.cargar(cfg, ollama)
    else:  # sin cargar Whisper ni Kokoro: no hacen falta y ocupan memoria
        g = Ganchos.vacios()
        if vision:
            g.vision = mod_ganchos._fabrica("orchestrator.vision", "crear_vision", cfg, ollama) or g.vision
    return Orquestador(cfg, ollama, host, g, db=db)


@contextmanager
def copia_db(origen: Path) -> Iterator[Path]:
    if not origen.exists():
        raise SystemExit(f"No existe {origen}: ejecute `make seed` primero.")
    with tempfile.TemporaryDirectory(prefix="centinela-") as tmp:
        db = Path(tmp) / "banco.db"
        shutil.copy(origen, db)
        yield db


def ultimo_id_auditoria(db: Path) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT COALESCE(MAX(id), 0) FROM auditoria").fetchone()[0])
    finally:
        con.close()


def auditoria_desde(db: Path, desde_id: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(f) for f in con.execute("SELECT herramienta, estado, modelo FROM auditoria WHERE id > ? "
                                             "ORDER BY id", (desde_id,))]
    finally:
        con.close()


def revisar_auditoria(resultados: list[ResultadoPaso], filas: list[dict[str, Any]]) -> list[str]:
    """Toda llamada a herramienta quedó en `auditoria`, con el modelo que la pidió."""
    problemas = []
    llamadas = sorted(h for r in resultados for h in r.herramientas)
    auditadas = sorted(f["herramienta"] for f in filas)
    if llamadas != auditadas:
        problemas.append(f"{len(llamadas)} llamadas a herramientas y {len(auditadas)} filas en auditoría")
    if any(not f.get("modelo") or f["modelo"].startswith("sin modelo") for f in filas):
        problemas.append("hay filas de auditoría sin el modelo que pidió la herramienta")
    return problemas
