"""Puntos de enganche del orquestador: visión (Fase 3), STT y TTS (Fase 4).

Cada fase entrega su módulo con una fábrica; el orquestador la busca al arrancar y, si no existe,
usa una implementación «no disponible» que responde con un mensaje amable. Así ninguna fase tiene
que editar el agente. Ver docs/CONTRATO_EVENTOS.md §5.

    orchestrator/vision.py  →  crear_vision(cfg, ollama)      →  Vision
    orchestrator/voz.py     →  crear_transcriptor(cfg)        →  Transcriptor
                               crear_sintetizador(cfg)        →  Sintetizador
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("centinela.ganchos")


class GanchoNoDisponible(RuntimeError):
    """El componente (visión, STT o TTS) no está instalado o no arrancó."""


@dataclass
class Extraccion:
    tipo_documento: str
    campos: dict[str, Any]  # ya validado con Pydantic por quien implementa la visión
    modelo: str
    advertencias: list[str] = field(default_factory=list)


@dataclass
class Transcripcion:
    texto: str
    modelo: str
    idioma: str = "es"


@dataclass
class Audio:
    datos: bytes  # WAV
    formato: str = "wav"
    motor: str = "kokoro"  # kokoro | say
    duracion_ms: int | None = None


@runtime_checkable
class Vision(Protocol):
    disponible: bool

    async def extraer(self, imagen: bytes, nombre: str) -> Extraccion: ...


@runtime_checkable
class Transcriptor(Protocol):
    disponible: bool

    async def transcribir(self, audio: bytes, formato: str) -> Transcripcion: ...


@runtime_checkable
class Sintetizador(Protocol):
    disponible: bool

    async def sintetizar(self, texto: str) -> Audio: ...


class _NoDisponible:
    disponible = False

    def __init__(self, que: str):
        self.que = que

    def _fallar(self, *_: Any, **__: Any):
        raise GanchoNoDisponible(f"{self.que} no está disponible en esta instalación.")

    async def extraer(self, imagen: bytes, nombre: str) -> Extraccion:
        self._fallar()

    async def transcribir(self, audio: bytes, formato: str) -> Transcripcion:
        self._fallar()

    async def sintetizar(self, texto: str) -> Audio:
        self._fallar()


@dataclass
class Ganchos:
    vision: Vision
    transcriptor: Transcriptor
    sintetizador: Sintetizador

    @classmethod
    def vacios(cls) -> Ganchos:
        return cls(_NoDisponible("La lectura de documentos"), _NoDisponible("El reconocimiento de voz"),
                   _NoDisponible("La voz de Centinela"))


def disponible(gancho: Any) -> bool:
    """Las implementaciones reales pueden no declarar `disponible`: si la fábrica las creó, lo están."""
    return bool(getattr(gancho, "disponible", True))


def _fabrica(modulo: str, nombre: str, *args: Any) -> Any | None:
    try:
        mod = importlib.import_module(modulo)
    except ModuleNotFoundError as e:
        if e.name == modulo:  # el módulo aún no existe: la fase correspondiente no se ha integrado
            return None
        log.warning("No se pudo importar %s: %s", modulo, e)
        return None
    fabrica = getattr(mod, nombre, None)
    if fabrica is None:
        return None
    try:
        return fabrica(*args)
    except Exception as e:  # p. ej. VozNoDisponible: un componente roto no debe tumbar el orquestador
        log.warning("%s.%s no arrancó: %s", modulo, nombre, e)
        return None


def cargar(cfg: dict[str, Any], ollama: Any) -> Ganchos:
    """Busca las implementaciones reales; lo que falte queda «no disponible»."""
    g = Ganchos.vacios()
    g.vision = _fabrica("orchestrator.vision", "crear_vision", cfg, ollama) or g.vision
    g.transcriptor = _fabrica("orchestrator.voz", "crear_transcriptor", cfg) or g.transcriptor
    g.sintetizador = _fabrica("orchestrator.voz", "crear_sintetizador", cfg) or g.sintetizador
    for nombre, obj in (("visión", g.vision), ("STT", g.transcriptor), ("TTS", g.sintetizador)):
        log.info("gancho %s: %s", nombre, "disponible" if disponible(obj) else "no disponible")
    return g
