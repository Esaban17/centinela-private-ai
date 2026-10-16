"""Carga de configuración: config.yaml + variables de entorno.

Cualquier clave se sobreescribe con una variable de entorno con el formato
CENTINELA__<seccion>__<clave>[__<subclave>...], por ejemplo:

    CENTINELA__rutas__db=/tmp/banco.db
    CENTINELA__modelos__agente__nombre=gemma4:26b-a4b-it-qat

Los valores de entorno se interpretan como YAML (números, booleanos, null).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parent.parent
PREFIJO = "CENTINELA__"


def _cargar_dotenv(ruta: Path) -> None:
    """Carga un .env simple (CLAVE=valor) sin pisar variables ya definidas."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def _aplicar_entorno(cfg: dict[str, Any]) -> None:
    for var, valor in os.environ.items():
        if not var.startswith(PREFIJO):
            continue
        partes = var[len(PREFIJO):].split("__")
        nodo = cfg
        for parte in partes[:-1]:
            nodo = nodo.setdefault(parte, {})
        nodo[partes[-1]] = yaml.safe_load(valor) if valor != "" else ""


@lru_cache(maxsize=1)
def cargar() -> dict[str, Any]:
    _cargar_dotenv(RAIZ / ".env")
    ruta = Path(os.environ.get("CENTINELA_CONFIG", RAIZ / "config.yaml"))
    cfg = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    _aplicar_entorno(cfg)
    return cfg


def ruta(clave: str) -> Path:
    """Devuelve una ruta de la sección `rutas`, resuelta respecto a la raíz del repo."""
    p = Path(cargar()["rutas"][clave])
    return p if p.is_absolute() else RAIZ / p
