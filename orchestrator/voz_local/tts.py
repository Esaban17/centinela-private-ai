"""Síntesis de voz local (TTS): Kokoro con respaldo automático a `say` de macOS.

- Kokoro-82M vía `kokoro-onnx` (ONNX Runtime en CPU). Sus voces en español (`ef_dora`, `em_alex`, `em_santa`)
  tienen timbre de España, pero el fonemizador acepta `es-419` (español latinoamericano: seseo, «Lucía» con s).
  Por eso `idioma_kokoro: es-419` por defecto. `scripts/comparar_voces.py` permite escucharlas en la Mac.
- `say -v Paulina` (es-MX) viene con macOS y no depende de nada más: es el respaldo.

`TTSConRespaldo` intenta el motor principal y, si falla (no instalado, modelo ausente, excepción), sintetiza esa
misma oración con el respaldo. Tras `fallos_para_degradar` fallos seguidos se queda con el respaldo hasta que
se llame `restablecer()`, para no pagar el error en cada oración durante la demo.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .audio import a_wav, duracion_wav_ms
from .opciones import OpcionesVoz
from .texto import normalizar_para_voz, tiene_contenido

log = logging.getLogger("centinela.voz.tts")


class TTSNoDisponible(RuntimeError):
    """El motor de TTS no está instalado, no tiene su modelo o no existe en esta plataforma."""


@dataclass
class AudioSintetizado:
    wav: bytes
    motor: str
    voz: str
    texto: str
    duracion_ms: int
    latencia_s: float
    respaldo: bool = False
    formato: str = "wav"


class MotorTTS(Protocol):
    nombre: str
    voz: str

    def sintetizar(self, texto: str) -> AudioSintetizado: ...

    def precalentar(self) -> None: ...


class KokoroTTS:
    nombre = "kokoro"

    def __init__(self, op: OpcionesVoz, *, voz: str | None = None, idioma: str | None = None,
                 velocidad: float | None = None):
        self.voz = voz or op.voz_kokoro
        self.idioma = idioma or op.idioma_kokoro
        self.velocidad = velocidad or op.velocidad_kokoro
        self._rutas = (op.ruta_kokoro, op.ruta_voces)
        self._kokoro = None
        self._candado = threading.Lock()

    def _cargar(self):
        if self._kokoro is None:
            try:
                from kokoro_onnx import Kokoro
            except ImportError as e:
                raise TTSNoDisponible("kokoro-onnx no está instalado (pip install kokoro-onnx).") from e
            modelo, voces = self._rutas
            if not modelo.exists() or not voces.exists():
                raise TTSNoDisponible(f"Faltan los archivos de Kokoro ({modelo.name}, {voces.name}) en "
                                      f"{modelo.parent}. Descárguelos una vez con: make voz-modelos")
            self._kokoro = Kokoro(str(modelo), str(voces))
            if self.voz not in self._kokoro.get_voices():
                raise TTSNoDisponible(f"La voz «{self.voz}» no existe en {voces.name}.")
        return self._kokoro

    def sintetizar(self, texto: str) -> AudioSintetizado:
        t0 = time.perf_counter()
        with self._candado:                      # ONNX Runtime ya usa varios hilos; uno a la vez
            kokoro = self._cargar()
            muestras, sr = kokoro.create(texto, voice=self.voz, speed=self.velocidad, lang=self.idioma)
        wav = a_wav(muestras, sr)
        return AudioSintetizado(wav, self.nombre, self.voz, texto, duracion_wav_ms(wav), time.perf_counter() - t0)

    def precalentar(self) -> None:
        self.sintetizar("Listo.")


class SayTTS:
    """`say` de macOS. Escribe WAV PCM 16 bits a 22.05 kHz."""

    nombre = "say"

    def __init__(self, op: OpcionesVoz, *, voz: str | None = None, velocidad: int | None = None):
        self.voz = voz or op.voz_say
        self.velocidad = int(velocidad or op.velocidad_say)

    @staticmethod
    def disponible() -> bool:
        return platform.system() == "Darwin" and shutil.which("say") is not None

    def sintetizar(self, texto: str) -> AudioSintetizado:
        if not self.disponible():
            raise TTSNoDisponible("`say` solo existe en macOS.")
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory() as d:
            salida = Path(d) / "voz.wav"
            # El texto va por stdin: así no se interpreta como opción aunque empiece con «-».
            proc = subprocess.run(
                ["say", "-v", self.voz, "-r", str(self.velocidad), "--file-format=WAVE",
                 "--data-format=LEI16@22050", "-o", str(salida)],
                input=texto.encode("utf-8"), capture_output=True, timeout=30, check=False)
            if proc.returncode != 0 or not salida.exists():
                raise TTSNoDisponible(f"say falló: {proc.stderr.decode(errors='replace').strip()[:200]}")
            wav = salida.read_bytes()
        return AudioSintetizado(wav, self.nombre, self.voz, texto, duracion_wav_ms(wav), time.perf_counter() - t0)

    def precalentar(self) -> None:
        self.sintetizar("Listo.")


class TTSConRespaldo:
    """Motor principal con respaldo automático. Sincrónico y seguro para usar desde varios hilos."""

    def __init__(self, principal: MotorTTS | None, respaldo: MotorTTS | None, *, fallos_para_degradar: int = 2):
        if principal is None and respaldo is None:
            raise TTSNoDisponible("No hay ningún motor de TTS configurado.")
        self.principal = principal
        self.respaldo = respaldo
        self.fallos_para_degradar = fallos_para_degradar
        self._fallos = 0
        self._degradado = principal is None
        self._candado = threading.Lock()

    @property
    def nombre(self) -> str:
        return self.motor_activo.nombre

    @property
    def motor_activo(self) -> MotorTTS:
        return self.respaldo if (self._degradado and self.respaldo) else self.principal  # type: ignore[return-value]

    @property
    def degradado(self) -> bool:
        return self._degradado

    def degradar(self, motivo: str = "") -> None:
        if self.respaldo is not None and not self._degradado:
            log.warning("TTS: paso al respaldo «%s». %s", self.respaldo.nombre, motivo)
            self._degradado = True

    def restablecer(self) -> None:
        with self._candado:
            self._fallos = 0
            self._degradado = self.principal is None

    def sintetizar(self, texto: str, *, normalizar: bool = True) -> AudioSintetizado:
        if normalizar:
            texto = normalizar_para_voz(texto)
        if not tiene_contenido(texto):
            raise ValueError("No hay texto que leer.")
        if not self._degradado and self.principal is not None:
            try:
                audio = self.principal.sintetizar(texto)
                self._fallos = 0
                return audio
            except Exception as e:  # cualquier falla del principal debe caer al respaldo, nunca al vacío
                with self._candado:
                    self._fallos += 1
                    permanente = isinstance(e, TTSNoDisponible)
                    if permanente or self._fallos >= self.fallos_para_degradar:
                        self.degradar(str(e))
                if self.respaldo is None:
                    raise
                log.warning("TTS principal falló (%s); esta oración va con «%s».", e, self.respaldo.nombre)
        if self.respaldo is None:
            raise TTSNoDisponible("El TTS principal no está disponible y no hay respaldo.")
        audio = self.respaldo.sintetizar(texto)
        audio.respaldo = True
        return audio

    def precalentar(self) -> None:
        """Carga el principal; si no puede, deja activo el respaldo (y lo calienta)."""
        if self.principal is not None and not self._degradado:
            try:
                self.principal.precalentar()
                return
            except Exception as e:
                if self.respaldo is None:  # sin respaldo no hay voz: que el warmup lo diga, no que finja «ok»
                    raise
                self.degradar(f"No se pudo precalentar: {e}")
        if self.respaldo is not None:
            self.respaldo.precalentar()


def crear_tts(op: OpcionesVoz) -> TTSConRespaldo:
    motores = {"kokoro": lambda: KokoroTTS(op), "say": lambda: SayTTS(op)}
    principal_nombre = (op.motor_tts or "kokoro").lower()
    respaldo_nombre = (op.respaldo_tts or "ninguno").lower()
    if principal_nombre not in motores:
        raise ValueError(f"Motor de TTS desconocido: {principal_nombre} (use kokoro o say)")
    principal = motores[principal_nombre]()
    respaldo = None
    if respaldo_nombre in motores and respaldo_nombre != principal_nombre:
        if respaldo_nombre != "say" or SayTTS.disponible():
            respaldo = motores[respaldo_nombre]()
    return TTSConRespaldo(principal, respaldo)
