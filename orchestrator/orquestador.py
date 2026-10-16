"""Estado del orquestador: sesiones, turnos, aprobaciones pendientes, documentos y audios."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from comun import config
from orchestrator.agente import Agente, Turno
from orchestrator.eventos import VERSION_CONTRATO, Bus
from orchestrator.ganchos import Ganchos, disponible
from orchestrator.mcp_host import HostMCP
from orchestrator.memoria import Memoria
from orchestrator.ollama import Ollama

log = logging.getLogger("centinela.orquestador")

ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
EXTENSIONES_DOC = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}


class TurnoEnCurso(Exception):
    pass


class Sesion:
    def __init__(self, sesion_id: str, max_mensajes: int):
        self.id = sesion_id
        self.bus = Bus(sesion_id)
        self.memoria = Memoria(max_mensajes)
        self.tarea: asyncio.Task[None] | None = None
        self.turno: Turno | None = None
        self.contador = 0
        self.hablar = True
        self.acumulado: dict[str, Any] = {"turnos": 0, "tokens_entrada": 0, "tokens_salida": 0,
                                          "herramientas_ejecutadas": 0, "documentos_analizados": 0,
                                          "aprobaciones": {"aprobadas": 0, "rechazadas": 0}, "modelos_usados": []}

    @property
    def ocupada(self) -> bool:
        return self.tarea is not None and not self.tarea.done()


class Documentos:
    """Documentos para la visión: los de data/documentos_demo/ y los que sube la persona (en memoria)."""

    def __init__(self, carpeta_demo: Path, maximo: int = 50):
        self.carpeta_demo = carpeta_demo
        self.subidos: OrderedDict[str, tuple[str, bytes]] = OrderedDict()
        self.maximo = maximo

    def listar(self) -> list[dict[str, str]]:
        demo = []
        if self.carpeta_demo.exists():
            demo = [{"documento_id": f"demo-{p.name}", "nombre": p.name} for p in sorted(self.carpeta_demo.iterdir())
                    if p.suffix.lower() in EXTENSIONES_DOC]
        return demo + [{"documento_id": k, "nombre": v[0]} for k, v in self.subidos.items()]

    def guardar(self, nombre: str, datos: bytes) -> str:
        doc_id = f"sub-{uuid.uuid4().hex[:10]}"
        self.subidos[doc_id] = (Path(nombre).name or "documento", datos)
        while len(self.subidos) > self.maximo:
            self.subidos.popitem(last=False)
        return doc_id

    def obtener(self, doc_id: str) -> tuple[str, bytes] | None:
        if doc_id in self.subidos:
            return self.subidos[doc_id]
        if doc_id.startswith("demo-"):
            nombre = Path(doc_id[5:]).name  # sin rutas: solo archivos de la carpeta demo
            ruta = self.carpeta_demo / nombre
            if ruta.is_file() and ruta.suffix.lower() in EXTENSIONES_DOC:
                return nombre, ruta.read_bytes()
        return None


class Audios:
    def __init__(self, maximo: int = 300):
        self.datos: OrderedDict[str, bytes] = OrderedDict()
        self.maximo = maximo

    def guardar(self, datos: bytes) -> str:
        audio_id = uuid.uuid4().hex[:12]
        self.datos[audio_id] = datos
        while len(self.datos) > self.maximo:
            self.datos.popitem(last=False)
        return audio_id

    def obtener(self, audio_id: str) -> bytes | None:
        return self.datos.get(audio_id)


class Orquestador:
    def __init__(self, cfg: dict[str, Any], ollama: Ollama, host: HostMCP, ganchos: Ganchos | None = None,
                 db: Path | None = None):
        self.cfg = cfg
        self.ollama = ollama
        self.host = host
        self.ganchos = ganchos or Ganchos.vacios()
        self.db = db or config.ruta("db")
        self.usuario = cfg.get("orquestador", {}).get("usuario") or os.environ.get("CENTINELA_USUARIO",
                                                                                     "ejecutivo.demo")
        self.sesiones: dict[str, Sesion] = {}
        self.aprobaciones: dict[str, tuple[asyncio.Future[tuple[bool, str, str]], Turno]] = {}
        self.documentos = Documentos(config.ruta("documentos_demo"))
        self.audios = Audios()
        self.agente = Agente(self)
        self.fecha_demo = self._leer_fecha_demo()
        self.precalentado: dict[str, str] = {}

    def _leer_fecha_demo(self) -> str | None:
        """El «ahora» de la demo que guardó el seed (tabla meta), para que el agente entienda «este mes»."""
        if not self.db.exists():
            return None
        try:
            con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
            fila = con.execute("SELECT valor FROM meta WHERE clave='fecha_referencia'").fetchone()
            con.close()
            return fila[0][:10] if fila else None
        except sqlite3.Error:
            return None

    # ───────────── sesiones y turnos ─────────────

    def sesion(self, sesion_id: str) -> Sesion:
        if not ID_VALIDO.match(sesion_id):
            raise ValueError("sesion_id inválido")
        s = self.sesiones.get(sesion_id)
        if s is None:
            s = Sesion(sesion_id, int(self.cfg["orquestador"].get("memoria_mensajes", 40)))
            self.sesiones[sesion_id] = s
        return s

    def datos_sesion_lista(self) -> dict[str, Any]:
        return {"banco": self.cfg["demo"]["banco"], "marca_demo": self.cfg["demo"]["marca_demo"],
                "modelos": {rol: m.get("nombre") for rol, m in self.cfg["modelos"].items()},
                "herramientas": [h.para_ui() for h in self.host.herramientas.values()],
                "voz_disponible": disponible(self.ganchos.transcriptor) and disponible(self.ganchos.sintetizador),
                "vision_disponible": disponible(self.ganchos.vision), "version_contrato": VERSION_CONTRATO}

    def iniciar_turno(self, sesion: Sesion, texto: str, entrada: str = "texto", documento_id: str | None = None,
                      audio: tuple[bytes, str] | None = None) -> str:
        if sesion.ocupada:
            sesion.bus.emitir("error", {"codigo": "turno_en_curso", "etapa": None, "recuperable": True,
                                        "mensaje": "Estoy terminando la respuesta anterior. Un momento, por favor."})
            raise TurnoEnCurso()
        sesion.contador += 1
        turno_id = f"t-{sesion.contador:04d}"
        turno = Turno(self, sesion, turno_id)
        sesion.turno = turno
        sesion.tarea = asyncio.create_task(self.agente.ejecutar(turno, texto or "", entrada, documento_id, audio),
                                           name=f"turno-{sesion.id}-{turno_id}")
        return turno_id

    def cancelar(self, sesion: Sesion) -> bool:
        if sesion.ocupada and sesion.tarea is not None:
            if sesion.turno is not None:
                sesion.turno.cancelar_aprobaciones()
            sesion.tarea.cancel()
            return True
        return False

    def resolver_aprobacion(self, aprobacion_id: str, aprobar: bool, comentario: str = "") -> bool:
        entrada = self.aprobaciones.get(aprobacion_id)
        if entrada is None or entrada[0].done():
            return False
        entrada[0].set_result((bool(aprobar), comentario or ("aprobado" if aprobar else "rechazado por la persona"),
                               "persona"))
        return True

    # ───────────── consultas para la UI ─────────────

    def auditoria(self, limite: int = 20) -> list[dict[str, Any]]:
        if not self.db.exists():
            return []
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            filas = con.execute("SELECT * FROM auditoria ORDER BY id DESC LIMIT ?",
                                (max(1, min(limite, 200)),)).fetchall()
            return [dict(f) for f in filas]
        finally:
            con.close()

    async def salud(self) -> dict[str, Any]:
        return {"ollama": await self.ollama.version(), "mcp": self.host.conectado,
                "herramientas": len(self.host.herramientas),
                "voz": {"stt": disponible(self.ganchos.transcriptor), "tts": disponible(self.ganchos.sintetizador)},
                "vision": disponible(self.ganchos.vision),
                "modelos": {rol: m.get("nombre") for rol, m in self.cfg["modelos"].items()},
                "fecha_demo": self.fecha_demo, "precalentado": self.precalentado}

    async def precalentar(self, timeout_s: float = 180) -> dict[str, str]:
        """Carga Whisper y Kokoro en ESTE proceso y hace una inferencia corta con cada uno, para que el primer
        turno de voz en escena no pague la carga. Lo llaman el arranque de la API y `make warmup`."""
        estado: dict[str, str] = {}
        for nombre, gancho in (("stt", self.ganchos.transcriptor), ("tts", self.ganchos.sintetizador)):
            if not disponible(gancho) or not hasattr(gancho, "precalentar"):
                estado[nombre] = "no disponible"
            else:
                t = time.perf_counter()
                try:
                    await asyncio.wait_for(gancho.precalentar(), timeout_s)
                    motor = getattr(gancho, "motor", None)
                    ms = int((time.perf_counter() - t) * 1000)
                    estado[nombre] = f"ok · {motor} · {ms} ms" if motor else f"ok · {ms} ms"
                except Exception as e:  # informa; un componente roto no tumba la API
                    estado[nombre] = f"error: {type(e).__name__}: {e}"
                    log.warning("precalentado de %s falló: %s", nombre, e)
            self.precalentado = {**self.precalentado, nombre: estado[nombre]}
        log.info("precalentado de voz: %s", estado)
        return estado

    async def cerrar(self) -> None:
        for s in self.sesiones.values():
            self.cancelar(s)
        await self.host.cerrar()
        await self.ollama.cerrar()
