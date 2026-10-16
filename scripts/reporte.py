"""Líneas de chequeo (✅ / ⚠️ / ❌) que imprimen `make warmup` y `make check-offline`."""

from __future__ import annotations

from dataclasses import dataclass

ICONO = {"ok": "✅", "aviso": "⚠️ ", "falla": "❌"}


@dataclass
class Chequeo:
    nombre: str
    estado: str  # ok | aviso | falla
    detalle: str = ""

    def __str__(self) -> str:
        return f"{ICONO[self.estado]} {self.nombre}" + (f" — {self.detalle}" if self.detalle else "")


def fase(titulo: str, chequeos: list[Chequeo], acumulado: list[Chequeo]) -> None:
    print(f"\n▸ {titulo}")
    for c in chequeos:
        print("  ", c)
    acumulado.extend(chequeos)


def cierre(chequeos: list[Chequeo], listo: str, no_listo: str) -> int:
    fallas = sum(c.estado == "falla" for c in chequeos)
    avisos = sum(c.estado == "aviso" for c in chequeos)
    print(f"\n{'❌ ' + no_listo if fallas else '✅ ' + listo} · {fallas} fallas · {avisos} avisos")
    return 1 if fallas else 0
