"""Opciones de voz leídas de config.yaml, con valores por defecto.

Todo lo de voz se configura en `modelos.stt`, `modelos.tts` y `voz` (ver config.yaml). Este módulo junta esas
tres secciones en un solo objeto para que el resto del paquete no lea el diccionario a mano.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from comun.config import RAIZ


@dataclass(frozen=True)
class OpcionesVoz:
    # STT
    motor_stt: str = "auto"                       # auto | mlx | faster
    modelo_mlx: str = "mlx-community/whisper-large-v3-turbo"
    modelo_faster: str = "large-v3-turbo"
    idioma: str = "es"
    prompt_inicial: str = ""
    dispositivo_faster: str = "auto"
    compute_type_faster: str = "int8"
    # TTS
    motor_tts: str = "kokoro"                     # kokoro | say
    respaldo_tts: str = "say"                     # say | ninguno
    voz_kokoro: str = "em_alex"
    idioma_kokoro: str = "es-419"
    velocidad_kokoro: float = 1.0
    archivo_kokoro: str = "kokoro/kokoro-v1.0.onnx"
    archivo_voces: str = "kokoro/voices-v1.0.bin"
    voz_say: str = "Paulina"
    velocidad_say: int = 185
    # Comunes
    dir_modelos: Path = RAIZ / "modelos"
    ffmpeg: str = "ffmpeg"
    sample_rate_stt: int = 16000
    max_segundos_audio: float = 30.0
    umbral_silencio_rms: float = 0.003
    timeout_stt_s: float = 15.0
    timeout_tts_s: float = 8.0
    modo_habla: str = "primeras_oraciones"        # primeras_oraciones | resumen
    max_oraciones_habladas: int = 3
    max_palabras_resumen: int = 45
    min_caracteres_oracion: int = 8
    max_caracteres_oracion: int = 220

    @property
    def ruta_kokoro(self) -> Path:
        return self._resolver(self.archivo_kokoro)

    @property
    def ruta_voces(self) -> Path:
        return self._resolver(self.archivo_voces)

    def _resolver(self, archivo: str) -> Path:
        p = Path(archivo)
        return p if p.is_absolute() else self.dir_modelos / p


def _sin_nulos(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def desde_config(cfg: dict[str, Any] | None) -> OpcionesVoz:
    """Construye OpcionesVoz a partir del diccionario completo de config.yaml."""
    cfg = cfg or {}
    stt = (cfg.get("modelos") or {}).get("stt") or {}
    tts = (cfg.get("modelos") or {}).get("tts") or {}
    voz = cfg.get("voz") or {}
    dir_modelos = Path((cfg.get("rutas") or {}).get("modelos_voz") or "modelos")
    if not dir_modelos.is_absolute():
        dir_modelos = RAIZ / dir_modelos

    valores = _sin_nulos({
        "motor_stt": stt.get("motor"),
        "modelo_mlx": stt.get("nombre"),
        "modelo_faster": stt.get("nombre_faster"),
        "idioma": stt.get("idioma"),
        "prompt_inicial": stt.get("prompt_inicial"),
        "dispositivo_faster": stt.get("dispositivo_faster"),
        "compute_type_faster": stt.get("compute_type_faster"),
        "motor_tts": tts.get("motor"),
        "respaldo_tts": tts.get("respaldo"),
        "voz_kokoro": tts.get("voz_kokoro"),
        "idioma_kokoro": tts.get("idioma_kokoro"),
        "velocidad_kokoro": tts.get("velocidad_kokoro"),
        "archivo_kokoro": tts.get("archivo_kokoro"),
        "archivo_voces": tts.get("archivo_voces"),
        "voz_say": tts.get("voz_say"),
        "velocidad_say": tts.get("velocidad_say"),
        "ffmpeg": voz.get("ffmpeg"),
        "max_segundos_audio": voz.get("max_segundos_audio"),
        "umbral_silencio_rms": voz.get("umbral_silencio_rms"),
        "timeout_stt_s": voz.get("timeout_stt_s"),
        "timeout_tts_s": voz.get("timeout_tts_s"),
        "modo_habla": voz.get("modo_habla"),
        "max_oraciones_habladas": voz.get("max_oraciones_habladas"),
        "max_palabras_resumen": voz.get("max_palabras_resumen"),
        "min_caracteres_oracion": voz.get("min_caracteres_oracion"),
        "max_caracteres_oracion": voz.get("max_caracteres_oracion"),
    })
    return OpcionesVoz(dir_modelos=dir_modelos, **valores)
