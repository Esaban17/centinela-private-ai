"""Logs legibles para la terminal secundaria, con los datos sensibles enmascarados."""

from __future__ import annotations

import logging
import os
import sys

from comun.mascaras import enmascarar_texto


class FiltroEnmascarado(logging.Filter):
    """Enmascara números de cuenta en cualquier mensaje de log, venga de donde venga."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = enmascarar_texto(record.getMessage())
        record.args = None
        return True


def configurar(nivel: str | None = None) -> None:
    manejador = logging.StreamHandler(sys.stderr)
    manejador.addFilter(FiltroEnmascarado())
    manejador.setFormatter(logging.Formatter("%(asctime)s %(name)-22s %(levelname)-7s %(message)s", "%H:%M:%S"))
    raiz = logging.getLogger()
    raiz.handlers[:] = [manejador]
    raiz.setLevel(nivel or os.environ.get("CENTINELA_LOG", "INFO"))
    for ruidoso in ("httpx", "httpcore", "mcp", "uvicorn.access"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
