"""Locutor: habla la respuesta oración por oración mientras el LLM sigue escribiendo.

El orquestador llama `alimentar(delta)` con cada `llm.delta` (o con el `resumen_hablado` completo) y consume
`fragmentos()`, que entrega un `FragmentoVoz` con el WAV de cada oración en orden. La primera oración se
sintetiza en cuanto termina, así la primera palabra suena antes de que acabe la respuesta escrita.

    locutor = Locutor(tts, op)
    tarea = asyncio.create_task(enviar_audios(locutor.fragmentos()))
    async for delta in llm:
        locutor.alimentar(delta)
    locutor.terminar()
    await tarea

`cancelar()` corta lo pendiente (botón de silenciar o una pregunta nueva).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

from .opciones import OpcionesVoz
from .texto import SegmentadorOraciones
from .tts import TTSConRespaldo

log = logging.getLogger("centinela.voz.locutor")

_FIN = object()


@dataclass
class FragmentoVoz:
    indice: int
    texto: str
    wav: bytes | None            # None si esa oración no se pudo sintetizar (el texto igual está en el chat)
    motor: str | None
    duracion_ms: int
    latencia_ms: int             # tiempo de síntesis de esta oración
    desde_inicio_ms: int         # desde que se creó el Locutor hasta tener este audio
    respaldo: bool = False
    error: str | None = None


class Locutor:
    def __init__(self, tts: TTSConRespaldo, op: OpcionesVoz, *, max_oraciones: int | None = None):
        self.tts = tts
        self.op = op
        self.max_oraciones = op.max_oraciones_habladas if max_oraciones is None else max_oraciones
        self._seg = SegmentadorOraciones(min_caracteres=op.min_caracteres_oracion,
                                         max_caracteres=op.max_caracteres_oracion)
        self._cola: asyncio.Queue = asyncio.Queue()
        self._encoladas = 0
        self._cancelado = False
        self._terminado = False
        self._t0 = time.perf_counter()
        self.primer_audio_ms: int | None = None

    # -- Entrada (sincrónica, desde el loop del orquestador) ---------------------------------------------
    def alimentar(self, delta: str) -> None:
        if self._cancelado or self._terminado or self._lleno():
            return
        for oracion in self._seg.agregar(delta):
            self._encolar(oracion)

    def terminar(self) -> None:
        if self._terminado:
            return
        if not self._cancelado:
            for oracion in self._seg.terminar():
                self._encolar(oracion)
        self._terminado = True
        self._cola.put_nowait(_FIN)

    def cancelar(self) -> None:
        self._cancelado = True
        while not self._cola.empty():
            self._cola.get_nowait()
        self._cola.put_nowait(_FIN)

    @property
    def cancelado(self) -> bool:
        return self._cancelado

    def _lleno(self) -> bool:
        return bool(self.max_oraciones) and self._encoladas >= self.max_oraciones

    def _encolar(self, oracion: str) -> None:
        if self._lleno():
            return
        self._encoladas += 1
        self._cola.put_nowait(oracion)

    # -- Salida --------------------------------------------------------------------------------------------
    async def fragmentos(self) -> AsyncIterator[FragmentoVoz]:
        indice = 0
        while True:
            oracion = await self._cola.get()
            if oracion is _FIN or self._cancelado:
                return
            yield await self._sintetizar(indice, oracion)
            indice += 1

    async def _sintetizar(self, indice: int, oracion: str) -> FragmentoVoz:
        t = time.perf_counter()
        try:
            # El texto ya viene normalizado por el segmentador.
            audio = await asyncio.wait_for(
                asyncio.to_thread(self.tts.sintetizar, oracion, normalizar=False), self.op.timeout_tts_s)
        except Exception as e:
            motivo = "tiempo agotado" if isinstance(e, asyncio.TimeoutError) else str(e)
            if isinstance(e, asyncio.TimeoutError):
                self.tts.degradar(f"La síntesis tardó más de {self.op.timeout_tts_s} s.")
            log.warning("No se pudo sintetizar la oración %d: %s", indice, motivo)
            return FragmentoVoz(indice, oracion, None, None, 0, self._ms(t), self._ms(self._t0), error=motivo)
        frag = FragmentoVoz(indice, oracion, audio.wav, audio.motor, audio.duracion_ms, self._ms(t),
                            self._ms(self._t0), respaldo=audio.respaldo)
        if self.primer_audio_ms is None:
            self.primer_audio_ms = frag.desde_inicio_ms
        return frag

    @staticmethod
    def _ms(desde: float) -> int:
        return int(round((time.perf_counter() - desde) * 1000))


async def hablar(textos: AsyncIterator[str], tts: TTSConRespaldo, op: OpcionesVoz, *,
                 max_oraciones: int | None = None) -> AsyncIterator[FragmentoVoz]:
    """Atajo: consume un iterador async de deltas y entrega los fragmentos de voz."""
    locutor = Locutor(tts, op, max_oraciones=max_oraciones)

    async def alimentar():
        try:
            async for delta in textos:
                locutor.alimentar(delta)
        finally:
            locutor.terminar()

    tarea = asyncio.create_task(alimentar())
    try:
        async for frag in locutor.fragmentos():
            yield frag
    finally:
        if not tarea.done():
            tarea.cancel()
