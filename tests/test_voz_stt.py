"""STT: decodificación con ffmpeg, silencio, alucinaciones y elección de motor (sin modelos de Whisper)."""

import shutil
import subprocess

import numpy as np
import pytest

from orchestrator.voz_local import stt as stt_mod
from orchestrator.voz_local.audio import AudioInvalido, a_wav, decodificar_audio
from orchestrator.voz_local.opciones import OpcionesVoz, desde_config
from orchestrator.voz_local.stt import STTNoDisponible, Transcriptor, crear_motor_stt, limpiar_transcripcion

requiere_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requiere ffmpeg")


def tono(segundos=1.0, sr=24000, amplitud=0.3):
    t = np.arange(int(segundos * sr)) / sr
    return (amplitud * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


class MotorFalso:
    nombre, modelo = "falso", "falso-1"

    def __init__(self, texto="¿Cómo está la cartera de Lucía Cojtí?"):
        self.texto, self.llamadas = texto, 0

    def transcribir_muestras(self, muestras):
        self.llamadas += 1
        return self.texto

    def precalentar(self):
        pass


@requiere_ffmpeg
def test_decodifica_wav_a_16k_mono():
    m = decodificar_audio(a_wav(tono(1.0, 24000), 24000), "audio/wav", sample_rate=16000)
    assert m.dtype == np.float32 and abs(m.size - 16000) < 50


@requiere_ffmpeg
def test_decodifica_webm_opus_como_el_navegador(tmp_path):
    wav, webm = tmp_path / "a.wav", tmp_path / "a.webm"
    wav.write_bytes(a_wav(tono(1.0, 48000), 48000))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", str(webm)], check=True)
    m = decodificar_audio(webm.read_bytes(), "audio/webm;codecs=opus")
    assert abs(m.size - 16000) < 400


@requiere_ffmpeg
def test_recorta_audio_largo():
    m = decodificar_audio(a_wav(tono(3.0, 16000), 16000), "wav", max_segundos=1)
    assert m.size <= 16000


@requiere_ffmpeg
def test_audio_basura():
    with pytest.raises(AudioInvalido):
        decodificar_audio(b"esto no es audio", "webm")
    with pytest.raises(AudioInvalido):
        decodificar_audio(b"", "webm")


@requiere_ffmpeg
def test_transcriptor_con_motor_falso():
    motor = MotorFalso()
    r = Transcriptor(OpcionesVoz(), motor).transcribir_bytes(a_wav(tono(1.5, 16000), 16000), "wav")
    assert r.texto == "¿Cómo está la cartera de Lucía Cojtí?" and not r.silencio
    assert r.idioma == "es" and abs(r.duracion_audio_s - 1.5) < 0.05


def test_silencio_no_llama_al_modelo():
    motor = MotorFalso()
    r = Transcriptor(OpcionesVoz(), motor).transcribir_muestras(np.zeros(16000, dtype=np.float32))
    assert r.silencio and r.texto == "" and motor.llamadas == 0


@pytest.mark.parametrize("alucinacion", ["Subtítulos realizados por la comunidad de Amara.org",
                                         "¡Gracias por ver el video!", " [Música] ", "Música"])
def test_filtra_alucinaciones(alucinacion):
    assert limpiar_transcripcion(alucinacion) == ""


def test_conserva_texto_normal():
    assert limpiar_transcripcion("  Gracias por ver los movimientos  ") == "Gracias por ver los movimientos"


def test_auto_en_linux_usa_faster(monkeypatch):
    monkeypatch.setattr(stt_mod, "es_apple_silicon", lambda: False)
    creados = []
    monkeypatch.setattr(stt_mod, "FasterWhisperSTT", lambda op: creados.append("faster") or MotorFalso())
    monkeypatch.setattr(stt_mod, "MlxWhisperSTT", lambda op: creados.append("mlx") or MotorFalso())
    crear_motor_stt(OpcionesVoz(motor_stt="auto"))
    assert creados == ["faster"]


def test_auto_en_mac_sin_mlx_cae_a_faster(monkeypatch):
    monkeypatch.setattr(stt_mod, "es_apple_silicon", lambda: True)

    def sin_mlx(op):
        raise STTNoDisponible("sin mlx")

    monkeypatch.setattr(stt_mod, "MlxWhisperSTT", sin_mlx)
    monkeypatch.setattr(stt_mod, "FasterWhisperSTT", lambda op: MotorFalso())
    assert isinstance(crear_motor_stt(OpcionesVoz()), MotorFalso)


def test_motor_desconocido():
    with pytest.raises(ValueError):
        crear_motor_stt(OpcionesVoz(motor_stt="nube"))


def test_mlx_fuera_de_mac_no_disponible():
    pytest.importorskip("numpy")
    try:
        import mlx_whisper  # noqa: F401
        pytest.skip("mlx-whisper instalado")
    except ImportError:
        pass
    with pytest.raises(STTNoDisponible):
        crear_motor_stt(OpcionesVoz(motor_stt="mlx"))


def test_faster_sin_modelo_da_instruccion(tmp_path):
    pytest.importorskip("faster_whisper")
    with pytest.raises(STTNoDisponible, match="make voz-modelos"):
        crear_motor_stt(OpcionesVoz(motor_stt="faster", modelo_faster="tiny", dir_modelos=tmp_path))


def test_opciones_desde_config(cfg):
    op = desde_config(cfg)
    assert op.idioma == "es" and op.modelo_mlx == "mlx-community/whisper-large-v3-turbo"
    assert op.ruta_kokoro.name == "kokoro-v1.0.onnx" and op.ruta_kokoro.is_absolute()
    assert "Cojtí" in op.prompt_inicial
