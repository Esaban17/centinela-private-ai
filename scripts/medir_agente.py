"""Compara candidatos a modelo agente con las preguntas del guion (`make medir`).

    python -m scripts.medir_agente --modelos qwen3.6:35b-a3b-q4_K_M gemma4:26b-a4b-it-qat --corridas 5

Es `scripts/ensayo.py` con otra puerta de entrada: mismas preguntas, misma copia de la base, mismos criterios.
"""

from __future__ import annotations

import argparse

from scripts import ensayo


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--modelos", nargs="*", help="etiquetas de Ollama (por defecto, el agente de config.yaml)")
    p.add_argument("--corridas", type=int, default=3)
    args = p.parse_args()
    ensayo.main(["--corridas", str(args.corridas)] + (["--modelos", *args.modelos] if args.modelos else []))


if __name__ == "__main__":
    main()
