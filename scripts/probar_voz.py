"""Prueba de humo de la voz, sin micrófono ni orquestador.

    python -m scripts.probar_voz                       # TTS de una frase → STT de ese audio, con latencias
    python -m scripts.probar_voz --audio grabacion.webm   # transcribir un archivo propio
    python -m scripts.probar_voz --streaming           # simula el LLM token a token y mide el primer audio

Imprime qué motor respondió (Kokoro o respaldo `say`, mlx-whisper o faster-whisper) y cuánto tardó cada etapa.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from comun import config
from comun.config import RAIZ
from orchestrator import voz

FRASE = ("Buenas tardes. Encontré seis movimientos inusuales en las cuentas de la cliente, entre ellos una "
         "transferencia de Q18,500.00 a las 02:14 a. m. ¿Desea que abra un ticket para el equipo de fraude?")


async def ida_y_vuelta(cfg: dict, salida: Path) -> None:
    tts = voz.crear_sintetizador(cfg)
    t0 = time.perf_counter()
    audio = await tts.sintetizar(FRASE)
    print(f"TTS  {tts.ultimo.motor:<6} voz={tts.ultimo.voz:<9} {time.perf_counter() - t0:5.2f} s  "
          f"(audio de {audio.duracion_ms / 1000 if hasattr(audio, 'duracion_ms') else 0:.1f} s"
          f"{', RESPALDO' if tts.ultimo.respaldo else ''})")
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "prueba_tts.wav").write_bytes(audio.datos)
    print("     texto leído:", tts.ultimo.texto)
    await transcribir(cfg, audio.datos, "wav")


async def transcribir(cfg: dict, datos: bytes, formato: str) -> None:
    stt = voz.crear_transcriptor(cfg)
    t0 = time.perf_counter()
    await stt.precalentar()
    print(f"STT  carga/precalentado {time.perf_counter() - t0:5.2f} s ({stt.motor}, {stt.modelo})")
    t0 = time.perf_counter()
    r = await stt.transcribir(datos, formato)
    print(f"STT  {time.perf_counter() - t0:5.2f} s para {stt.ultimo.duracion_audio_s:.1f} s de audio")
    print("     transcripción:", r.texto or "(silencio)")


async def streaming(cfg: dict) -> None:
    tts = voz.crear_sintetizador(cfg)
    await tts.precalentar()
    locutor = tts.locutor(max_oraciones=0)

    async def llm_simulado():
        for palabra in FRASE.split(" "):
            locutor.alimentar(palabra + " ")
            await asyncio.sleep(0.03)          # ~30 tokens/s
        locutor.terminar()

    tarea = asyncio.create_task(llm_simulado())
    async for f in locutor.fragmentos():
        print(f"  [{f.desde_inicio_ms:5d} ms] oración {f.indice} ({f.motor}, {f.latencia_ms} ms, "
              f"{f.duracion_ms} ms de audio): {f.texto}")
    await tarea
    print(f"Primer audio listo a los {locutor.primer_audio_ms} ms de empezar a llegar el texto.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", type=Path, help="archivo de audio a transcribir (webm, wav, m4a…)")
    ap.add_argument("--streaming", action="store_true")
    args = ap.parse_args()
    cfg = config.cargar()
    if args.audio:
        asyncio.run(transcribir(cfg, args.audio.read_bytes(), args.audio.suffix.lstrip(".")))
    elif args.streaming:
        asyncio.run(streaming(cfg))
    else:
        asyncio.run(ida_y_vuelta(cfg, RAIZ / "salida"))


if __name__ == "__main__":
    main()
