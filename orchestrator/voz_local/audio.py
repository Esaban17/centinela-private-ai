"""Utilidades de audio: decodificar lo que manda el navegador y empaquetar WAV.

El navegador graba en webm/opus (Chrome, Firefox) o mp4/aac (Safari). Whisper necesita PCM mono a 16 kHz en
float32, así que todo pasa por `ffmpeg` (local, instalado con Homebrew). Se usa un archivo temporal en vez de
una tubería porque los mp4 de Safari traen el índice al final y ffmpeg no los lee desde stdin.
"""

from __future__ import annotations

import io
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np


class AudioInvalido(ValueError):
    """El audio no se pudo decodificar o está fuera de los límites."""


_EXTENSIONES = {"webm": ".webm", "ogg": ".ogg", "opus": ".ogg", "wav": ".wav", "mp4": ".mp4", "m4a": ".m4a",
                "mpeg": ".mp3", "mp3": ".mp3", "aac": ".aac", "flac": ".flac"}


def _extension(formato: str) -> str:
    f = (formato or "").lower().split(";")[0].strip()
    f = f.split("/")[-1]                           # acepta MIME («audio/webm;codecs=opus») o extensión
    return _EXTENSIONES.get(f.lstrip("."), ".bin")


def decodificar_audio(datos: bytes, formato: str = "webm", *, sample_rate: int = 16000, ffmpeg: str = "ffmpeg",
                      max_segundos: float | None = None, timeout_s: float = 15.0) -> np.ndarray:
    """Convierte audio en cualquier formato que entienda ffmpeg a PCM float32 mono."""
    if not datos:
        raise AudioInvalido("No llegó audio.")
    with tempfile.NamedTemporaryFile(suffix=_extension(formato)) as tmp:
        tmp.write(datos)
        tmp.flush()
        cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", tmp.name]
        if max_segundos:
            cmd += ["-t", str(max_segundos)]
        cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate), "pipe:1"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
        except FileNotFoundError as e:
            raise AudioInvalido(f"No se encontró ffmpeg ({ffmpeg}). En la Mac: brew install ffmpeg") from e
        except subprocess.TimeoutExpired as e:
            raise AudioInvalido("ffmpeg tardó demasiado en decodificar el audio.") from e
    if proc.returncode != 0:
        raise AudioInvalido(f"ffmpeg no pudo leer el audio: {proc.stderr.decode(errors='replace').strip()[:300]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def rms(muestras: np.ndarray) -> float:
    if muestras.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(muestras, dtype=np.float64))))


def es_silencio(muestras: np.ndarray, umbral_rms: float) -> bool:
    return rms(muestras) < umbral_rms


def a_wav(muestras: np.ndarray, sample_rate: int) -> bytes:
    """float32 en [-1, 1] → WAV PCM de 16 bits mono."""
    pcm = (np.clip(np.asarray(muestras, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def leer_wav(datos: bytes) -> tuple[np.ndarray, int]:
    """WAV PCM 16 bits (mono o estéreo) → float32 mono y frecuencia de muestreo."""
    with wave.open(io.BytesIO(datos), "rb") as w:
        if w.getsampwidth() != 2:
            raise AudioInvalido("Solo se admite WAV PCM de 16 bits.")
        canales, sr = w.getnchannels(), w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if canales > 1:
        pcm = pcm.reshape(-1, canales).mean(axis=1)
    return pcm, sr


def duracion_wav_ms(datos: bytes) -> int:
    with wave.open(io.BytesIO(datos), "rb") as w:
        return int(round(1000 * w.getnframes() / w.getframerate()))


def guardar(datos: bytes, ruta: Path) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    return ruta
