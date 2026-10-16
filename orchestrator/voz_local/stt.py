"""Reconocimiento de voz local (STT) con Whisper.

Motores:

- `mlx`: `mlx-whisper` sobre Apple Silicon (Metal). Es el de la demo: whisper-large-v3-turbo.
- `faster`: `faster-whisper` (CTranslate2, CPU). Alternativa para Linux y para probar sin Mac.

`auto` elige `mlx` en macOS arm64 y `faster` en lo demás. El idioma se fuerza a español: así Whisper no pierde
tiempo detectándolo ni cambia a inglés con palabras como «NOC» o «vCenter».

Los modelos se cargan desde `rutas.modelos_voz` (los descarga `make voz-modelos` durante la instalación). En
tiempo de ejecución no se descarga nada: si falta el modelo, el error lo dice.
"""

from __future__ import annotations

import logging
import platform
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .audio import decodificar_audio, es_silencio
from .opciones import OpcionesVoz

log = logging.getLogger("centinela.voz.stt")


class STTNoDisponible(RuntimeError):
    """El motor de STT no está instalado o su modelo no está descargado."""


@dataclass
class ResultadoSTT:
    texto: str
    idioma: str
    motor: str
    modelo: str
    duracion_audio_s: float
    latencia_s: float
    silencio: bool = False


class MotorSTT(Protocol):
    nombre: str
    modelo: str

    def transcribir_muestras(self, muestras: np.ndarray) -> str: ...

    def precalentar(self) -> None: ...


# Frases que Whisper «alucina» con silencio o ruido (vienen de subtítulos con los que se entrenó).
_ALUCINACIONES = [
    r"subt[ií]tulos? (realizados? )?por la comunidad de amara\.org",
    r"subt[ií]tulos? por",
    r"gracias por ver(nos| el video)?",
    r"suscr[ií]bete",
    r"amara\.org",
    r"m[uú]sica",
    r"\[.*?\]|\(.*?\)",
]
_RE_ALUCINACION = re.compile(r"^[\s¡¿]*(?:" + "|".join(_ALUCINACIONES) + r")[\s.!¡]*$", re.IGNORECASE)


def limpiar_transcripcion(texto: str) -> str:
    t = re.sub(r"\s+", " ", texto or "").strip()
    return "" if _RE_ALUCINACION.match(t) else t


def es_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _modelo_local(dir_modelos: Path, nombre: str) -> str:
    """Si el modelo está descargado en modelos/<nombre corto>, usa esa ruta; si no, el id del repo."""
    candidato = dir_modelos / nombre.split("/")[-1]
    return str(candidato) if candidato.exists() else nombre


class MlxWhisperSTT:
    nombre = "mlx-whisper"

    def __init__(self, op: OpcionesVoz):
        try:
            import mlx_whisper  # noqa: F401
        except ImportError as e:
            raise STTNoDisponible("mlx-whisper no está instalado (solo funciona en Mac con Apple Silicon).") from e
        self._op = op
        self.modelo = _modelo_local(op.dir_modelos, op.modelo_mlx)

    def transcribir_muestras(self, muestras: np.ndarray) -> str:
        import mlx_whisper

        r = mlx_whisper.transcribe(
            muestras, path_or_hf_repo=self.modelo, language=self._op.idioma, task="transcribe",
            initial_prompt=self._op.prompt_inicial or None, condition_on_previous_text=False, verbose=None)
        return r.get("text", "")

    def precalentar(self) -> None:
        self.transcribir_muestras(np.zeros(16000, dtype=np.float32))


class FasterWhisperSTT:
    nombre = "faster-whisper"

    def __init__(self, op: OpcionesVoz):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise STTNoDisponible("faster-whisper no está instalado (pip install faster-whisper).") from e
        self._op = op
        self.modelo = op.modelo_faster
        raiz = op.dir_modelos / "faster-whisper"
        local = Path(self.modelo) if Path(self.modelo).is_absolute() else raiz / self.modelo
        origen = str(local) if (local / "model.bin").exists() else self.modelo
        try:
            # local_files_only: nunca descargar en tiempo de ejecución.
            self._modelo = WhisperModel(origen, device=op.dispositivo_faster, compute_type=op.compute_type_faster,
                                        download_root=str(raiz), local_files_only=True)
        except Exception as e:  # el error de HF cuando falta el modelo es largo y confuso
            raise STTNoDisponible(f"No encuentro el modelo faster-whisper «{self.modelo}» en {raiz}. "
                                  "Descárguelo una vez con: make voz-modelos FASTER=1") from e

    def transcribir_muestras(self, muestras: np.ndarray) -> str:
        segmentos, _ = self._modelo.transcribe(
            muestras, language=self._op.idioma, task="transcribe", beam_size=1,
            initial_prompt=self._op.prompt_inicial or None, condition_on_previous_text=False, vad_filter=False)
        return "".join(s.text for s in segmentos)

    def precalentar(self) -> None:
        self.transcribir_muestras(np.zeros(16000, dtype=np.float32))


def crear_motor_stt(op: OpcionesVoz, motor: str | None = None) -> MotorSTT:
    """Crea el motor indicado; con `auto` prueba mlx en Apple Silicon y cae a faster-whisper."""
    motor = (motor or op.motor_stt or "auto").lower()
    if motor == "mlx":
        return MlxWhisperSTT(op)
    if motor == "faster":
        return FasterWhisperSTT(op)
    if motor != "auto":
        raise ValueError(f"Motor de STT desconocido: {motor} (use auto, mlx o faster)")
    if es_apple_silicon():
        try:
            return MlxWhisperSTT(op)
        except STTNoDisponible as e:
            log.warning("mlx-whisper no disponible (%s); uso faster-whisper.", e)
    return FasterWhisperSTT(op)


class Transcriptor:
    """Decodifica el audio del navegador, filtra silencio y transcribe. Sincrónico (correr en un hilo)."""

    def __init__(self, op: OpcionesVoz, motor: MotorSTT):
        self.op = op
        self.motor = motor
        self._candado = threading.Lock()          # un solo audio a la vez por modelo

    @property
    def modelo(self) -> str:
        return self.motor.modelo

    def transcribir_bytes(self, datos: bytes, formato: str = "webm") -> ResultadoSTT:
        muestras = decodificar_audio(datos, formato, sample_rate=self.op.sample_rate_stt, ffmpeg=self.op.ffmpeg,
                                     max_segundos=self.op.max_segundos_audio)
        return self.transcribir_muestras(muestras)

    def transcribir_muestras(self, muestras: np.ndarray) -> ResultadoSTT:
        t0 = time.perf_counter()
        duracion = muestras.size / self.op.sample_rate_stt
        if duracion < 0.2 or es_silencio(muestras, self.op.umbral_silencio_rms):
            return ResultadoSTT("", self.op.idioma, self.motor.nombre, self.motor.modelo, duracion,
                                time.perf_counter() - t0, silencio=True)
        with self._candado:
            texto = limpiar_transcripcion(self.motor.transcribir_muestras(muestras))
        return ResultadoSTT(texto, self.op.idioma, self.motor.nombre, self.motor.modelo, duracion,
                            time.perf_counter() - t0, silencio=not texto)

    def precalentar(self) -> None:
        with self._candado:
            self.motor.precalentar()
