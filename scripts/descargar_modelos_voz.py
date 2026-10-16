"""Descarga los modelos de voz a `rutas.modelos_voz` (por defecto ./modelos). Requiere internet, UNA vez.

    python -m scripts.descargar_modelos_voz            # Kokoro + Whisper MLX (en Mac Apple Silicon)
    python -m scripts.descargar_modelos_voz --faster   # además faster-whisper (Linux / pruebas)
    python -m scripts.descargar_modelos_voz --int8     # además la variante int8 de Kokoro (88 MB, más rápida)

Después de esto, en tiempo de ejecución no se descarga nada (HF_HUB_OFFLINE=1 y local_files_only).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.request
from pathlib import Path

from comun import config
from orchestrator.voz_local.opciones import desde_config
from orchestrator.voz_local.stt import es_apple_silicon

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_ARCHIVOS = {"kokoro-v1.0.onnx": 300_000_000, "voices-v1.0.bin": 25_000_000}
KOKORO_INT8 = {"kokoro-v1.0.int8.onnx": 80_000_000}


def _descargar(url: str, destino: Path, tam_minimo: int) -> None:
    if destino.exists() and destino.stat().st_size >= tam_minimo:
        print(f"  ya existe {destino}")
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".parcial")
    print(f"  descargando {url}")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    if tmp.stat().st_size < tam_minimo:
        tmp.unlink()
        sys.exit(f"  descarga incompleta: {destino.name}")
    tmp.rename(destino)


def kokoro(dir_kokoro: Path, int8: bool) -> None:
    print("Kokoro (TTS):")
    archivos = dict(KOKORO_ARCHIVOS, **(KOKORO_INT8 if int8 else {}))
    for nombre, tam in archivos.items():
        _descargar(KOKORO_BASE + nombre, dir_kokoro / nombre, tam)


def whisper_mlx(dir_modelos: Path, repo: str) -> None:
    from huggingface_hub import snapshot_download

    destino = dir_modelos / repo.split("/")[-1]
    print(f"Whisper MLX (STT): {repo} → {destino}")
    snapshot_download(repo, local_dir=str(destino))


def whisper_faster(dir_modelos: Path, modelo: str) -> None:
    from faster_whisper import WhisperModel

    raiz = dir_modelos / "faster-whisper"
    print(f"faster-whisper (STT): {modelo} → {raiz}")
    WhisperModel(modelo, device="cpu", compute_type="int8", download_root=str(raiz))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--faster", action="store_true", help="descargar también el modelo de faster-whisper")
    ap.add_argument("--modelo-faster", help="p. ej. small o tiny para pruebas rápidas (por defecto el de config)")
    ap.add_argument("--int8", action="store_true", help="descargar también Kokoro int8")
    ap.add_argument("--sin-mlx", action="store_true", help="no descargar Whisper MLX")
    args = ap.parse_args()

    # Este script es el único que tiene permiso de usar la red.
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ.pop(var, None)

    op = desde_config(config.cargar())
    kokoro(op.ruta_kokoro.parent, args.int8)
    if es_apple_silicon() and not args.sin_mlx:
        whisper_mlx(op.dir_modelos, op.modelo_mlx)
    elif not args.sin_mlx:
        print("Whisper MLX: se omite (no es Mac con Apple Silicon).")
    if args.faster or not es_apple_silicon():
        whisper_faster(op.dir_modelos, args.modelo_faster or op.modelo_faster)
    print("Listo. Los modelos de voz quedaron en", op.dir_modelos)


if __name__ == "__main__":
    main()
