"""Carga de configuración: config.yaml + variables de entorno.

Cualquier clave se sobreescribe con una variable de entorno con el formato
CENTINELA__<seccion>__<clave>[__<subclave>...], por ejemplo:

    CENTINELA__rutas__db=/tmp/banco.db
    CENTINELA__modelos__agente__nombre=gemma4:26b-a4b-it-qat

Los valores de entorno se interpretan como YAML (números, booleanos, null).

Orden: config.yaml → config.local.yaml (si existe; lo escribe la UI al elegir modelos, no se versiona) →
variables de entorno → perfil de modelos según la RAM (`comun.modelos`) → variables de entorno otra vez, para que
`CENTINELA__modelos__...` siga ganándole al perfil. `CENTINELA_CONFIG_LOCAL` cambia la ruta del archivo local
("" lo apaga).
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


def _fusionar(base: dict[str, Any], encima: dict[str, Any]) -> None:
    for clave, valor in encima.items():
        if isinstance(valor, dict) and isinstance(base.get(clave), dict):
            _fusionar(base[clave], valor)
        else:
            base[clave] = valor


def ruta_local() -> Path | None:
    valor = os.environ.get("CENTINELA_CONFIG_LOCAL")
    if valor is None:
        return RAIZ / "config.local.yaml"
    return Path(valor) if valor.strip() else None


@lru_cache(maxsize=1)
def cargar() -> dict[str, Any]:
    _cargar_dotenv(RAIZ / ".env")
    ruta = Path(os.environ.get("CENTINELA_CONFIG", RAIZ / "config.yaml"))
    cfg = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    local = ruta_local()
    if local is not None and local.exists():
        _fusionar(cfg, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    _aplicar_entorno(cfg)
    if cfg.get("seleccion_modelos"):
        from comun import modelos

        modelos.aplicar_perfil(cfg)
        _aplicar_entorno(cfg)
    return cfg


def guardar_local(cambios: dict[str, Any]) -> Path:
    """Fusiona `cambios` en config.local.yaml (lo crea si no existe). Rige desde el próximo arranque."""
    local = ruta_local() or RAIZ / "config.local.yaml"
    actual = (yaml.safe_load(local.read_text(encoding="utf-8")) or {}) if local.exists() else {}
    _fusionar(actual, cambios)
    encabezado = ("# Escrito por Centinela (botón «Modelos» de la UI). Pisa a config.yaml; bórrelo para volver a él.\n")
    local.write_text(encabezado + yaml.safe_dump(actual, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return local


def ruta(clave: str, cfg: dict[str, Any] | None = None) -> Path:
    """Devuelve una ruta de la sección `rutas` (de `cfg` o de la configuración cargada), resuelta respecto a
    la raíz del repo."""
    p = Path((cfg or cargar())["rutas"][clave])
    return p if p.is_absolute() else RAIZ / p
