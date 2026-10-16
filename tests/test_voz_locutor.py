"""Locutor: síntesis oración por oración mientras llega el texto (TTS simulado)."""

import asyncio
import time

import numpy as np

from orchestrator.voz_local.audio import a_wav, duracion_wav_ms
from orchestrator.voz_local.locutor import Locutor, hablar
from orchestrator.voz_local.opciones import OpcionesVoz
from orchestrator.voz_local.tts import AudioSintetizado, TTSConRespaldo


class MotorLento:
    nombre, voz = "kokoro", "em_alex"

    def __init__(self, demora=0.0, falla_en=None):
        self.demora, self.falla_en, self.textos = demora, falla_en, []

    def sintetizar(self, texto):
        self.textos.append(texto)
        if self.falla_en and self.falla_en in texto:
            raise RuntimeError("falla simulada")
        time.sleep(self.demora)
        wav = a_wav(np.zeros(4800, dtype=np.float32), 24000)
        return AudioSintetizado(wav, self.nombre, self.voz, texto, duracion_wav_ms(wav), self.demora)

    def precalentar(self):
        pass


class Respaldo(MotorLento):
    nombre, voz = "say", "Paulina"


RESPUESTA = "Buenas tardes, licenciado. Encontré seis movimientos raros. El mayor fue de Q18,500.00. ¿Abro un ticket?"


async def _consumir(locutor):
    return [f async for f in locutor.fragmentos()]


async def test_habla_en_orden_y_respeta_el_tope():
    op = OpcionesVoz(max_oraciones_habladas=3)
    locutor = Locutor(TTSConRespaldo(MotorLento(), None), op)
    tarea = asyncio.create_task(_consumir(locutor))
    for palabra in RESPUESTA.split(" "):
        locutor.alimentar(palabra + " ")
        await asyncio.sleep(0)
    locutor.terminar()
    frags = await tarea
    assert [f.indice for f in frags] == [0, 1, 2]
    assert frags[2].texto == "El mayor fue de 18500 quetzales."
    assert all(f.wav and f.motor == "kokoro" for f in frags)


async def test_primer_audio_antes_de_que_termine_el_texto():
    locutor = Locutor(TTSConRespaldo(MotorLento(0.01), None), OpcionesVoz(max_oraciones_habladas=0))
    tarea = asyncio.create_task(_consumir(locutor))
    locutor.alimentar("Buenas tardes, licenciado. Ahora sigo")
    await asyncio.sleep(0.2)                  # el «LLM» todavía no termina
    assert locutor.primer_audio_ms is not None
    locutor.alimentar(" escribiendo.")
    locutor.terminar()
    frags = await tarea
    assert [f.texto for f in frags] == ["Buenas tardes, licenciado.", "Ahora sigo escribiendo."]


async def test_falla_de_una_oracion_usa_respaldo():
    tts = TTSConRespaldo(MotorLento(falla_en="seis"), Respaldo())
    frags = [f async for f in hablar(_deltas(RESPUESTA), tts, OpcionesVoz(max_oraciones_habladas=0))]
    assert [f.motor for f in frags] == ["kokoro", "say", "kokoro", "kokoro"]
    assert frags[1].respaldo


async def test_tiempo_agotado_no_congela_y_degrada():
    tts = TTSConRespaldo(MotorLento(0.5), Respaldo())
    op = OpcionesVoz(timeout_tts_s=0.1, max_oraciones_habladas=0)
    frags = [f async for f in hablar(_deltas(RESPUESTA), tts, op)]
    assert frags[0].wav is None and frags[0].error == "tiempo agotado"
    assert tts.degradado and frags[1].motor == "say"


async def test_cancelar_corta_lo_pendiente():
    locutor = Locutor(TTSConRespaldo(MotorLento(0.05), None), OpcionesVoz(max_oraciones_habladas=0))
    locutor.alimentar(RESPUESTA)
    frags = []
    async for f in locutor.fragmentos():
        frags.append(f)
        locutor.cancelar()
    assert len(frags) == 1 and locutor.cancelado


async def test_sin_texto_hablable_no_entrega_nada():
    frags = [f async for f in hablar(_deltas("| a | b |\n|---|---|\n"), TTSConRespaldo(MotorLento(), None),
                                     OpcionesVoz())]
    assert frags == []


async def _deltas(texto, paso=4):
    for i in range(0, len(texto), paso):
        yield texto[i:i + paso]
        await asyncio.sleep(0)
