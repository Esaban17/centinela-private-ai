"""Logs legibles para la terminal secundaria, con los datos sensibles enmascarados."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from comun.mascaras import enmascarar_texto


class FiltroEnmascarado(logging.Filter):
    """Enmascara números de cuenta en cualquier mensaje de log, venga de donde venga."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = enmascarar_texto(record.getMessage())
        record.args = None
        return True


def configurar(nivel: str | None = None, archivo: str | Path | None = None) -> None:
    """Log en la terminal y, si se indica `archivo`, una copia para seguirla con `make logs`."""
    formato = logging.Formatter("%(asctime)s %(name)-22s %(levelname)-7s %(message)s", "%H:%M:%S")
    manejadores: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if archivo:
        ruta = Path(archivo)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        manejadores.append(logging.FileHandler(ruta, encoding="utf-8"))
    for m in manejadores:
        m.addFilter(FiltroEnmascarado())
        m.setFormatter(formato)
    raiz = logging.getLogger()
    raiz.handlers[:] = manejadores
    raiz.setLevel(nivel or os.environ.get("CENTINELA_LOG", "INFO"))
    for ruidoso in ("httpx", "httpcore", "mcp", "uvicorn.access"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
