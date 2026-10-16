"""Ganchos de voz para el orquestador (Fase 4). Ver docs/CONTRATO_EVENTOS.md §5 y docs/VOZ.md.

El orquestador busca aquí dos fábricas:

    transcriptor = crear_transcriptor(cfg)   # await transcriptor.transcribir(audio: bytes, formato: str) -> Transcripcion
    sintetizador = crear_sintetizador(cfg)   # await sintetizador.sintetizar(texto: str) -> Audio

Ambas son async por fuera y corren el modelo en un hilo, para no bloquear el loop de FastAPI. Si el motor no
está disponible, la fábrica lanza `VozNoDisponible` (el orquestador sigue con su implementación «no
disponible» y emite `voz_no_disponible`).

Extras para el orquestador, opcionales:

- `sintetizador.locutor()` → `Locutor`: alimentarlo con `llm.delta` para hablar mientras escribe.
- `partir_en_oraciones(texto)` y `normalizar_para_voz(texto)`.
- `resumen_hablado(respuesta, cfg, generar=...)` con el modelo rápido y recorte de respaldo.
- `precalentar_voz(cfg)` para `make warmup`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from orchestrator.voz_local import opciones as _opciones
from orchestrator.voz_local.audio import AudioInvalido, duracion_wav_ms
from orchestrator.voz_local.locutor import FragmentoVoz, Locutor
from orchestrator.voz_local.resumen import (INSTRUCCION_CAMPO_RESUMEN, PROMPT_RESUMEN_HABLADO, ResumenHablado,
                                            extraer_campo_resumen)
from orchestrator.voz_local.resumen import resumen_hablado as _resumen_hablado
from orchestrator.voz_local.stt import STTNoDisponible, Transcriptor, crear_motor_stt
from orchestrator.voz_local.texto import SegmentadorOraciones, normalizar_para_voz, partir_en_oraciones
from orchestrator.voz_local.tts import TTSConRespaldo, TTSNoDisponible, crear_tts

__all__ = [
    "Audio", "AudioInvalido", "FragmentoVoz", "INSTRUCCION_CAMPO_RESUMEN", "Locutor", "PROMPT_RESUMEN_HABLADO",
    "ResumenHablado", "SegmentadorOraciones", "SintetizadorLocal", "Transcripcion", "TranscriptorLocal",
    "VozNoDisponible", "crear_sintetizador", "crear_transcriptor", "duracion_wav_ms", "extraer_campo_resumen",
    "normalizar_para_voz", "partir_en_oraciones", "precalentar_voz", "resumen_hablado",
]


class VozNoDisponible(RuntimeError):
    """No hay motor de STT o TTS utilizable en esta máquina."""


# Tipos del contrato. Si el orquestador ya definió los suyos en orchestrator/ganchos.py, se usan esos.
try:  # pragma: no cover - depende de que la Fase 2 esté presente
    from orchestrator.ganchos import Audio as _AudioContrato, Transcripcion as _TranscripcionContrato  # type: ignore
except ImportError:  # pragma: no cover
    _AudioContrato = _TranscripcionContrato = None


@dataclass
class _Transcripcion:
    texto: str
    modelo: str
    idioma: str


@dataclass
class _Audio:
    datos: bytes
    formato: str
    motor: str


def _tipo_concreto(contrato, propio):
    # Si ganchos.py define el tipo como Protocol (no instanciable), se usa el dataclass propio, que lo cumple.
    if contrato is None or getattr(contrato, "_is_protocol", False):
        return propio
    return contrato


Transcripcion = _tipo_concreto(_TranscripcionContrato, _Transcripcion)
Audio = _tipo_concreto(_AudioContrato, _Audio)


def _construir(tipo, **valores):
    """Crea el tipo del contrato pasando solo los campos que acepta (tolera versiones con menos campos)."""
    campos = getattr(tipo, "__dataclass_fields__", None) or getattr(tipo, "model_fields", None)
    if campos:
        valores = {k: v for k, v in valores.items() if k in campos}
    return tipo(**valores)


class TranscriptorLocal:
    """STT para el orquestador: `await transcribir(audio, formato)`."""

    def __init__(self, transcriptor: Transcriptor):
        self._t = transcriptor
        self.op = transcriptor.op
        self.ultimo = None            # ResultadoSTT del último audio (latencia, duración, silencio)

    @property
    def modelo(self) -> str:
        return self._t.modelo

    @property
    def motor(self) -> str:
        return self._t.motor.nombre

    async def transcribir(self, audio: bytes, formato: str = "webm"):
        r = await asyncio.wait_for(asyncio.to_thread(self._t.transcribir_bytes, audio, formato),
                                   self.op.timeout_stt_s)
        self.ultimo = r
        return _construir(Transcripcion, texto=r.texto, modelo=r.modelo, idioma=r.idioma,
                          duracion_ms=int(r.latencia_s * 1000), motor=r.motor)

    async def precalentar(self) -> None:
        await asyncio.to_thread(self._t.precalentar)


class SintetizadorLocal:
    """TTS para el orquestador: `await sintetizar(texto)` → WAV. Normaliza el texto antes de leerlo."""

    def __init__(self, tts: TTSConRespaldo, op: _opciones.OpcionesVoz):
        self.tts = tts
        self.op = op
        self.ultimo = None            # AudioSintetizado del último texto (latencia, respaldo, voz)

    @property
    def motor(self) -> str:
        return self.tts.nombre

    async def sintetizar(self, texto: str):
        a = await asyncio.wait_for(asyncio.to_thread(self.tts.sintetizar, texto), self.op.timeout_tts_s)
        self.ultimo = a
        return _construir(Audio, datos=a.wav, formato=a.formato, motor=a.motor, duracion_ms=a.duracion_ms,
                          texto=a.texto)

    def locutor(self, *, max_oraciones: int | None = None) -> Locutor:
        return Locutor(self.tts, self.op, max_oraciones=max_oraciones)

    def oraciones(self, texto: str) -> list[str]:
        return partir_en_oraciones(texto, min_caracteres=self.op.min_caracteres_oracion,
                                   max_caracteres=self.op.max_caracteres_oracion)

    async def precalentar(self) -> None:
        await asyncio.to_thread(self.tts.precalentar)


def crear_transcriptor(cfg: dict[str, Any] | None) -> TranscriptorLocal:
    op = _opciones.desde_config(cfg)
    try:
        return TranscriptorLocal(Transcriptor(op, crear_motor_stt(op)))
    except STTNoDisponible as e:
        raise VozNoDisponible(str(e)) from e


def crear_sintetizador(cfg: dict[str, Any] | None) -> SintetizadorLocal:
    """Kokoro con respaldo `say`. Kokoro se carga en el primer uso (o en `precalentar`)."""
    op = _opciones.desde_config(cfg)
    try:
        return SintetizadorLocal(crear_tts(op), op)
    except TTSNoDisponible as e:
        raise VozNoDisponible(str(e)) from e


async def resumen_hablado(respuesta: str, cfg: dict[str, Any] | None, *, resumen_del_modelo: str | None = None,
                          generar: Callable[[str], Awaitable[str]] | None = None) -> ResumenHablado:
    return await _resumen_hablado(respuesta, _opciones.desde_config(cfg), resumen_del_modelo=resumen_del_modelo,
                                  generar=generar)


async def precalentar_voz(cfg: dict[str, Any] | None) -> dict[str, str]:
    """Carga Whisper y Kokoro y hace una inferencia corta con cada uno. Devuelve el estado por componente."""
    estado: dict[str, str] = {}
    for nombre, fabrica in (("stt", crear_transcriptor), ("tts", crear_sintetizador)):
        try:
            motor = fabrica(cfg)
            await motor.precalentar()
            estado[nombre] = f"ok ({motor.motor})"
        except Exception as e:  # warmup informa; no se cae
            estado[nombre] = f"error: {e}"
    return estado
