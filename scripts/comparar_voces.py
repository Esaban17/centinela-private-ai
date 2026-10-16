"""Escuchar y comparar voces de Kokoro y de `say` con frases del guion. Se corre en la Mac de la demo.

    python -m scripts.comparar_voces                    # genera los WAV y una página para escucharlos
    python -m scripts.comparar_voces --reproducir       # además los reproduce uno tras otro con afplay
    python -m scripts.comparar_voces --frase "Buenas tardes, licenciado."

Genera salida/voces/ con un WAV por combinación y un index.html local (sin nada externo) para compararlas.
Kokoro solo trae voces con timbre de España; se prueba con el fonemizador `es` (España) y `es-419`
(Latinoamérica, seseo). Al terminar, fije la elegida en config.yaml (modelos.tts.voz_kokoro / idioma_kokoro).
"""

from __future__ import annotations

import argparse
import html
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

from comun import config
from comun.config import RAIZ
from orchestrator.voz_local.opciones import desde_config
from orchestrator.voz_local.texto import normalizar_para_voz
from orchestrator.voz_local.tts import KokoroTTS, SayTTS, TTSNoDisponible

FRASES = [
    "Buenas tardes. La cliente Lucía Fernanda Cojtí Barrios tiene tres cuentas activas y riesgo KYC medio.",
    "Encontré seis movimientos inusuales este mes: una transferencia de Q18,500.00 a las 02:14 a. m. desde Zacapa.",
    "Según la política de prevención de fraude, sección 4.2, corresponde un bloqueo preventivo de la tarjeta.",
    "Hay dos alertas críticas: el clúster VKS de producción y un NSX Edge sin redundancia.",
]
VOCES_KOKORO = ["ef_dora", "em_alex", "em_santa"]
IDIOMAS_KOKORO = ["es-419", "es"]
VOCES_SAY_PREFERIDAS = ["Paulina", "Juan", "Mónica", "Jorge", "Paulina (Enhanced)", "Paulina (Premium)"]


def voces_say_es() -> list[str]:
    """Voces en español instaladas en macOS (`say -v ?`)."""
    if platform.system() != "Darwin":
        return []
    salida = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=False).stdout
    voces = []
    for linea in salida.splitlines():
        m = re.match(r"^(.+?)\s{2,}(es_[A-Z]{2})\s", linea)
        if m:
            voces.append(m.group(1).strip())
    preferidas = [v for v in VOCES_SAY_PREFERIDAS if v in voces]
    return preferidas + [v for v in voces if v not in preferidas]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frase", action="append", help="frase propia (se puede repetir)")
    ap.add_argument("--velocidad", type=float, default=None, help="velocidad de Kokoro (1.0 normal)")
    ap.add_argument("--reproducir", action="store_true", help="reproducir con afplay al terminar (macOS)")
    ap.add_argument("--salida", default=str(RAIZ / "salida" / "voces"))
    args = ap.parse_args()

    op = desde_config(config.cargar())
    frases = args.frase or FRASES
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    texto = " ".join(normalizar_para_voz(f) for f in frases)
    print("Texto normalizado que se leerá:\n ", texto, "\n")

    motores = [KokoroTTS(op, voz=v, idioma=i, velocidad=args.velocidad) for v in VOCES_KOKORO for i in IDIOMAS_KOKORO]
    motores += [SayTTS(op, voz=v) for v in voces_say_es()]
    if not SayTTS.disponible():
        print("(say no existe en esta máquina: solo se prueban voces de Kokoro)\n")

    filas = []
    for m in motores:
        etiqueta = f"{m.nombre}-{m.voz}" + (f"-{m.idioma}" if isinstance(m, KokoroTTS) else "")
        archivo = salida / (re.sub(r"[^\w.-]+", "_", etiqueta) + ".wav")
        try:
            t0 = time.perf_counter()
            audio = m.sintetizar(texto)
            archivo.write_bytes(audio.wav)
            seg = time.perf_counter() - t0
            rtf = seg / (audio.duracion_ms / 1000)
            filas.append((etiqueta, archivo.name, f"{seg:.2f} s", f"{rtf:.2f}", ""))
            print(f"  {etiqueta:<34} {seg:6.2f} s  RTF {rtf:.2f}  → {archivo.name}")
        except (TTSNoDisponible, Exception) as e:
            filas.append((etiqueta, "", "", "", str(e)))
            print(f"  {etiqueta:<34} ERROR: {e}")

    pagina = ["<!doctype html><meta charset='utf-8'><title>Voces Centinela</title>",
              "<style>body{font:18px system-ui;background:#111;color:#eee;margin:2rem}td{padding:.4rem 1rem}"
              "audio{width:22rem}</style><h1>Comparar voces — DEMO / DATOS FICTICIOS</h1>",
              f"<p>{html.escape(texto)}</p><p>RTF &lt; 1 = sintetiza más rápido que el tiempo real.</p><table>",
              "<tr><th>Voz</th><th>Audio</th><th>Síntesis</th><th>RTF</th><th></th></tr>"]
    for etiqueta, wav, seg, rtf, err in filas:
        celda = f"<audio controls src='{wav}'></audio>" if wav else ""
        pagina.append(f"<tr><td>{html.escape(etiqueta)}</td><td>{celda}</td><td>{seg}</td><td>{rtf}</td>"
                      f"<td>{html.escape(err)}</td></tr>")
    pagina.append("</table>")
    (salida / "index.html").write_text("\n".join(pagina), encoding="utf-8")
    print(f"\nAbra {salida / 'index.html'} en el navegador para comparar.")

    if args.reproducir and shutil.which("afplay"):
        for etiqueta, wav, *_ in filas:
            if wav:
                print("▶", etiqueta)
                subprocess.run(["afplay", str(salida / wav)], check=False)


if __name__ == "__main__":
    main()
