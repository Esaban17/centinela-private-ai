"""Interfaz que engancha el orquestador (orchestrator/voz.py), con motores simulados."""

import shutil

import numpy as np
import pytest

from orchestrator import voz
from orchestrator.voz_local.audio import a_wav
from orchestrator.voz_local.opciones import OpcionesVoz
from orchestrator.voz_local.stt import Transcriptor
from orchestrator.voz_local.tts import TTSConRespaldo
from tests.test_voz_locutor import MotorLento
from tests.test_voz_stt import MotorFalso


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requiere ffmpeg")
async def test_transcribir_async():
    t = voz.TranscriptorLocal(Transcriptor(OpcionesVoz(), MotorFalso("Hola, Centinela.")))
    tono = (0.3 * np.sin(np.arange(16000) / 3)).astype(np.float32)
    r = await t.transcribir(a_wav(tono, 16000), "wav")
    assert (r.texto, r.modelo, r.idioma) == ("Hola, Centinela.", "falso-1", "es")


async def test_sintetizar_async_devuelve_wav():
    s = voz.SintetizadorLocal(TTSConRespaldo(MotorLento(), None), OpcionesVoz())
    a = await s.sintetizar("Saldo de Q100.00")
    assert a.datos[:4] == b"RIFF" and a.formato == "wav" and a.motor == "kokoro"
    assert s.ultimo.texto == "Saldo de 100 quetzales"
    assert s.oraciones("Uno dos tres. Cuatro cinco seis.") == ["Uno dos tres.", "Cuatro cinco seis."]


def test_fabricas_con_config(cfg):
    s = voz.crear_sintetizador(cfg)          # Kokoro se carga perezosamente: no falla sin modelo
    assert s.motor in ("kokoro", "say")


def test_transcriptor_sin_motor_es_voz_no_disponible(cfg, tmp_path):
    cfg["rutas"]["modelos_voz"] = str(tmp_path)
    cfg["modelos"]["stt"]["motor"] = "faster"
    with pytest.raises(voz.VozNoDisponible):
        voz.crear_transcriptor(cfg)


async def test_precalentar_informa_sin_caerse(cfg, tmp_path):
    cfg["rutas"]["modelos_voz"] = str(tmp_path)
    estado = await voz.precalentar_voz(cfg)
    assert set(estado) == {"stt", "tts"}
