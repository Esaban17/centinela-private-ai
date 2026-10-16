"""Enmascarado de datos sensibles para logs y auditoría."""

from __future__ import annotations

import re
from typing import Any

# Cuentas del banco ficticio: AT-<5 dígitos>-<2 dígitos>
_CUENTA = re.compile(r"\bAT-(\d{5})-(\d{2})\b")


def enmascarar_cuenta(cuenta: str) -> str:
    """AT-00042-01 → AT-•••42-01"""
    m = _CUENTA.fullmatch(cuenta or "")
    if not m:
        return cuenta
    return f"AT-•••{m.group(1)[-2:]}-{m.group(2)}"


def enmascarar_texto(texto: str) -> str:
    return _CUENTA.sub(lambda m: enmascarar_cuenta(m.group(0)), texto)


def enmascarar(valor: Any) -> Any:
    """Enmascara recursivamente cadenas dentro de dicts/listas."""
    if isinstance(valor, str):
        return enmascarar_texto(valor)
    if isinstance(valor, dict):
        return {k: enmascarar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [enmascarar(v) for v in valor]
    return valor
