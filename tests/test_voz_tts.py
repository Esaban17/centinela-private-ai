"""TTS con respaldo automático (motores simulados; no requiere Kokoro ni Mac)."""

import platform

import numpy as np
import pytest

from orchestrator.voz_local.audio import a_wav, duracion_wav_ms, leer_wav
from orchestrator.voz_local.opciones import OpcionesVoz, desde_config
from orchestrator.voz_local.tts import AudioSintetizado, KokoroTTS, SayTTS, TTSConRespaldo, TTSNoDisponible, crear_tts


class MotorFalso:
    def __init__(self, nombre, falla=None):
        self.nombre, self.voz, self.falla, self.textos = nombre, "v", falla, []

    def sintetizar(self, texto):
        self.textos.append(texto)
        if self.falla:
            raise self.falla
        wav = a_wav(np.zeros(2400, dtype=np.float32), 24000)
        return AudioSintetizado(wav, self.nombre, self.voz, texto, duracion_wav_ms(wav), 0.01)

    def precalentar(self):
        self.sintetizar("Listo.")


def test_wav_ida_y_vuelta():
    muestras = np.sin(np.linspace(0, 100, 24000)).astype(np.float32) * 0.5
    wav = a_wav(muestras, 24000)
    leidas, sr = leer_wav(wav)
    assert sr == 24000 and duracion_wav_ms(wav) == 1000
    assert np.allclose(leidas, muestras, atol=1e-3)


def test_usa_principal_y_normaliza():
    p, r = MotorFalso("kokoro"), MotorFalso("say")
    audio = TTSConRespaldo(p, r).sintetizar("Saldo de Q1,500.00")
    assert audio.motor == "kokoro" and not audio.respaldo
    assert p.textos == ["Saldo de 1500 quetzales"] and r.textos == []


def test_falla_transitoria_usa_respaldo_solo_esa_vez():
    p, r = MotorFalso("kokoro", falla=RuntimeError("onnx")), MotorFalso("say")
    tts = TTSConRespaldo(p, r, fallos_para_degradar=2)
    a = tts.sintetizar("Hola, licenciado.")
    assert a.motor == "say" and a.respaldo and not tts.degradado
    p.falla = None
    assert tts.sintetizar("Otra oración.").motor == "kokoro"


def test_fallos_seguidos_degradan():
    p, r = MotorFalso("kokoro", falla=RuntimeError("onnx")), MotorFalso("say")
    tts = TTSConRespaldo(p, r, fallos_para_degradar=2)
    tts.sintetizar("Uno dos tres.")
    tts.sintetizar("Cuatro cinco.")
    assert tts.degradado and tts.nombre == "say"
    tts.sintetizar("Seis siete.")
    assert len(p.textos) == 2                 # ya no se intenta el principal
    tts.restablecer()
    assert not tts.degradado


def test_no_disponible_degrada_de_inmediato():
    p, r = MotorFalso("kokoro", falla=TTSNoDisponible("sin modelo")), MotorFalso("say")
    tts = TTSConRespaldo(p, r)
    tts.sintetizar("Hola, licenciado.")
    assert tts.degradado


def test_sin_respaldo_propaga_el_error():
    tts = TTSConRespaldo(MotorFalso("kokoro", falla=RuntimeError("x")), None)
    with pytest.raises(RuntimeError):
        tts.sintetizar("Hola, licenciado.")


def test_texto_vacio():
    with pytest.raises(ValueError):
        TTSConRespaldo(MotorFalso("kokoro"), None).sintetizar("| --- | 🚨 |")


def test_precalentar_cae_al_respaldo():
    p, r = MotorFalso("kokoro", falla=TTSNoDisponible("x")), MotorFalso("say")
    tts = TTSConRespaldo(p, r)
    tts.precalentar()
    assert tts.degradado and r.textos == ["Listo."]


def test_kokoro_sin_archivos_es_no_disponible(tmp_path):
    op = OpcionesVoz(dir_modelos=tmp_path)
    pytest.importorskip("kokoro_onnx")
    with pytest.raises(TTSNoDisponible, match="make voz-modelos"):
        KokoroTTS(op).sintetizar("Hola.")


@pytest.mark.skipif(platform.system() == "Darwin", reason="en macOS `say` sí existe")
def test_say_fuera_de_mac():
    assert not SayTTS.disponible()
    with pytest.raises(TTSNoDisponible):
        SayTTS(OpcionesVoz()).sintetizar("Hola.")


def test_crear_tts_desde_config(cfg):
    tts = crear_tts(desde_config(cfg))
    assert tts.principal.nombre == "kokoro"
    assert (tts.respaldo is not None) == SayTTS.disponible()
    assert tts.principal.idioma == "es-419"


def test_kokoro_falla_en_linux_sin_respaldo_y_say_principal(cfg):
    cfg["modelos"]["tts"]["motor"] = "say"
    cfg["modelos"]["tts"]["respaldo"] = "kokoro"
    tts = crear_tts(desde_config(cfg))
    assert tts.principal.nombre == "say" and tts.respaldo.nombre == "kokoro"


def test_telemetria_de_onnxruntime_apagada():
    import os

    import orchestrator.voz_local.tts  # noqa: F401

    assert os.environ.get("ORT_DISABLE_TELEMETRY") == "1"
